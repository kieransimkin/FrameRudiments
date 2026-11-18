"""Actual native planning/CLI boundaries, without raster or GPU callbacks."""
from hashlib import sha256
import json

import pytest

from framerudiments.cli import main
from framerudiments.compositor import Compositor
from framerudiments.preview_seek_support import seek_status, temporal_reach
from framerudiments.temporal_inspection import inspect_temporal, write_inspection
from test_preview_native_project import fixture


def project_file(tmp_path):
    path, data = fixture(tmp_path)
    data['render_quality'] = {'spatial_scale': 1, 'temporal_samples': 4}
    data['post_effects'] = {'temporal': {'strength': 1, 'shutter_frames': 8, 'samples': 9}}
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def test_real_inspection_is_repeatable_ordered_and_does_not_render_or_open_gpu(tmp_path, monkeypatch):
    from framerudiments import compositor as compositor_module
    from framerudiments import super_sampling

    path = project_file(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    closed = []
    original_close = Compositor.close

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Inspection must not render or create a GPU/virtual compositor')

    def close(self):
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(Compositor, 'frame', forbidden)
    monkeypatch.setattr(compositor_module, 'create_gpu_backend', forbidden)
    monkeypatch.setattr(super_sampling, '_virtual_project', forbidden)
    monkeypatch.setattr(Compositor, 'close', close)
    report = inspect_temporal(path, [6, 1, 6])
    assert report == inspect_temporal(path, [6, 1, 6])
    assert len(closed) == 2
    assert report['schema'] == 'framerudiments.temporal-inspection.v1'
    assert report['project_sha256'] == sha256(path.read_bytes()).hexdigest()
    assert [row['frame'] for row in report['frames']] == [6, 1, 6]
    assert report['frames'][0] == report['frames'][2]
    assert report['scope'] == {'pixels_rendered': False, 'sequential_external_engines': False,
                               'gpu_context_created': False, 'stage': 'pre_external_effects'}
    for row in report['frames']:
        assert len(row['digest']) == 64 and row['schedule']
    assert str(tmp_path) not in json.dumps(report)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before


@pytest.mark.parametrize('frames', [[], [True], [-1], [.5], '1', list(range(65))])
def test_invalid_frame_selection_fails_before_reading_a_project(tmp_path, frames):
    with pytest.raises(ValueError, match='1..64 nonnegative integer frames'):
        inspect_temporal(tmp_path / 'not-read.json', frames)


def test_out_of_programme_frame_fails_before_compositor_creation(tmp_path, monkeypatch):
    from framerudiments import temporal_inspection

    path = project_file(tmp_path)
    monkeypatch.setattr(temporal_inspection, 'Compositor', lambda *_a, **_k: pytest.fail('created renderer'))
    with pytest.raises(ValueError, match='outside the programme'):
        inspect_temporal(path, [10_000_001])


def test_input_changed_during_planning_is_rejected_and_compositor_closed(tmp_path, monkeypatch):
    path = project_file(tmp_path)
    original_plan, original_close = Compositor.temporal_plan, Compositor.close
    closed = []

    def changed(self, frame):
        plan = original_plan(self, frame)
        (tmp_path / 'source.png').write_bytes(b'changed pinned input')
        return plan

    def close(self):
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(Compositor, 'temporal_plan', changed)
    monkeypatch.setattr(Compositor, 'close', close)
    with pytest.raises(ValueError, match='Input changed'):
        inspect_temporal(path, [0])
    assert len(closed) == 1


def test_project_changed_during_planning_is_rejected_and_compositor_closed(tmp_path, monkeypatch):
    path = project_file(tmp_path)
    original_plan, original_close = Compositor.temporal_plan, Compositor.close
    closed = []

    def changed(self, frame):
        plan = original_plan(self, frame)
        path.write_text(path.read_text() + '\n', encoding='utf-8')
        return plan

    def close(self):
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(Compositor, 'temporal_plan', changed)
    monkeypatch.setattr(Compositor, 'close', close)
    with pytest.raises(ValueError, match='Project changed'):
        inspect_temporal(path, [0])
    assert len(closed) == 1


def test_cli_stdout_and_saved_inspection_match_without_overwriting(tmp_path, capsys):
    path = project_file(tmp_path)
    args = ['inspect-temporal', str(path), '--frame', '0', '--frame', '6']
    assert main(args) == 0
    printed = json.loads(capsys.readouterr().out)
    out = tmp_path / 'inspection.json'
    assert main([*args, '--output', str(out)]) == 0
    capsys.readouterr()
    assert json.loads(out.read_text()) == printed
    existing = out.read_bytes()
    assert main([*args, '--output', str(out)]) == 2
    assert 'already exists' in capsys.readouterr().err
    assert out.read_bytes() == existing
    with pytest.raises(FileExistsError):
        write_inspection(out, {'different': True})
    assert out.read_bytes() == existing


def test_cli_plan_records_contract_and_closes_compositor(tmp_path, capsys, monkeypatch):
    path = project_file(tmp_path)
    output = tmp_path / 'plan.json'
    closed = []
    original_close = Compositor.close

    def close(self):
        closed.append(self)
        original_close(self)

    monkeypatch.setattr(Compositor, 'close', close)
    assert main(['plan', str(path), '--output', str(output)]) == 0
    capsys.readouterr()
    assert len(closed) == 1
    report = json.loads(output.read_text())
    assert report['schema'] == 'framerudiments.plan.v1'
    assert report['temporal_schedule']['max_reach']['past_frames'] == 8
    assert report['temporal_schedule']['max_reach']['future_frames'] == 1


def test_preview_closures_use_native_past_and_forward_bounds_without_inventing_feedback():
    quality = {'render_quality': {'temporal_samples': 4}}
    future = temporal_reach(quality)
    assert (future.mode, future.past_frames, future.future_frames) == ('bounded', 0, 1)
    assert seek_status(quality)['mode'] == 'independent'
    assert seek_status(quality)['future_frames'] == 1
    assert temporal_reach({'render_quality': {'enabled': False}}).mode == 'independent'
    assert temporal_reach({}).mode == seek_status({})['mode'] == 'independent'
    both = temporal_reach({**quality, 'post_effects': {'temporal': {'shutter_frames': 1}}})
    assert (both.past_frames, both.future_frames) == (8, 1)
    assert seek_status({**quality, 'post_effects': {'temporal': {}}})['mode'] == 'bounded_history'
    feedback = temporal_reach({**quality, 'effects': [{}]})
    assert feedback.mode == 'feedback' and not feedback.reset_frames
