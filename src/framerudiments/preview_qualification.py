"""S7 bounded, content-free observations for one preview session.

The stream condition owns this object. Timings use the existing monotonic
clock; source/audio time is never derived here. A receipt records observations,
not production, physical-display, GPU or release approval.
"""
from collections import deque
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from math import ceil, isfinite
from pathlib import Path
import os
import re
import sys
from time import monotonic, process_time

SCHEMA = 'framerudiments.preview-stream-qualification.v1'
MAX_SAMPLES = 512
MAX_HISTORY = 128
WINDOW_SECONDS = 3.0
_SAFE_INTEGER = 2**53 - 1
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')
_PROFILE = re.compile(r'Q[0-5]\Z')
_VERSIONS = re.compile(r'[0-9][0-9A-Za-z.+_-]{0,63}\Z')
_REASONS = frozenset(('p95_over_budget', 'persistent_backlog', 'catastrophic_service_time',
                     'sustained_headroom', 'allocation_failure', 'target_changed'))
_CAUSES = frozenset(('initial', 'unchanged', 'boundaries_changed', 'quality_or_renderer_changed',
                    'shared_pipeline_changed', 'section_dependencies_changed', 'source_changed',
                    'stateful_revoked', 'unknown'))


def _count(value, default=0):
    return min(value, _SAFE_INTEGER) if type(value) is int and value >= 0 else default


def _digest(value):
    return value if isinstance(value, str) and _DIGEST.fullmatch(value) else None


def _percentile(values, fraction):
    return round(sorted(values)[ceil(len(values) * fraction) - 1], 3) if values else None


def runtime_versions():
    """Allowlisted package versions; metadata paths and arbitrary text stay local."""
    result = {'python': '.'.join(map(str, sys.version_info[:3]))}
    for name in ('framerudiments', 'dancerudiments', 'pillow'):
        try:
            found = version(name)
        except PackageNotFoundError:
            found = None
        result[name] = found if isinstance(found, str) and _VERSIONS.fullmatch(found) else None
    return result


def current_rss_bytes():
    """Current resident memory of this server process, or unavailable.

    No child-process/GPU/host-wide measurement is inferred. In particular,
    getrusage's historical maximum is not relabelled as current RSS.
    """
    try:
        if sys.platform.startswith('linux'):
            fields = Path('/proc/self/statm').read_text(encoding='ascii').split()
            return int(fields[1]) * os.sysconf('SC_PAGE_SIZE')
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                        'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                        'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetCurrentProcess.argtypes = []
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            api = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
            api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            api.restype = wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    return None


class StreamQualification:
    """Bounded session counters, timing windows and auditable identities.

    Cache hits/misses count accepted foreground requests. Cache.get() calls for
    PNG delivery or background inspection do not inflate that hit rate. The
    lightweight status view caches storage/resource probes for one second.
    """
    def __init__(self, cache, frame_count, target_fps, *, clock=monotonic,
                 identity=None, acknowledgement_source='client_decode_ack'):
        if acknowledgement_source not in ('client_decode_ack', 'harness_png_decode'):
            raise ValueError('unsupported acknowledgement source')
        self.cache, self.frame_count, self.clock = cache, frame_count, clock
        self.target_fps = target_fps
        self.acknowledgement_source = acknowledgement_source
        self.started, self.cpu_started = clock(), process_time()
        self.sample_sequence = 0
        self.timings = {key: deque(maxlen=MAX_SAMPLES) for key in (
            'render', 'queue', 'seek_ack', 'seek_first_frame', 'seek_decode')}
        self.times = {key: deque(maxlen=MAX_SAMPLES) for key in ('fresh', 'decoded')}
        self.rate_samples = {key: deque(maxlen=MAX_SAMPLES) for key in self.times}
        self.last_rate_sample = self.started
        self.transitions, self.invalidations = deque(maxlen=MAX_HISTORY), deque(maxlen=MAX_HISTORY)
        self.history_dropped = {'transitions': 0, 'invalidations': 0}
        self.counters = dict.fromkeys(('commands', 'coalesced_requests', 'stale_results', 'render_errors',
            'quota_rejections', 'foreground_completed', 'background_completed',
            'decoded_acknowledgements', 'repeated_acknowledgements'), 0)
        self.lookup_hits = self.lookup_misses = 0
        self.request_sequence = self.request_frame = None
        self.seek_started = None
        self.decode_seek_started = None
        self.baseline_stats = cache.stats()
        self._storage_at, self._resource_at = self.started, self.started
        self._storage = self._cache_view(self.baseline_stats)
        self._resources = self._resource_view()
        raw = identity or {}
        self.identity = {
            'project_sha256': _digest(raw.get('project_sha256')) or _digest(cache.project_hash),
            'pipeline_sha256': _digest(raw.get('pipeline_sha256')) or _digest(cache.pipeline_hash),
            'renderer_abi_sha256': sha256(cache.renderer_abi.encode('utf-8')).hexdigest(),
            'profile_ladder_sha256': _digest(cache.profile_digest),
            'implementation_sha256': _digest(raw.get('implementation_sha256')),
        }
        self.versions = runtime_versions()

    def elapsed_ms(self):
        return round(max(0, self.clock() - self.started) * 1000, 3)

    def _timing(self, key, milliseconds):
        if type(milliseconds) not in (float, int) or not isfinite(milliseconds) or milliseconds < 0:
            raise ValueError('qualification timings must be finite nonnegative milliseconds')
        self.timings[key].append(float(milliseconds))

    def command(self, sequence, frame, name, *, received_at, coalesced=False, target_fps=None,
                profile='Q0'):
        self.counters['commands'] += 1
        self.counters['coalesced_requests'] += int(coalesced)
        self.request_sequence, self.request_frame = sequence, frame
        self.seek_started = received_at if name == 'seek' else None
        self.decode_seek_started = self.seek_started
        if target_fps is not None and target_fps != self.target_fps:
            self.transition(profile, profile, 'target_changed')
            self.target_fps = target_fps

    def command_ack(self, name, received_at):
        if name == 'seek':
            self._timing('seek_ack', max(0, self.clock() - received_at) * 1000)

    def lookup(self, hit):
        if hit:
            self.lookup_hits += 1
        else:
            self.lookup_misses += 1

    def ready(self, sequence, frame):
        if (self.seek_started is not None and sequence == self.request_sequence
                and frame == self.request_frame):
            self._timing('seek_first_frame', max(0, self.clock() - self.seek_started) * 1000)
            self.seek_started = None

    def rendered(self, *, render_ms, queue_ms, origin='foreground'):
        if origin == 'foreground':
            self._timing('render', render_ms)
            self._timing('queue', queue_ms)
            self.counters['foreground_completed'] += 1
            self.times['fresh'].append(self.clock())
        else:
            self.counters['background_completed'] += 1
        self._rates()

    def acknowledged(self, *, repeated):
        self.counters['decoded_acknowledgements'] += 1
        self.counters['repeated_acknowledgements'] += int(repeated)
        if not repeated:
            self.times['decoded'].append(self.clock())
        if self.decode_seek_started is not None:
            self._timing('seek_decode', max(0, self.clock() - self.decode_seek_started) * 1000)
            self.decode_seek_started = None
        self._rates()

    def _history(self, name, row):
        rows = getattr(self, name)
        if len(rows) == MAX_HISTORY:
            self.history_dropped[name] += 1
        rows.append(row)

    def transition(self, before, after, reason):
        if (not isinstance(before, str) or not _PROFILE.fullmatch(before)
                or not isinstance(after, str) or not _PROFILE.fullmatch(after)
                or reason not in _REASONS):
            return
        self._history('transitions', dict(at_ms=self.elapsed_ms(), from_profile=before,
                                        to_profile=after, reason=reason))

    def invalidated(self, receipt):
        if not isinstance(receipt, dict):
            return
        cause = receipt.get('cause')
        if not isinstance(cause, str):
            cause = 'unknown'
        cause = {'pinned_source_revoked': 'source_changed', 'nonvisual_revision': 'unchanged'}.get(cause, cause)
        affected = receipt.get('affected_sections')
        # A local cache index read, with no PNG decoding or filesystem walk.
        self._storage = self._cache_view(self.cache.stats())
        self._storage_at = self.clock()
        self._history('invalidations', dict(at_ms=self.elapsed_ms(),
            cause=cause if cause in _CAUSES else 'unknown',
            affected_sections=min(len(affected), 2048) if isinstance(affected, list) else 0,
            invalidated_frames=_count(receipt.get('invalidated_frames')),
            invalidated_bytes=_count(receipt.get('invalidated_bytes')),
            retained_frames=self._storage['frames'], revision=_count(receipt.get('revision'))))

    def event(self, kind, sequence, properties):
        if kind == 'frame-ready':
            self.ready(sequence, properties.get('frame'))
        elif kind == 'quality-changed':
            self.transition(properties.get('from_profile'), properties.get('to_profile'), properties.get('reason'))
        elif kind == 'cache-invalidated':
            self.invalidated(properties.get('invalidation'))
        elif kind in ('render-error', 'stateful-seek-unavailable'):
            self.counters['render_errors'] += 1
        elif kind == 'overload' and properties.get('reason') == 'quota_rejected':
            self.counters['quota_rejections'] += 1

    def _cache_view(self, stats):
        return dict(frames=_count(stats.get('frames')), q5_frames=_count(stats.get('q5_frames')),
            bytes=_count(stats.get('disk_bytes')), quota_bytes=_count(stats.get('max_bytes')),
            hits=self.lookup_hits, misses=self.lookup_misses,
            upgrades=max(0, _count(stats.get('upgrades')) - _count(self.baseline_stats.get('upgrades'))),
            evictions=max(0, _count(stats.get('evictions')) - _count(self.baseline_stats.get('evictions'))))

    def _resource_view(self):
        return dict(scope='preview_server_process', cpu_seconds=round(max(0, process_time() - self.cpu_started), 6),
                    rss_bytes=current_rss_bytes(), gpu_memory_bytes=None)

    def _rates(self):
        now = self.clock()
        elapsed = min(WINDOW_SECONDS, max(0, now - self.started))
        values = {}
        for key, rows in self.times.items():
            while rows and rows[0] < now - WINDOW_SECONDS:
                rows.popleft()
            # A saturated timestamp buffer cannot support an exact rate claim.
            values[key] = (round(len(rows) / elapsed, 3)
                           if elapsed >= .25 and len(rows) < MAX_SAMPLES else None)
        if now - self.last_rate_sample >= .25:
            self.last_rate_sample = now
            for key, value in values.items():
                if value is not None:
                    self.rate_samples[key].append(value)
        return values

    def snapshot(self, *, full=False, refresh=False):
        now = self.clock()
        if refresh or now - self._storage_at >= 1:
            self._storage = self._cache_view(self.cache.stats())
            self._storage_at = now
        if refresh or now - self._resource_at >= 1:
            self._resources = self._resource_view()
            self._resource_at = now
        values = self._rates()
        self.sample_sequence += 1
        result = dict(schema=SCHEMA, sample_sequence=self.sample_sequence, elapsed_ms=self.elapsed_ms(),
            target_fps=self.target_fps, frame_count=self.frame_count,
            rates=dict(fresh_fps=values['fresh'], decoded_fps=values['decoded'],
                acknowledgement_source=self.acknowledgement_source, window_seconds=WINDOW_SECONDS,
                fresh_fps_p50=_percentile(self.rate_samples['fresh'], .50),
                fresh_fps_p95=_percentile(self.rate_samples['fresh'], .95),
                decoded_fps_p50=_percentile(self.rate_samples['decoded'], .50),
                decoded_fps_p95=_percentile(self.rate_samples['decoded'], .95)),
            latency_ms={key: dict(p50=_percentile(rows, .50), p95=_percentile(rows, .95), samples=len(rows))
                        for key, rows in self.timings.items()},
            cache={**self._storage, 'hits': self.lookup_hits, 'misses': self.lookup_misses},
            resources=dict(self._resources), counters={key: _count(value) for key, value in self.counters.items()},
            bounds=dict(timing_samples=MAX_SAMPLES, history_events=MAX_HISTORY, sections=2048),
            measurement=dict(physical_display=False, production_approval=False,
                seek_ack_scope='server_command_processing', seek_first_frame_scope='server_frame_ready',
                seek_decode_scope='server_seek_to_decode_ack',
                render_scope='foreground_service_including_setup_and_cache_publish',
                rate_scope='rolling_3s_current_session',
                rate_percentile_scope='observations_at_least_250ms_apart',
                cache_scope='current_indexed_png_bytes', resource_scope='server_process_only'),
            release_gates=dict(exact_change_ci=False, native_engine_matrix=False, physical_gpu=False,
                               encoded_output=False, perceptual_approval=False))
        if full:
            result.update(identity=dict(self.identity), versions=dict(self.versions),
                          segments=self.cache.qualification_segments(),
                          transitions=[dict(row) for row in self.transitions],
                          invalidations=[dict(row) for row in self.invalidations],
                          history_dropped=dict(self.history_dropped))
        return result
