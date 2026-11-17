"""S0: deterministic stateless contracts; no OpenGL, codec, file cache or new service."""

from dataclasses import replace
from fractions import Fraction
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from framerudiments.preview_stream_contract import (
    DEPENDENCY_SCOPE, PreviewTelemetry, Segment, canonical_digest,
    frame_cache_identity, frame_clock, project_fingerprint, quality_profiles,
    requested_target_fps, section_segments, segment_for_frame,
)


class ProjectFixture:
    """Project API fixture with source repeat and a variable beat map.

    The adapter must delegate to these methods; it is not allowed to rebuild
    the source/BPM calculation in its own representation.
    """

    def __init__(self, *, fps=60, cues=None):
        self.fps = fps
        self.start_sample = 48000
        self.end_sample = 144000
        self.sample_rate = 48000
        self.repeats = 2
        self.frame_count = fps * 4
        self.width, self.height = 1920, 1080
        self.output_quality = SimpleNamespace(enabled=True, spatial_scale=4, temporal_samples=8)
        self.cues = [] if cues is None else cues
        self.data = {
            'schema': 'framerudiments.project.v1',
            'clock': {'authority': 'native', 'beat_timeline': {'sha256': 'b' * 64}},
            'audio': {'sample_rate': 48000, 'start_sample': 48000, 'end_sample': 144000,
                      'repeats': 2},
            'effect_graph': {'nodes': [{'id': 'a', 'type': 'source'}], 'output': 'a'},
        }
        self.inputs = [{'path': '/private/recording.wav', 'sha256': 'a' * 64, 'bytes': 1000},
                       {'path': '/private/beat-grid.json', 'sha256': 'b' * 64, 'bytes': 500}]
        self.canonical_inputs = None
        self.calls = []

    def source_time(self, frame):
        self.calls.append(('source', frame))
        elapsed = Fraction(frame, self.fps)
        return float(1 + (elapsed % 2))

    def continuous_beat(self, frame):
        self.calls.append(('beat', frame))
        elapsed = Fraction(frame, self.fps)
        cycle, within = divmod(elapsed, 2)
        # Two distinct BPM intervals: 0..1s at 2 beats/s, 1..2s at 3 beats/s
        phase = 2 * within if within <= 1 else 2 + 3 * (within - 1)
        return float(phase + 5 * cycle)


def test_absolute_clock_seeking_repeat_excerpt_and_variable_bpm_does_not_recompute():
    project = ProjectFixture()
    frames = [239, 0, 121, 59, 120, 60, 239]
    values = [frame_clock(project, n) for n in frames]
    assert [v.frame for v in values] == frames
    assert values[0] == values[-1]  # no sequential state
    assert values[1].output_time_numerator == 0
    assert (values[3].output_time_numerator, values[3].output_time_denominator) == (59, 60)
    assert (values[4].source_seconds, values[4].continuous_beat) == (1.0, 5.0)
    assert (values[5].source_seconds, values[5].continuous_beat) == (2.0, 2.0)
    assert values[2].continuous_beat > values[4].continuous_beat
    assert project.calls == [item for n in frames for item in (('source', n), ('beat', n))]
    for invalid in (-1, project.frame_count, True, 3.0, '30'):
        with pytest.raises(ValueError):
            frame_clock(project, invalid)


def test_complete_project_dependency_closure_is_conservative_order_independent():
    p = ProjectFixture()
    digest = project_fingerprint(p, renderer_abi='FrameRudiments test implementation SHA')
    assert len(digest) == 64 and '/private/' not in digest
    p.inputs.reverse()
    p.data = dict(reversed(list(p.data.items())))
    assert project_fingerprint(p, renderer_abi='FrameRudiments test implementation SHA') == digest
    p.data['effect_graph']['nodes'].append({'id': 'opacity', 'type': 'opacity', 'inputs': ['a'], 'opacity': .8})
    assert project_fingerprint(p, renderer_abi='FrameRudiments test implementation SHA') != digest
    assert project_fingerprint(p, renderer_abi='different renderer SHA') != digest
    p.inputs[0]['sha256'] = 'c' * 64
    assert project_fingerprint(p, renderer_abi='FrameRudiments test implementation SHA') != digest
    with pytest.raises(ValueError):
        project_fingerprint(p, renderer_abi='')
    p.data['effect_graph']['nodes'][1]['opacity'] = float('nan')
    with pytest.raises(ValueError, match='finite JSON'):
        project_fingerprint(p, renderer_abi='shader')
    assert canonical_digest({'b': 1, 'a': [2, 3]}) == canonical_digest({'a': [2, 3], 'b': 1})


def test_cue_segments_are_stable_half_open_and_derived_from_native_ledger():
    p = ProjectFixture(cues=[{'frame': 120}, {'frame': 45}, {'frame': 120},
                                     {'frame': 0}, {'frame': 240}, {'frame': 1000}])
    digest = project_fingerprint(p, renderer_abi='renderer-A')
    sections = section_segments(p, project_hash=digest)
    assert [(s.start_frame, s.end_frame_exclusive) for s in sections] == [
        (0, 45), (45, 120), (120, 240),
    ]
    assert sections[0].dependency_scope == DEPENDENCY_SCOPE
    assert segment_for_frame(sections, 44) == sections[0]
    assert segment_for_frame(sections, 45) == sections[1]
    assert segment_for_frame(sections, 239) == sections[-1]
    for n in (-1, 240):
        with pytest.raises(ValueError):
            segment_for_frame(sections, n)
    assert sections == section_segments(p, project_hash=digest)
    p.cues.insert(0, {'frame': 100})
    assert sections != section_segments(p, project_hash=digest)


def test_empty_cue_ledger_fixed_fallback_does_not_duplicate_frames():
    p = ProjectFixture()
    p.frame_count = 6001
    digest = 'f' * 64
    sections = section_segments(p, project_hash=digest, fallback_seconds=30)
    assert [(s.start_frame, s.end_frame_exclusive) for s in sections] == [
        (0, 1800), (1800, 3600), (3600, 5400), (5400, 6001)]
    assert sum(s.end_frame_exclusive - s.start_frame for s in sections) == p.frame_count
    assert segment_for_frame(sections, 6000) == sections[-1]
    with pytest.raises(ValueError):
        segment_for_frame([sections[0], sections[2]], 1900)


def test_segment_count_bound_and_no_false_local_dependency_proof():
    p = ProjectFixture()
    p.frame_count = 5000
    p.cues = [{'frame': n} for n in range(1, 5000)]
    with pytest.raises(ValueError, match='segment count'):
        section_segments(p, project_hash='a' * 64)
    with pytest.raises(ValueError, match='cannot claim'):
        Segment('x', 0, 2, 'a' * 64, dependency_scope='precisely_segment_local')


def test_quality_ladder_follows_authored_quality_and_does_not_claim_a_renderer():
    p = ProjectFixture()
    profiles = quality_profiles(p)
    assert [q.id for q in profiles] == [f'Q{i}' for i in range(6)]
    assert [q.quality_rank for q in profiles] == list(range(6))
    assert [(q.output_width, q.output_height) for q in profiles] == [
        (480, 270), (960, 540), (1440, 810), (1920, 1080), (1920, 1080), (1920, 1080)]
    assert [(q.spatial_scale, q.temporal_samples) for q in profiles] == [
        (1, 1), (1, 1), (1, 1), (1, 2), (2, 4), (4, 8)]
    assert all(q.execution_status == 'not_implemented' and not q.production_equivalent
               for q in profiles)
    p.output_quality = SimpleNamespace(enabled=False, spatial_scale=4, temporal_samples=8)
    assert [(q.spatial_scale, q.temporal_samples) for q in quality_profiles(p)] == [(1, 1)] * 6
    p.output_quality = SimpleNamespace(enabled=True, spatial_scale=2, temporal_samples=3)
    assert quality_profiles(p)[-1].temporal_samples == 3
    assert quality_profiles(p)[-1].spatial_scale == 2
    assert p.data['effect_graph']['nodes'][0] == {'id': 'a', 'type': 'source'}


def test_target_rate_default_ceiling_and_rejection():
    p = ProjectFixture(fps=120)
    assert requested_target_fps(p) == 60
    assert requested_target_fps(p, 24) == 24
    assert requested_target_fps(p, preview_ceiling=30) == 30
    assert requested_target_fps(ProjectFixture(fps=12)) == 12  # authored FPS default
    for bad in (0, 14, 121, True, 59.5):
        with pytest.raises(ValueError):
            requested_target_fps(p, bad)


def test_future_cache_key_namespaces_project_segment_generation_and_rank():
    p = ProjectFixture(cues=[{'frame': 100}])
    digest = project_fingerprint(p, renderer_abi='r1')
    sections = section_segments(p, project_hash=digest)
    profiles = quality_profiles(p)
    a = frame_cache_identity(digest, sections[0], 50, profiles[0], renderer_abi='r1')
    assert a == frame_cache_identity(digest, sections[0], 50, profiles[0], renderer_abi='r1')
    assert a != frame_cache_identity(digest, sections[0], 50, profiles[5], renderer_abi='r1')
    assert a != frame_cache_identity(digest, replace(sections[0], generation=1), 50, profiles[0], renderer_abi='r1')
    assert a != frame_cache_identity(digest, sections[0], 51, profiles[0], renderer_abi='r1')
    for segment, frame in ((sections[1], 50), (sections[0], 100)):
        with pytest.raises(ValueError):
            frame_cache_identity(digest, segment, frame, profiles[0], renderer_abi='r1')
    with pytest.raises(ValueError):
        frame_cache_identity('b' * 64, sections[0], 50, profiles[0], renderer_abi='r1')


def test_telemetry_separates_fresh_render_cache_hit_presentation_and_stale():
    m = PreviewTelemetry(window=8)
    assert m.snapshot()['render_ms_p95'] is None
    for sample in (5, 7, 12, 40, 15, 6, 17, 30, 2):
        assert m.record_render(render_ms=sample, queue_ms=sample / 2)
    m.record_hit()
    m.record_presentation(repeated=True)
    m.record_presentation(repeated=False)
    m.record_rejection()
    m.record_rejection(failure=True)
    data = m.snapshot()
    assert data['render_samples'] == 8 and data['rendered_fresh'] == 9
    assert data['render_ms_p95'] == 40
    assert data['queue_ms_p95'] == 20
    assert data['cache_hits'] == 1 and data['presented'] == 2 and data['presented_duplicates'] == 1
    assert data['render_errors'] == 1 and data['queue_rejected'] == 1
    assert data['fresh_render_fps'] is None and data['presented_fps'] is None
    assert data['presentation_lag_ms_p95'] is None
    before = m.snapshot()
    m.new_generation(2)
    assert not m.record_render(render_ms=100, generation=1)
    assert m.snapshot()['stale_discarded'] == 1 and m.snapshot()['render_samples'] == 0
    assert m.snapshot()['rendered_fresh'] == before['rendered_fresh']
    assert m.record_render(render_ms=10, presentation_lag_ms=7, generation=2)
    assert m.snapshot()['presentation_lag_ms_p95'] == 7
    assert '/private/' not in json.dumps(m.snapshot())


def test_invalid_telemetry_rejected_and_concurrent_samples_bounded():
    m = PreviewTelemetry(window=24)
    for bad in (-1, float('nan'), float('inf'), True):
        with pytest.raises(ValueError):
            m.record_render(render_ms=bad)
    with pytest.raises(ValueError):
        m.new_generation(0)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda n: m.record_render(render_ms=n + 1), range(400)))
    data = m.snapshot()
    assert data['rendered_fresh'] == 400
    assert data['render_samples'] == 24
    assert data['render_ms_p95'] is not None
    assert data['cache_hits'] == 0
