"""M11.2 native preview reconstruction, lifecycle, invalidation and inspection.

Exercise real Project/Compositor/Pillow/cache paths with pinned synthetic inputs.
The independent recurrence and encoded-image oracle live in the native qualifier;
these tests cover the boundaries between that implementation and authoring.
"""
from copy import deepcopy
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.cli import main
from framerudiments.compositor import Compositor
from framerudiments.preview_native_renderer import NativePreviewRenderer
from framerudiments.preview_quality import ProfileUnsupported, profile_capabilities, profile_document
from framerudiments.preview_refinement_process import RefinementCancelled
from framerudiments.preview_replay import StatefulSeekUnavailable
from framerudiments.preview_seek_support import seek_status, temporal_reach
from framerudiments.preview_stream_s2 import PreviewStreamAPI
from framerudiments.project import Project
from framerudiments.temporal_inspection import inspect_temporal
from test_preview_adaptive_stream import ready
from test_preview_native_project import fixture


def feedback_project(tmp_path, *, reset='programme', max_replay_frames=3600):
    path, data = fixture(tmp_path)
    data.update(fps=12, fps_reason='small synthetic native feedback qualification')
    data['audio'].update(end_sample=24000, repeats=4)
    data['render_quality'] = {'spatial_scale': 1, 'temporal_samples': 2, 'antialias_passes': 2}
    data['layer_animations'] = {'layer:square': {'opacity': {'keys': [
        {'time': 0, 'value': .1}, {'time': .25, 'value': 1}, {'time': .5, 'value': .2}]}}}
    data['post_effects'] = {'feedback': {'delay_frames': 3, 'mix': .65, 'reset': reset,
        'checkpoint_interval': 4, 'max_checkpoints': 2, 'max_replay_frames': max_replay_frames}}
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


def renderer_for(path, data, tmp_path):
    return NativePreviewRenderer(path, data, {}, validate_draft=lambda _a, _b: None,
                                 temp_parent=tmp_path / 'native')


def pixels(png):
    with Image.open(BytesIO(png)) as image:
        assert image.mode == 'RGBA' and image.size == (64, 48)
        return image.tobytes()


@pytest.mark.parametrize('reset', ['repeat', 'programme'])
def test_q5_preview_random_access_matches_native_production_and_preserves_clocks(tmp_path, reset):
    path, data = feedback_project(tmp_path, reset=reset)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    project = Project.load(path)
    assert project.frame_count == 24
    native, renderer = Compositor(project), renderer_for(path, data, tmp_path)
    expected = {}
    try:
        for frame in range(project.frame_count):
            expected[frame] = native.frame(frame)[0].convert('RGBA').tobytes()
        assert len(set(expected.values())) > 6
        for frame in (23, 6, 5, 7, 18, 17, 19, 0, 9, 22, 3, 11, 12, 23):
            assert pixels(renderer(frame, 'Q5')) == expected[frame]
            view = renderer.compositor.project
            assert (view.fps, view.frame_count, view.start_sample, view.end_sample, view.repeats) == (
                project.fps, project.frame_count, project.start_sample, project.end_sample, project.repeats)
            assert view.source_time(frame) == project.source_time(frame)
            assert view.continuous_beat(frame) == project.continuous_beat(frame)
            status = renderer.stateful_status
            assert status['mode'] == 'checkpoint_replay' and status['phase'] == 'ready'
            assert status['requested_frame'] == frame and 0 < status['current_request_frames'] <= 24
            assert status['checkpoint_restore'] and not status['production_equivalent']
    finally:
        renderer.close()
        native.close()
    assert not list((tmp_path / 'native').iterdir())
    assert {name: (tmp_path / name).read_bytes() for name in before} == before


def test_feedback_admission_requires_authored_q5_and_does_not_change_project(tmp_path):
    path, data = feedback_project(tmp_path)
    project = Project.load(path)
    assert not any(row['supported'] for row in profile_capabilities(project))
    assert [row['supported'] for row in profile_capabilities(project, allow_stateful=True)] == [
        False, False, False, False, False, True]
    assert profile_document(project, 'Q5', allow_stateful=True) == data
    renderer = renderer_for(path, data, tmp_path)
    try:
        with pytest.raises(ProfileUnsupported, match='authored Q5'):
            renderer(4, 'Q4')
        assert renderer.compositor is None
        assert pixels(renderer(4, 'Q5'))
        assert project.data == data
    finally:
        renderer.close()


def test_cancelled_reconstruction_keeps_committed_checkpoint_and_can_resume(tmp_path):
    path, data = feedback_project(tmp_path)
    renderer = renderer_for(path, data, tmp_path)
    reference = Compositor(Project.load(path))
    updates = []
    cancel = [False]

    def progress(status):
        updates.append(status)
        if status['phase'] == 'reconstructing' and status['current_request_frames'] >= 2:
            cancel[0] = True

    try:
        renderer(7, 'Q5')
        checkpoint = renderer.compositor.feedback_checkpoint()
        with pytest.raises(RefinementCancelled, match='superseded native feedback'):
            renderer.render_cancellable(19, 'Q5', lambda: cancel[0], progress)
        assert renderer.compositor.feedback_checkpoint().digest == checkpoint.digest
        assert renderer.stateful_status['phase'] == 'cancelled'
        assert updates and updates[-1]['phase'] == 'cancelled'
        assert all(not any(isinstance(value, bytes) for value in row.values()) for row in updates)
        assert pixels(renderer(10, 'Q5')) == reference.frame(10)[0].convert('RGBA').tobytes()
        assert renderer.compositor.feedback_snapshot()['last_execution']['evaluated_frames'] == [8, 9, 10]
    finally:
        renderer.close()
        reference.close()


def test_pause_closes_native_owner_and_restores_full_ring_with_less_work(tmp_path):
    path, data = feedback_project(tmp_path)
    renderer = renderer_for(path, data, tmp_path)
    reference = Compositor(Project.load(path))
    try:
        renderer(11, 'Q5')
        checkpoint = renderer.compositor.feedback_checkpoint()
        assert len(checkpoint.history) == 3
        previous_owner = renderer.compositor
        renderer.release_compositor()
        assert renderer.compositor is None and renderer.profile is None
        assert renderer._feedback_resume[1].digest == checkpoint.digest
        assert pixels(renderer(12, 'Q5')) == reference.frame(12)[0].convert('RGBA').tobytes()
        assert renderer.compositor is not previous_owner and renderer._feedback_resume is None
        execution = renderer.compositor.feedback_snapshot()['last_execution']
        assert execution['origin'] == 'checkpoint' and execution['evaluated_frames'] == [12]
        assert renderer.stateful_status['restored_from_frame'] == 12
        renderer.release_compositor()
        assert renderer._feedback_resume is not None
    finally:
        renderer.close()
        reference.close()
    assert renderer._feedback_resume is None and not list((tmp_path / 'native').iterdir())


def test_request_budget_is_fixed_diagnostic_and_preserves_recoverable_state(tmp_path):
    path, data = feedback_project(tmp_path, max_replay_frames=4)
    renderer = renderer_for(path, data, tmp_path)
    try:
        renderer(3, 'Q5')
        checkpoint = renderer.compositor.feedback_checkpoint()
        with pytest.raises(StatefulSeekUnavailable) as failure:
            renderer(20, 'Q5')
        assert failure.value.reason_code == 'replay_budget_exceeded'
        assert renderer.stateful_status['phase'] == 'unavailable'
        assert renderer.stateful_status['last_reason'] == 'replay_budget_exceeded'
        assert renderer.compositor.feedback_checkpoint().digest == checkpoint.digest
        assert pixels(renderer(4, 'Q5'))
        assert renderer.stateful_status['phase'] == 'ready'
        assert renderer.stateful_status['last_reason'] is None
    finally:
        renderer.close()


@pytest.mark.parametrize('when', ['before', 'paused', 'release', 'release_suspended', 'during', 'after'])
def test_changed_source_retires_native_checkpoint_and_reports_sticky_identity_failure(tmp_path, when):
    path, data = feedback_project(tmp_path)
    renderer = renderer_for(path, data, tmp_path)
    changed = [False]

    def mutate():
        (tmp_path / 'source.png').write_bytes(b'changed pinned source')
        changed[0] = True

    def progress(status):
        if when == 'during' and not changed[0] and status['current_request_frames'] == 1:
            mutate()

    try:
        renderer(7, 'Q5')
        if when in ('paused', 'release_suspended'):
            renderer.release_compositor()
            assert renderer._feedback_resume is not None
        if when in ('before', 'paused', 'release', 'release_suspended'):
            mutate()
        if when == 'after':
            checks = [0]

            def verifier():
                checks[0] += 1
                if checks[0] == 2:
                    mutate()
                renderer.project.verify_inputs()

            renderer.verifier = verifier
        with pytest.raises(StatefulSeekUnavailable) as failure:
            if when in ('release', 'release_suspended'):
                renderer.release_compositor()
            else:
                renderer.render_cancellable(10, 'Q5', lambda: False, progress)
        assert failure.value.reason_code == 'input_identity_changed'
        assert renderer.compositor is None and renderer._feedback_resume is None
        assert renderer.stateful_status['phase'] == 'unavailable'
        assert renderer.stateful_status['checkpoint_count'] == 0
        assert renderer.stateful_status['last_reason'] == 'input_identity_changed'
        with pytest.raises(StatefulSeekUnavailable) as repeated:
            renderer(0, 'Q5')
        assert repeated.value.reason_code == 'input_identity_changed'
    finally:
        renderer.close()


@pytest.mark.parametrize('reset,expected', [('repeat', (6, 12, 18)), ('programme', ())])
def test_dependency_closures_include_feedback_history_and_fractional_forward_reads(tmp_path, reset, expected):
    _path, data = feedback_project(tmp_path, reset=reset)
    reach = temporal_reach(data)
    assert (reach.mode, reach.past_frames, reach.future_frames, reach.reset_frames) == (
        'feedback', 0, 1, expected)
    section = SimpleNamespace(start_frame=13, end_frame_exclusive=16)
    assert reach.reads(section, 24) == ((12 if reset == 'repeat' else 0), 17)
    status = seek_status(data)
    assert status['mode'] == 'checkpoint_replay' and status['checkpoint_restore']
    external = deepcopy(data)
    external['effects'] = [{}]
    assert temporal_reach(external).reset_frames == ()
    assert seek_status(external)['mode'] == 'verified_prefix_replay'
    assert not seek_status(external)['checkpoint_restore']


def test_rational_repeat_resets_are_exact_and_large_ledger_is_conservative(tmp_path):
    _path, data = feedback_project(tmp_path, reset='repeat')
    data['audio']['end_sample'] = 19600  # 49/10 encoded frames per repeat.
    data['audio']['repeats'] = 5
    assert temporal_reach(data).reset_frames == (5, 10, 15, 20)
    data['audio']['repeats'] = 8193
    assert temporal_reach(data).reset_frames == ()
    data['audio']['repeats'] = 4
    data['audio']['end_sample'] = 2000  # Half a frame; duplicate reset boundaries collapse.
    assert temporal_reach(data).reset_frames == (1,)


def test_actual_adaptive_stream_publishes_q5_pixels_and_checkpoint_progress(tmp_path):
    path, data = feedback_project(tmp_path)
    reference = Compositor(Project.load(path))
    api = PreviewStreamAPI(path, lambda _a, _b: None,
                           lambda *_a, **_k: pytest.fail('legacy renderer selected'), cache_root=tmp_path / 'cache')
    try:
        reply = api.open({'project': data, 'adaptive': True, 'frame': 11})
        stream = api.get(reply['session'])
        first = ready(stream, 0)
        assert first['profile_id'] == 'Q5'
        assert pixels(stream.image(11, first['generation'], first['image_sha256'])) == (
            reference.frame(11)[0].convert('RGBA').tobytes())
        assert isinstance(stream.renderer, NativePreviewRenderer)
        assert stream.stateful['mode'] == 'checkpoint_replay'
        assert stream.stateful['phase'] == 'ready'
        assert stream.stateful['checkpoint_count'] <= 2
        api.command({'session': reply['session'], 'command': 'seek', 'sequence': 1, 'frame': 12})
        following = ready(stream, 1)
        assert pixels(stream.image(12, following['generation'], following['image_sha256'])) == (
            reference.frame(12)[0].convert('RGBA').tobytes())
        assert stream.stateful['current_request_frames'] == 1
        assert stream.stateful['live_next_frame'] == 13
        assert any(row['type'] == 'stateful-progress' for row in stream.since(0))
    finally:
        api.shutdown()
        reference.close()


def test_inspection_and_cli_expose_bounded_reconstruction_without_pixels(tmp_path, monkeypatch, capsys):
    path, _data = feedback_project(tmp_path, reset='repeat')
    before = path.read_bytes()

    def forbidden(*_args, **_kwargs):
        pytest.fail('planning must not render')

    monkeypatch.setattr(Compositor, '_native_frame', forbidden)
    report = inspect_temporal(path, [11, 6, 11])
    assert [row['frame'] for row in report['frames']] == [11, 6, 11]
    assert report['feedback']['scope']['checkpointed_state'] == 'owned_rgb8_feedback_history'
    assert report['frames'][0]['feedback']['start_frame'] == 6
    assert report['frames'][0]['feedback']['steps'] == 6
    assert report['frames'][1]['feedback']['steps'] == 1
    assert report['frames'][0] == report['frames'][2]
    assert report['scope']['pixels_rendered'] is False
    assert str(tmp_path) not in json.dumps(report)
    plan = tmp_path / 'plan.json'
    assert main(['plan', str(path), '--output', str(plan)]) == 0
    capsys.readouterr()
    assert json.loads(plan.read_text())['feedback'] == report['feedback']
    assert path.read_bytes() == before
