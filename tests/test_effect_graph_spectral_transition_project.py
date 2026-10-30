"""M5.2: native motion and compositor integration using hash-pinned demo assets."""
import copy
import os
from pathlib import Path

import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def demo_project(tmp_path):
    font = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        for candidate in (Path('C:/Windows/Fonts/arial.ttf'),
                          Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')):
            if candidate.is_file():
                font = str(candidate)
                break
    if not font or not Path(font).is_file():
        pytest.skip('Installed TTF needed to create the pinned demo project')
    path = create_demo(tmp_path / 'm5-2-demo', font, width=180, height=320)
    raw = Project.load(path)
    config = copy.deepcopy(raw.data)
    config['render_quality'] = {'enabled': False}
    return Project(config, raw.base)


def project_with_spectral(demo, controls=()):
    config = copy.deepcopy(demo.data)
    config['effect_graph'] = {'nodes': [
        {'id': 'layer:native', 'type': 'source'},
        {'id': 'frame', 'type': 'source'},
        {'id': 'spectral', 'type': 'transition', 'transition_style': 'spectral',
         'inputs': ['layer:native', 'frame'], 'progress': .45,
         'bands': [
             {'id': 'cyan', 'source_color': '#76d4cf', 'to_color': '#76d4cf',
              'space': 'rgb', 'tolerance': .2, 'softness': .1,
              'start': 0, 'end': .6, 'offset_from': [2, 0], 'offset_to': [-2, 1]},
         ]},
    ], 'output': 'spectral'}
    if controls:
        config['effect_graph_controls'] = list(controls)
    return Project(config, demo.base)


def test_actual_project_spectral_transition_is_seekable(demo_project):
    comp = Compositor(project_with_spectral(demo_project))
    first, _ = comp.frame(42, apply_quality=False)
    comp.frame(90, apply_quality=False)
    again, _ = comp.frame(42, apply_quality=False)
    assert first.size == (180, 320) and first.mode == 'RGB'
    assert first.tobytes() == again.tobytes()
    assert comp.effect_graph.nodes['spectral'].config['progress'] == .45
    comp.close()


def test_native_motion_modulates_band_offset_and_progress(demo_project):
    controls = [
        {'id': 'bass-phase', 'node': 'spectral', 'property': 'progress',
         'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': [0, 1]},
        {'id': 'colour-drift', 'node': 'spectral', 'property': 'bands.cyan.offset_to.x',
         'motion': {'kind': 'named', 'pattern': 'bounce'}, 'axis': 'y', 'range': [-12, 12]},
    ]
    comp = Compositor(project_with_spectral(demo_project, controls))
    early, _ = comp.frame(25, apply_quality=False)
    late, _ = comp.frame(65, apply_quality=False)
    comp.frame(15, apply_quality=False)
    assert comp.frame(25, apply_quality=False)[0].tobytes() == early.tobytes()
    assert early.tobytes() != late.tobytes()
    assert comp.effect_graph.nodes['spectral'].config['bands'][0]['offset_to'] == [-2, 1]
    comp.close()


def test_invalid_spectral_band_target_rejected_before_render(demo_project):
    control = {'id': 'invalid', 'node': 'spectral', 'property': 'bands.missing.start',
               'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': [0, 1]}
    with pytest.raises(ValueError, match='control target'):
        Compositor(project_with_spectral(demo_project, [control]))
