"""Native delayed recurrence, exact clocks, portable checkpoints and failures."""
from copy import deepcopy
from fractions import Fraction
import json
from math import ceil
from pathlib import Path
import shutil

from PIL import Image
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.feedback_history import FeedbackCancelled, FeedbackUnavailable
from framerudiments.quality_effects import linear_shutter_composite

from test_compositor_temporal import project_factory as project_factory


def _project(factory, *, feedback=None, **kwargs):
    project = factory(**kwargs)
    data = deepcopy(project.data)
    data.setdefault('post_effects', {})['feedback'] = {
        'delay_frames': 1, 'mix': .6, 'reset': 'programme',
        'checkpoint_interval': 4, 'max_checkpoints': 4,
        **(feedback or {}),
    }
    return Project(data, project.base)


def _reference(project):
    """Independent recurrence over the real feedback-disabled native pipeline."""
    data = deepcopy(project.data)
    feedback = data['post_effects'].pop('feedback')
    native = Compositor(Project(data, project.base))
    span = Fraction((project.end_sample - project.start_sample) * project.fps, project.sample_rate)
    results, contexts = {}, {}
    try:
        for frame in range(project.frame_count):
            current, context = native.frame(frame, apply_quality=False)
            reset = 0 if feedback['reset'] == 'programme' else ceil((frame // span) * span)
            previous = frame - feedback['delay_frames']
            if previous >= reset:
                current = linear_shutter_composite([current, results[previous]],
                    [1 - feedback['mix'], feedback['mix']], 1)
            results[frame], contexts[frame] = current, context
    finally:
        native.close()
    return results, contexts


@pytest.mark.parametrize('factor,shutter,span,delay,reset', [
    (1, False, 6000, 1, 'programme'),
    (1, True, 6000, 3, 'repeat'),
    (4, True, 4900, 2, 'repeat'),
])
def test_native_recurrence_matches_independent_sequential_shuffled_and_cold_oracle(
        project_factory, factor, shutter, span, delay, reset):
    project = _project(project_factory, factor=factor, shutter=shutter, span_samples=span,
        feedback={'delay_frames': delay, 'reset': reset})
    expected, contexts = _reference(project)
    comp = Compositor(project)
    try:
        requests = [*range(project.frame_count), project.frame_count - 1, 4, 0, 8, 3, project.frame_count - 1]
        for frame in requests:
            image, context = comp.frame(frame, apply_quality=False)
            assert image.tobytes() == expected[frame].tobytes()
            assert context == contexts[frame]
            assert comp.post_effect_reports()['feedback']['frame'] == frame
        cold = Compositor(Project(deepcopy(project.data), project.base))
        try:
            assert cold.frame(project.frame_count - 1)[0].tobytes() == expected[project.frame_count - 1].tobytes()
        finally:
            cold.close()
    finally:
        comp.close()


@pytest.mark.parametrize('mix', [0, 1])
def test_missing_history_is_unattenuated_and_mix_endpoints_are_exact(project_factory, mix):
    project = _project(project_factory, factor=1, shutter=False,
        feedback={'delay_frames': 3, 'mix': mix, 'reset': 'repeat'})
    expected, _ = _reference(project)
    comp = Compositor(project)
    try:
        for frame in (0, 2, 3, 5, 6, 8, 9):
            assert comp.frame(frame)[0].tobytes() == expected[frame].tobytes()
            report = comp.post_effect_reports()['feedback']
            assert report['history_frame'] == (None if frame % 6 < 3 else frame - 3)
        if mix == 1:
            assert expected[0].tobytes() == expected[3].tobytes()
    finally:
        comp.close()


def test_feedback_inspection_has_no_raster_or_virtual_renderer_and_preserves_authored_document(
        project_factory, monkeypatch):
    project = _project(project_factory, factor=4, span_samples=4900,
        feedback={'reset': 'repeat'})
    before = deepcopy(project.data)
    comp = Compositor(project)
    try:
        monkeypatch.setattr(Compositor, '_native_frame', lambda *_a, **_k: pytest.fail('inspection rendered'))
        contract = comp.feedback_contract()
        state = comp.feedback_snapshot()
        assert comp.feedback_checkpoint() is None
        assert comp.feedback_plan(5)['start_frame'] == 5
        assert comp.feedback_plan(9)['history_refs'] == [8]
        assert comp.feedback_plan(10)['reset_frame'] == 10
        assert contract['scope']['checkpointed_state'] == 'owned_rgb8_feedback_history'
        assert contract['scope']['sequential_external_engine_checkpoints'] is False
        assert contract['scope']['gpu_state_serialization'] is False
        assert Fraction(**contract['repeat_span_frames']) == Fraction(49, 10)
        assert state['checkpoint_count'] == 0 and state['last_execution'] is None
        assert not hasattr(comp, '_temporal_virtual')
        assert json.loads(json.dumps(contract)) == contract
        assert str(project.base) not in json.dumps(contract)
        assert project.data == before
    finally:
        comp.close()


def test_actual_checkpoint_restore_saves_native_work_and_always_restores_target_metadata(
        project_factory, monkeypatch):
    project = _project(project_factory, factor=1, shutter=False)
    original = Compositor(project)
    restored = Compositor(Project(deepcopy(project.data), project.base))
    cold = Compositor(Project(deepcopy(project.data), project.base))
    calls = []
    native = Compositor._native_frame

    def record(owner, frame, **kwargs):
        calls.append((id(owner), frame))
        return native(owner, frame, **kwargs)

    monkeypatch.setattr(Compositor, '_native_frame', record)
    try:
        original.frame(11)
        checkpoint = original.feedback_checkpoint()
        assert checkpoint.next_frame == 12 and checkpoint.history
        restored.restore_feedback(checkpoint, expected_sha256=checkpoint.digest)
        plan = restored.feedback_plan(14)
        assert (plan['origin'], plan['start_frame'], plan['steps']) == ('checkpoint', 12, 3)
        image, context = restored.frame(14)
        assert [frame for owner, frame in calls if owner == id(restored)] == [12, 13, 14]
        assert image.tobytes() == cold.frame(14)[0].tobytes()
        assert [frame for owner, frame in calls if owner == id(cold)] == list(range(15))
        assert context.frame == 14 and context.source_seconds == project.source_time(14)
        assert restored.post_effect_reports()['feedback']['frame'] == 14
        assert restored.evidence()['temporal_schedule']['last_execution']['frame'] == 14
        before = len(calls)
        assert restored.frame(14)[0].tobytes() == image.tobytes()
        assert any(owner == id(restored) and frame == 14 for owner, frame in calls[before:])
    finally:
        original.close()
        restored.close()
        cold.close()


def test_content_identity_allows_relocated_absolute_and_relative_pins(project_factory, tmp_path):
    source = _project(project_factory, factor=1, shutter=False)
    data = deepcopy(source.data)
    moved = tmp_path / 'relocated'
    moved.mkdir()
    for name in ('source.wav', 'source.png'):
        shutil.copyfile(tmp_path / name, moved / name)
    data['audio']['source']['path'] = str(moved / 'source.wav')
    data['layers'][0]['asset']['path'] = str(moved / 'source.png')
    first, second = Compositor(source), Compositor(Project(data, moved))
    try:
        assert first.feedback_contract()['identity'] == second.feedback_contract()['identity']
        first.frame(7)
        checkpoint = first.feedback_checkpoint()
        second.restore_feedback(checkpoint, expected_sha256=checkpoint.digest)
        assert second.frame(10)[0].tobytes() == first.frame(10)[0].tobytes()
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize('change', ['mix', 'clock', 'quality', 'pin', 'backend'])
def test_checkpoints_reject_changed_declarative_identity(project_factory, change, monkeypatch):
    source = _project(project_factory, factor=1, shutter=False)
    data = deepcopy(source.data)
    if change == 'mix':
        data['post_effects']['feedback']['mix'] = .25
    elif change == 'clock':
        data['clock']['origin'] += .1
    elif change == 'quality':
        data['render_quality']['antialias'] = .5
    elif change == 'pin':
        data['layers'][0]['asset']['sha256'] = '0' * 64
        # The declared pin is itself verified during construction.
        with pytest.raises(ValueError, match='(?i)hash|sha'):
            Compositor(Project(data, source.base))
        return
    first, second = Compositor(source), Compositor(Project(data, source.base))
    try:
        first.frame(3)
        checkpoint = first.feedback_checkpoint()
        if change == 'backend':
            monkeypatch.setattr(second, 'effect_graph_backend_mode', 'auto')
            monkeypatch.setattr(second, '_owns_effect_graph_gpu', False)
        with pytest.raises(ValueError, match='identity|configuration'):
            second.restore_feedback(checkpoint, expected_sha256=checkpoint.digest)
        assert second.feedback_checkpoint() is None
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize('config', [
    {}, {'delay_frames': 0}, {'delay_frames': -1}, {'delay_frames': True}, {'delay_frames': 1.5},
    {'delay_frames': 65}, {'delay_frames': 1, 'mix': True}, {'delay_frames': 1, 'mix': float('nan')},
    {'delay_frames': 1, 'mix': 1.01}, {'delay_frames': 1, 'reset': 'forever'},
    {'delay_frames': 1, 'checkpoint_interval': 0}, {'delay_frames': 1, 'max_checkpoints': 0},
    {'delay_frames': 1, 'max_replay_frames': 36001}, {'delay_frames': 1, 'max_state_bytes': 2**30},
    {'delay_frames': 1, 'unknown': 3}, {'delay_frames': 1, 'modulations': [{}]},
    {'delay_frames': 1, 'modulations': 'bad'}, {'delay_frames': 1, 'modulations': [{}, {}]},
])
def test_authored_feedback_schema_is_strict(project_factory, config, monkeypatch):
    source = project_factory(factor=1, shutter=False)
    data = deepcopy(source.data)
    data['post_effects'] = {'feedback': config}
    monkeypatch.setattr(Compositor, '_native_frame', lambda *_a, **_k: pytest.fail('invalid config rendered'))
    with pytest.raises(ValueError):
        Compositor(Project(data, source.base))


@pytest.mark.parametrize('custom', ['scene', 'renderers'])
def test_feedback_rejects_unidentifiable_python_hooks_before_allocating(project_factory, custom, monkeypatch):
    project = _project(project_factory, factor=1, shutter=False)
    monkeypatch.setattr(Image, 'new', lambda *_a, **_k: pytest.fail('allocated before hook admission'))
    with pytest.raises(ValueError, match='custom scene/renderers'):
        Compositor(project, **{custom: (lambda *_a: None) if custom == 'scene' else {}})


def test_native_mix_modulation_uses_source_clock_and_weighted_combination(project_factory):
    project = _project(project_factory, factor=1, shutter=False,
        feedback={'mix': .2, 'reset': 'repeat', 'modulations': [{
            'motion': {'kind': 'named', 'pattern': 'circle', 'repeat_clock': 'source'},
            'target': 'feedback.mix', 'axis': 'x', 'range': [0, .6],
            'combine': 'add', 'weight': .5,
        }]})
    comp = Compositor(project)
    try:
        for frame in (2, 8, 14, 1):
            image, context = comp.frame(frame)
            selected, native = comp.post_pipeline.feedback.driver.output(context)
            report = comp.post_effect_reports()['feedback']
            assert report['mix'] == pytest.approx(.2 + .5 * selected)
            assert report['modulations']['feedback.mix']['native_value'] == native
            assert report['source_seconds'] == project.source_time(frame)
            assert image.size == (64, 48)
        comp.frame(2)
        first = comp.post_effect_reports()['feedback']['mix']
        comp.frame(8)
        assert comp.post_effect_reports()['feedback']['mix'] == first
    finally:
        comp.close()


@pytest.mark.parametrize('budget', ['replay', 'history', 'native', 'routes'])
def test_all_work_budgets_admit_before_raster_callbacks(project_factory, monkeypatch, budget):
    from framerudiments import compositor_temporal

    settings = {'max_replay_frames': 2} if budget == 'replay' else {'max_state_bytes': 1} if budget == 'history' else {}
    comp = Compositor(_project(project_factory, factor=4, routing=budget == 'routes', feedback=settings))
    try:
        if budget in ('native', 'routes'):
            monkeypatch.setattr(compositor_temporal, 'MAX_NATIVE_RASTER_BYTES', 1)
        monkeypatch.setattr(Compositor, '_native_frame', lambda *_a, **_k: pytest.fail('unadmitted native work'))
        with pytest.raises(FeedbackUnavailable if budget == 'replay' else ValueError):
            comp.frame(4)
        assert not hasattr(comp, '_temporal_virtual')
        assert comp._last_temporal_schedule is None
    finally:
        comp.close()


@pytest.mark.parametrize('failure', ['frame', 'budget', 'cancel', 'callback'])
def test_admission_failure_retires_prior_receipts_without_changing_committed_history(
        project_factory, monkeypatch, failure):
    from framerudiments import compositor_temporal

    comp = Compositor(_project(project_factory, factor=2, shutter=False))
    try:
        comp.frame(2)
        checkpoint = comp.feedback_checkpoint()
        if failure == 'budget':
            monkeypatch.setattr(compositor_temporal, 'MAX_NATIVE_RASTER_BYTES', 1)
        with pytest.raises(FeedbackCancelled if failure == 'cancel' else ValueError):
            comp.frame(-1 if failure == 'frame' else 4,
                cancelled=(lambda: True) if failure == 'cancel' else None,
                progress=42 if failure == 'callback' else None)
        assert comp.feedback_checkpoint().digest == checkpoint.digest
        assert comp.feedback_snapshot()['last_execution'] is None
        assert comp.feedback_snapshot()['current_request_frames'] == 0
        assert comp._last_temporal_schedule is None
        assert not hasattr(comp, '_temporal_virtual')
    finally:
        comp.close()


@pytest.mark.parametrize('failure', ['native', 'aa', 'progress', 'cancel'])
def test_partial_reconstruction_failure_preserves_committed_state_and_metadata(
        project_factory, monkeypatch, failure):
    project = _project(project_factory, factor=2, shutter=False)
    expected, _ = _reference(project)
    comp = Compositor(project)
    try:
        comp.frame(2)
        checkpoint = comp.feedback_checkpoint()
        metadata = deepcopy(comp.extents), deepcopy(comp.text_states), deepcopy(comp.post_effect_reports())
        native = Compositor._native_frame
        seen = []

        def record(owner, frame, **kwargs):
            seen.append(frame)
            if failure == 'native' and len(seen) == 4:
                raise RuntimeError('injected native failure')
            return native(owner, frame, **kwargs)

        def failed_aa(_image):
            raise RuntimeError('injected AA failure')

        def progress(state):
            if failure == 'progress' and state['current_request_frames'] >= 2:
                raise RuntimeError('injected progress failure')

        with monkeypatch.context() as patch:
            patch.setattr(Compositor, '_native_frame', record)
            if failure == 'aa':
                patch.setattr(comp.output_quality, 'apply', failed_aa)
            with pytest.raises(FeedbackCancelled if failure == 'cancel' else FeedbackUnavailable):
                comp.frame(7, cancelled=lambda: failure == 'cancel' and len(seen) >= 3, progress=progress)
        assert comp.feedback_checkpoint().digest == checkpoint.digest
        assert (comp.extents, comp.text_states, comp.post_effect_reports()) == metadata
        assert comp.feedback_snapshot()['last_execution'] is None
        assert comp.evidence()['temporal_schedule']['last_execution'] is None
        assert not hasattr(comp, '_temporal_virtual')
        assert comp.frame(7)[0].tobytes() == expected[7].tobytes()
    finally:
        comp.close()


@pytest.mark.parametrize('when', ['before', 'during_native', 'during_progress'])
def test_changed_input_revokes_state_without_masking_pin_error(project_factory, monkeypatch, when):
    comp = Compositor(_project(project_factory, factor=2, shutter=False))
    try:
        comp.frame(2)
        source = Path(comp.project.base) / 'source.png'
        old = source.read_bytes()
        seen = []
        native = Compositor._native_frame

        def change():
            source.write_bytes(old + b'changed pin')

        def record(owner, frame, **kwargs):
            result = native(owner, frame, **kwargs)
            seen.append(frame)
            if when == 'during_native' and len(seen) == 2:
                change()
            return result

        def progress(_state):
            if when == 'during_progress':
                change()

        if when == 'before':
            change()
        monkeypatch.setattr(Compositor, '_native_frame', record)
        with pytest.raises(ValueError, match='(?i)changed|input|pin|hash|source'):
            comp.frame(6, progress=progress)
        state = comp.feedback_snapshot()
        assert state['phase'] == 'closed' and state['retained_state_bytes'] == 0
        assert state['last_execution'] is None
        assert not hasattr(comp, '_temporal_virtual')
        with pytest.raises(ValueError, match='revoked'):
            comp.frame(6)
    finally:
        comp.close()


def test_final_aa_runs_once_per_requested_target_and_never_enters_feedback_history(project_factory, monkeypatch):
    project = _project(project_factory, factor=4, shutter=True)
    expected, _ = _reference(project)
    comp = Compositor(project)
    applied = []

    def aa(image):
        applied.append(image.tobytes())
        image.paste('#ff00ff', (0, 0, *image.size))
        return image

    monkeypatch.setattr(comp.output_quality, 'apply', aa)
    try:
        assert comp.frame(7)[0].getpixel((0, 0)) == (255, 0, 255)
        assert len(applied) == 1 and applied[0] == expected[7].tobytes()
        assert comp.frame(8, apply_quality=False)[0].tobytes() == expected[8].tobytes()
        assert len(applied) == 1
        returned = comp.frame(9, apply_quality=False)[0]
        returned.paste('#00ffff', (0, 0, *returned.size))
        assert comp.frame(10, apply_quality=False)[0].tobytes() == expected[10].tobytes()
        assert comp.frame(9, apply_quality=False)[0].tobytes() == expected[9].tobytes()
    finally:
        comp.close()


def test_feedback_keeps_current_frame_routes_and_external_scope_separate(project_factory):
    project = _project(project_factory, factor=4, routing=True, injection=True)
    data = deepcopy(project.data)
    data['post_effects'].pop('feedback')
    comp, native = Compositor(project), Compositor(Project(data, project.base))
    try:
        comp.frame(8)
        native.frame(8)
        assert {key: image.tobytes() for key, image in comp._route_parts} == {
            key: image.tobytes() for key, image in native._route_parts}
        assert comp.feedback_contract()['scope']['native_compositor'] == 'pre_external_effects'
        assert comp.feedback_contract()['scope']['sequential_external_engine_checkpoints'] is False
        with pytest.raises(ValueError, match='sequential external'):
            comp.sample_frames(8, (0, 1))
    finally:
        comp.close()
        native.close()


def test_feedback_close_releases_owned_state_and_ordinary_projects_remain_stateless(project_factory):
    plain = Compositor(project_factory(factor=1, shutter=False))
    comp = Compositor(_project(project_factory, factor=1, shutter=False))
    try:
        assert plain.feedback_contract() is None and plain.feedback_checkpoint() is None
        assert plain.feedback_plan(2) is None and plain.feedback_snapshot() is None
        assert 'feedback' not in plain.evidence()
        with pytest.raises(ValueError, match='no configured'):
            plain.restore_feedback(None, expected_sha256='0' * 64)
        comp.frame(7)
        assert comp.feedback_snapshot()['retained_state_bytes'] > 0
        comp.close()
        state = comp.feedback_snapshot()
        assert state['phase'] == 'closed' and state['retained_state_bytes'] == 0
        with pytest.raises(FeedbackUnavailable, match='renderer_closed'):
            comp.frame(2)
    finally:
        plain.close()
        comp.close()
