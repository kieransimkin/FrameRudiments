"""S0 contracts and bounded timing metrics for future seekable preview streaming.

Pure, allocation-free metadata only: no render, thread, queue, network listener,
GPU context, cache write, audio analysis, or project mutation is performed here.
Every section conservatively depends on the complete project until S5 supplies
a verified reverse-dependency graph. An S0 fingerprint is NOT cache admission.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from dataclasses import asdict, dataclass
from fractions import Fraction
from hashlib import sha256
import json
from math import ceil, isfinite
from threading import Lock

SCHEMA = 'framerudiments.preview-stream-contract.v1'
CLOCK_SCHEMA = 'framerudiments.preview-frame-clock.v1'
QUALITY_SCHEMA = 'framerudiments.preview-quality-ladder.v1'
TELEMETRY_SCHEMA = 'framerudiments.preview-telemetry.v1'
DEPENDENCY_SCOPE = 'whole_project_conservative'
MAX_SECTIONS = 2048
MAX_FRAME_COUNT = 10_000_000
MAX_TELEMETRY_SAMPLES = 512
_SEGMENT_SECONDS = 30
_PROFILES = (('Q0', Fraction(1, 4), 1, 1, 'minimum_declared_lod'),
             ('Q1', Fraction(1, 2), 1, 1, 'reduced_declared_lod'),
             ('Q2', Fraction(3, 4), 1, 1, 'conservative_declared_lod'),
             ('Q3', Fraction(1, 1), 1, 2, 'validated_preview_variants_only'),
             ('Q4', Fraction(1, 1), 2, 4, 'full_graph'),
             ('Q5', Fraction(1, 1), None, None, 'authored_production'))


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be an integer in [{minimum}, {maximum}]')
    return value


def _finite(value, label, *, minimum=0):
    if type(value) not in (int, float) or not isfinite(value) or value < minimum:
        raise ValueError(f'{label} must be a finite number >= {minimum}')
    return float(value)


def _hex(value, label):
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in '0123456789abcdefABCDEF' for c in value)):
        raise ValueError(f'{label} must be SHA-256 hexadecimal')
    return value.lower()


def canonical_digest(document):
    """Hash canonical JSON without serializing source text into receipts."""
    try:
        encoded = json.dumps(document, sort_keys=True, ensure_ascii=False,
                             allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('fingerprint input must be finite JSON data') from exc
    return sha256(encoded).hexdigest()


def project_fingerprint(project, *, renderer_abi):
    """Conservative complete-project identity for *all* sections (not a pixel proof).

    Hash the immutable project schema/data, authoritative audio window, renderer
    ABI, and already-validated input references. Absolute paths are only hashed;
    they never appear in the returned receipt. S5 will replace the global-only
    dependency scope with an explicit, audited reverse dependency index.
    """
    if not isinstance(renderer_abi, str) or not 1 <= len(renderer_abi) <= 128:
        raise ValueError('renderer_abi requires a stable nonempty implementation identity')
    if not isinstance(getattr(project, 'data', None), dict):
        raise ValueError('project requires validated project data')
    if project.data.get('schema') != 'framerudiments.project.v1':
        raise ValueError('project schema mismatch')
    pins = []
    for item in getattr(project, 'canonical_inputs', None) or getattr(project, 'inputs', ()):
        if not isinstance(item, dict):
            raise ValueError('input provenance must contain pinned reference objects')
        digest = _hex(item.get('sha256'), 'input reference')
        path = item.get('path')
        if not isinstance(path, str) or not path:
            raise ValueError('input provenance needs a path')
        size = item.get('bytes')
        if size is not None:
            _integer(size, 'input bytes', 0, 1 << 55)
        pins.append({'sha256': digest, 'path_sha256': sha256(path.encode('utf-8')).hexdigest(),
                     'bytes': size})
    pins.sort(key=lambda item: (item['path_sha256'], item['sha256']))
    window = {name: _integer(getattr(project, name), name, 0 if name == 'start_sample' else 1,
                             MAX_FRAME_COUNT if name == 'frame_count' else 10**13)
              for name in ('start_sample', 'end_sample', 'sample_rate', 'fps', 'repeats', 'frame_count')}
    if window['end_sample'] <= window['start_sample']:
        raise ValueError('invalid project sample window')
    return canonical_digest({'contract': SCHEMA, 'renderer_abi': renderer_abi,
                             'project': project.data, 'window': window, 'pins': pins})


@dataclass(frozen=True)
class FrameClock:
    """Source and beat timestamps delegated to the authoritative Project APIs."""
    frame: int
    output_time_numerator: int
    output_time_denominator: int
    source_seconds: float
    continuous_beat: float
    schema: str = CLOCK_SCHEMA


def frame_clock(project, frame):
    _integer(getattr(project, 'frame_count'), 'frame_count', 1, MAX_FRAME_COUNT)
    fps = _integer(getattr(project, 'fps'), 'fps', 1, 120)
    _integer(frame, 'absolute frame', 0, project.frame_count - 1)
    # Never integrate a new BPM map or reconstruct repeat-window source time.
    # Project.source_time() and continuous_beat() own those contracts.
    source = _finite(project.source_time(frame), 'authoritative source time')
    beat = project.continuous_beat(frame)
    if type(beat) not in (int, float) or not isfinite(beat):
        raise ValueError('authoritative beat must be finite')
    time = Fraction(frame, fps)
    return FrameClock(frame, time.numerator, time.denominator, source, float(beat))


@dataclass(frozen=True)
class Segment:
    id: str
    start_frame: int
    end_frame_exclusive: int
    dependency_fingerprint: str
    generation: int = 0
    dependency_scope: str = DEPENDENCY_SCOPE

    def __post_init__(self):
        if self.dependency_scope != DEPENDENCY_SCOPE:
            raise ValueError('S0 cannot claim segment-local dependency isolation')
        _integer(self.generation, 'segment generation', 0, 2**63 - 1)
        _hex(self.dependency_fingerprint, 'dependency fingerprint')
        if (not isinstance(self.id, str) or not self.id
                or type(self.start_frame) is not int or type(self.end_frame_exclusive) is not int
                or self.start_frame < 0 or self.end_frame_exclusive <= self.start_frame):
            raise ValueError('invalid half-open segment')


def section_segments(project, *, project_hash, fallback_seconds=_SEGMENT_SECONDS):
    """Deterministic cue boundaries, falling back to fixed-size sections.

    No new clock is introduced: cue entries are from Project.ledger() and have
    absolute frame ordinals. A moved boundary gives different segment IDs.
    """
    digest = _hex(project_hash, 'project fingerprint')
    fps = _integer(getattr(project, 'fps'), 'fps', 1, 120)
    total = _integer(getattr(project, 'frame_count'), 'frame_count', 1, MAX_FRAME_COUNT)
    interval = _integer(fallback_seconds, 'fallback_seconds', 1, 3600) * fps
    cues = getattr(project, 'cues', ())
    if not isinstance(cues, (tuple, list)):
        raise ValueError('section cues must be a native source ledger')
    if cues:
        boundaries = {0, total}
        for cue in cues:
            if not isinstance(cue, dict):
                raise ValueError('invalid cue ledger entry')
            n = cue.get('frame')
            if type(n) is int and 0 < n < total:
                boundaries.add(n)
        ordered = sorted(boundaries)
        if len(ordered) == 2:
            ordered = list(range(0, total, interval)) + [total]
    else:
        ordered = list(range(0, total, interval)) + [total]
    if len(ordered) - 1 > MAX_SECTIONS:
        raise ValueError('preview segment count exceeds allocation-free contract')
    return tuple(Segment(f'section-{start:08d}-{end:08d}', start, end, digest)
                 for start, end in zip(ordered, ordered[1:]))


def segment_for_frame(segments, frame):
    """Require complete, contiguous coverage rather than serving a wrong section."""
    if not segments:
        raise ValueError('no preview segments')
    starts = [s.start_frame for s in segments]
    if starts[0] != 0 or any(a.end_frame_exclusive != b.start_frame
                             for a, b in zip(segments, segments[1:])):
        raise ValueError('segments must be sorted and contiguous')
    _integer(frame, 'absolute frame', 0, segments[-1].end_frame_exclusive - 1)
    return segments[bisect_right(starts, frame) - 1]


@dataclass(frozen=True)
class QualityProfile:
    id: str
    quality_rank: int
    resolution_fraction: str
    output_width: int
    output_height: int
    spatial_scale: int
    temporal_samples: int
    effect_policy: str
    execution_status: str = 'not_implemented'
    production_equivalent: bool = False
    schema: str = QUALITY_SCHEMA


def quality_profiles(project):
    """Describe the ordered Q0..Q5 ladder; never silently change real effects.

    S0 is *descriptive*, not an executable preview profile: all variants stay
    unimplemented until S3 capability checks and M11 feedback eligibility.
    """
    width = _integer(getattr(project, 'width'), 'width', 1, 16384)
    height = _integer(getattr(project, 'height'), 'height', 1, 16384)
    quality = getattr(project, 'output_quality')
    active = getattr(quality, 'enabled', None)
    if type(active) is not bool:
        raise ValueError('project output quality must be validated')
    spatial = _integer(quality.spatial_scale if active else 1, 'native spatial', 1, 4)
    temporal = _integer(quality.temporal_samples if active else 1, 'native temporal', 1, 8)
    result = []
    for rank, (name, fraction, spatial_cap, temporal_cap, policy) in enumerate(_PROFILES):
        w = max(1, (width * fraction.numerator + fraction.denominator - 1) // fraction.denominator)
        h = max(1, (height * fraction.numerator + fraction.denominator - 1) // fraction.denominator)
        result.append(QualityProfile(name, rank, str(fraction), w, h,
                                     min(spatial, spatial_cap) if spatial_cap is not None else spatial,
                                     min(temporal, temporal_cap) if temporal_cap is not None else temporal,
                                     policy))
    return tuple(result)


def requested_target_fps(project, target_fps=None, *, preview_ceiling=60):
    """Default to the encoded FPS capped by preview ceiling, allow 15..120 explicit."""
    ceiling = _integer(preview_ceiling, 'preview ceiling', 15, 120)
    if target_fps is None:
        return min(_integer(project.fps, 'project FPS', 1, 120), ceiling)
    return _integer(target_fps, 'target FPS', 15, 120)


def frame_cache_identity(project_hash, segment, frame, profile, *, renderer_abi):
    """S1 cache ticket identity; not a pixel or production-quality proof."""
    _hex(project_hash, 'project fingerprint')
    if not isinstance(segment, Segment) or segment.dependency_fingerprint != project_hash:
        raise ValueError('frame identity requires matching section dependencies')
    _integer(frame, 'frame', segment.start_frame, segment.end_frame_exclusive - 1)
    if not isinstance(profile, QualityProfile) or profile.execution_status != 'not_implemented':
        raise ValueError('unqualified quality profile identity')
    if not isinstance(renderer_abi, str) or not renderer_abi:
        raise ValueError('missing renderer ABI')
    return canonical_digest({'schema': SCHEMA, 'project': project_hash, 'segment': segment.id,
                             'generation': segment.generation, 'frame': frame,
                             'quality_rank': profile.quality_rank, 'profile': asdict(profile),
                             'renderer_abi': renderer_abi})


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, ceil(len(ordered) * fraction) - 1)], 3)


class PreviewTelemetry:
    """Thread-safe, bounded, content-free one-shot/stream timing measurements.

    A completed *fresh render*, a hit and a presented picture are distinct. No
    FPS success is asserted here; S3 will divide fresh frames by elapsed time.
    Missing presentation timestamps are reported as unavailable, not zero.
    """

    def __init__(self, *, window=120):
        _integer(window, 'telemetry window', 8, MAX_TELEMETRY_SAMPLES)
        self._lock = Lock()
        self._render_ms = deque(maxlen=window)
        self._queue_ms = deque(maxlen=window)
        self._late_ms = deque(maxlen=window)
        self._rendered = 0
        self._hits = 0
        self._presented = 0
        self._repeated = 0
        self._rejected = 0
        self._errors = 0
        self._stale = 0
        self._generation = 0

    def new_generation(self, generation):
        _integer(generation, 'session generation', 0, 2**63 - 1)
        with self._lock:
            if generation <= self._generation:
                raise ValueError('session generations must increase')
            self._generation = generation
            self._render_ms.clear()
            self._queue_ms.clear()
            self._late_ms.clear()

    def record_render(self, *, render_ms, queue_ms=0, presentation_lag_ms=None, generation=None):
        render = _finite(render_ms, 'render milliseconds')
        queue = _finite(queue_ms, 'queue milliseconds')
        late = None if presentation_lag_ms is None else _finite(
            presentation_lag_ms, 'presentation lag milliseconds')
        if generation is not None:
            _integer(generation, 'session generation', 0, 2**63 - 1)
        with self._lock:
            if generation is not None and generation != self._generation:
                self._stale += 1
                return False
            self._render_ms.append(render)
            self._queue_ms.append(queue)
            if late is not None:
                self._late_ms.append(late)
            self._rendered += 1
            return True

    def record_hit(self):
        with self._lock:
            self._hits += 1

    def record_presentation(self, *, repeated=False):
        if type(repeated) is not bool:
            raise ValueError('repeated must be boolean')
        with self._lock:
            self._presented += 1
            if repeated:
                self._repeated += 1

    def record_rejection(self, *, failure=False):
        if type(failure) is not bool:
            raise ValueError('failure must be boolean')
        with self._lock:
            self._errors += int(failure)
            self._rejected += int(not failure)

    def snapshot(self):
        with self._lock:
            # Explicitly no source path, lyric, frame data or achievement claim.
            return {'schema': TELEMETRY_SCHEMA, 'scope': 'local_preview_only',
                    'generation': self._generation, 'rendered_fresh': self._rendered,
                    'cache_hits': self._hits, 'presented': self._presented,
                    'presented_duplicates': self._repeated, 'queue_rejected': self._rejected,
                    'render_errors': self._errors, 'stale_discarded': self._stale,
                    'render_samples': len(self._render_ms),
                    'render_ms_p50': _percentile(self._render_ms, .50),
                    'render_ms_p95': _percentile(self._render_ms, .95),
                    'queue_ms_p50': _percentile(self._queue_ms, .50),
                    'queue_ms_p95': _percentile(self._queue_ms, .95),
                    'presentation_lag_ms_p95': _percentile(self._late_ms, .95),
                    'fresh_render_fps': None, 'presented_fps': None,
                    'cache_active': False, 'adaptive_rendering_active': False}
