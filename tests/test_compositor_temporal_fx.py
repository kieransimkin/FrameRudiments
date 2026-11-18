"""M11.3 real Project/Compositor integration: exact reads and feedback coexistence.

Uses the checked-in test_compositor_temporal.project_factory, with actual
checksum-pinned WAV/sprite sources and a real native DanceRudiments clock.
"""
from copy import deepcopy
from fractions import Fraction
import json

import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.feedback_history import FeedbackCancelled
from framerudiments.quality_effects import linear_shutter_composite
from framerudiments.compositor_temporal import temporal_read_bounds
from test_compositor_temporal import project_factory as project_factory


def make_project(factory, *, effects=None, feedback=None, **kwargs):
    project = factory(**kwargs)
    data = deepcopy(project.data)
    post = data.setdefault('post_effects', {})
    if effects is not None:
        post['temporal_fx'] = effects
    if feedback is not None:
        post['feedback'] = {'delay_frames': 2, 'mix': .6, 'reset': 'repeat',
                            'checkpoint_interval': 3, 'max_replay_frames': 64,
                            'max_state_bytes': 8 * 1024 * 1024, **feedback}
    return Project(data, project.base)


def _raw(factory, **kwargs):
    return Compositor(make_project(factory, **kwargs))


def test_planning_contract_is_raster_free_and_exact_source_repeats(project_factory, monkeypatch):
    comp = _raw(project_factory, factor=1, shutter=False,
        effects={'motion_echoes': {'count': 3, 'delay_frames': 2}})
    try:
        monkeypatch.setattr(comp, '_native_frame', lambda *_a, **_k: pytest.fail('planning drew pixels'))
        plan = comp.temporal_fx_plan(5)
        assert plan['source_frames'] == [1, 3, 5]
        assert comp.temporal_fx_plan(6)['source_frames'] == [6]
        assert comp.temporal_fx_contract()['max_unique_frames'] == 48
        assert comp.temporal_contract()['temporal_fx']['scope'] == 'pre_feedback_native_RGB8'
        assert json.loads(json.dumps(plan)) == plan
        assert not hasattr(comp, '_temporal_virtual')
    finally:
        comp.close()


def test_visual_half_speed_is_independent_linear_interpolation(project_factory):
    fx = {'time_remap': {'output_start': 0, 'output_end': 6,
                         'source_start': 0, 'rate': [1, 2]}}
    actual = _raw(project_factory, factor=1, shutter=False, effects=fx)
    native = _raw(project_factory, factor=1, shutter=False)
    try:
        for n in (1, 3, 5):
            output, context = actual.frame(n, apply_quality=False)
            lower, upper = n // 2, (n + 1) // 2
            first, _ = native.frame(lower, apply_quality=False)
            second, _ = native.frame(upper, apply_quality=False)
            expected = (first if lower == upper else linear_shutter_composite(
                [first, second], [.5, .5], strength=1))
            assert output.tobytes() == expected.tobytes()
            assert context.frame == n
            assert actual.evidence()['temporal_fx']['last_execution']['completed'] is True
            assert actual.post_effect_reports()['temporal_fx']['frame'] == n
    finally:
        actual.close()
        native.close()


def test_motion_echoes_exact_repeat_reset_and_no_cross_cycle(project_factory):
    config = {'motion_echoes': {'count': 2, 'delay_frames': 2, 'strength': 1, 'decay': 0}}
    comp = _raw(project_factory, factor=1, shutter=False, effects=config)
    native = _raw(project_factory, factor=1, shutter=False)
    try:
        for n in (0, 1, 6, 7, 12, 13):
            actual, _ = comp.frame(n, apply_quality=False)
            reference, _ = native.frame(n, apply_quality=False)
            assert actual.tobytes() == reference.tobytes()
        for n in (4, 5, 10, 11):
            actual, _ = comp.frame(n, apply_quality=False)
            reference, _ = native.frame(n - 2, apply_quality=False)
            assert actual.tobytes() == reference.tobytes()
    finally:
        comp.close()
        native.close()


def test_all_four_effects_remain_shuffle_safe_with_native_clock(project_factory):
    fx = {'boundary': 'programme',
          'time_remap': {'output_start': 0, 'output_end': 16,
                         'source_start': 2, 'rate': [1, 2]},
          'motion_echoes': {'count': 3, 'delay_frames': 1, 'strength': .6, 'shift_x': 2},
          'directional_blur': {'samples': 4, 'history_frames': 3, 'strength': .5},
          'slit_scan': {'axis': 'x', 'strips': 6, 'history_frames': 3, 'strength': .8}}
    comp = _raw(project_factory, factor=1, shutter=False, effects=fx)
    fresh = _raw(project_factory, factor=1, shutter=False, effects=fx)
    try:
        expected = {n: comp.frame(n, apply_quality=False)[0].tobytes() for n in range(1,9)}
        for n in (8,2,5,1,7,3,6,4):
            pixels, context = fresh.frame(n, apply_quality=False)
            assert pixels.tobytes() == expected[n]
            assert context.source_seconds == comp.project.source_time(n)
            assert fresh.evidence()['temporal_fx']['last_execution']['native_tap_callbacks'] <= 48
    finally:
        comp.close()
        fresh.close()


def test_feedback_restores_checkpoint_with_temporal_fx_input(project_factory):
    fx = {'motion_echoes': {'count': 2, 'delay_frames': 1, 'strength': .45},
          'slit_scan': {'strips': 4, 'history_frames': 3, 'strength': .5}}
    producer = _raw(project_factory, factor=1, shutter=False, effects=fx,
                    feedback={'reset':'programme'})
    restored = _raw(project_factory, factor=1, shutter=False, effects=fx,
                    feedback={'reset':'programme'})
    cold = _raw(project_factory, factor=1, shutter=False, effects=fx,
                feedback={'reset':'programme'})
    try:
        producer.frame(9)
        checkpoint = producer.feedback_checkpoint()
        assert checkpoint is not None
        restored.restore_feedback(checkpoint, expected_sha256=checkpoint.digest)
        image, context = restored.frame(13, apply_quality=False)
        expected, context2 = cold.frame(13, apply_quality=False)
        assert image.tobytes() == expected.tobytes()
        assert context == context2
        assert restored.feedback_snapshot()['last_execution']['origin'] == 'checkpoint'
        assert restored.post_effect_reports()['feedback']['frame'] == 13
    finally:
        producer.close()
        restored.close()
        cold.close()


def test_cancel_during_native_taps_restores_metadata(project_factory):
    comp = _raw(project_factory, factor=1, shutter=False,
        effects={'motion_echoes': {'count': 3, 'delay_frames': 1}})
    try:
        comp.frame(1)
        before = deepcopy(comp.frame_reports())
        before_post = deepcopy(comp.post_effect_reports())
        completed = []
        def progress(receipt):
            completed.append(receipt)
        def cancel():
            return bool(completed)
        with pytest.raises(FeedbackCancelled):
            comp.frame(5, cancelled=cancel, progress=progress)
        assert comp.frame_reports() == before
        assert comp.post_effect_reports() == before_post
        assert comp.frame(5)[1].frame == 5
    finally:
        comp.close()


def test_temporal_fx_conservative_reach_is_reported_for_invalidation(project_factory):
    data = make_project(project_factory, factor=1, shutter=True,
        effects={'motion_echoes': {'delay_frames': 3, 'count': 2},
                 'slit_scan': {'history_frames': 7}}).data
    past, future = temporal_read_bounds(data)
    assert past >= 15 and future >= 0
    comp = Compositor(Project(deepcopy(data), project_factory().base))
    try:
        contract = comp.temporal_fx_contract()
        assert contract['source_read_bounds']['past_frames'] >= 7
    finally:
        comp.close()


def test_invalid_resource_request_fails_before_native_render(project_factory, monkeypatch):
    project = make_project(project_factory, factor=1, shutter=False,
        effects={'slit_scan': {'strips': 24, 'history_frames': 24},
                 'motion_echoes': {'count': 12, 'delay_frames': 2}})
    comp = Compositor(project)
    try:
        # This fixture is small, so admission succeeds: test rejection for an
        # illegal source request without executing native callbacks instead.
        monkeypatch.setattr(comp, '_native_frame', lambda *_a, **_k: pytest.fail('unexpected raster'))
        with pytest.raises(ValueError):
            comp.temporal_fx_plan(project.frame_count)
    finally:
        comp.close()


def test_no_temporal_fx_keeps_existing_scheduler_outputs_byte_identical(project_factory):
    project = project_factory(factor=1, shutter=False)
    comp=Compositor(project)
    try:
        output=comp.frame(3, apply_quality=False)[0].tobytes()
        assert comp.temporal_fx_contract() is None
        assert comp.temporal_fx_plan(3) is None
        assert comp.frame(3, apply_quality=False)[0].tobytes()==output
    finally:
        comp.close()
