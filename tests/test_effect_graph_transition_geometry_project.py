"""End-to-end M5.3 compositor test using generated, hash-pinned demo media."""
import copy
import os
from pathlib import Path

import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def scene(tmp_path):
    font = os.getenv('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        font = next((str(p) for p in (
            Path('C:/Windows/Fonts/arial.ttf'),
            Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        ) if p.is_file()), None)
    if not font:
        pytest.skip('Installed TTF font required for actual compositor integration')
    project = Project.load(create_demo(tmp_path / 'geom-demo', font, width=180, height=320))
    data = copy.deepcopy(project.data)
    data['render_quality'] = {'enabled': False}
    return Project(data, project.base)


def configured(project, style, controls=()):
    data = copy.deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'layer:native', 'type': 'source'},
        {'id': 'layer:bands', 'type': 'source'},
        {'id': 'geom', 'type': 'transition', 'inputs': ['layer:native', 'layer:bands'],
         'transition_style': style, 'progress': .4, 'feather': .25},
    ], 'output': 'geom'}
    data['effect_graph_controls'] = list(controls)
    return Project(data, project.base)


@pytest.mark.parametrize('style', ['zoom', 'rotate', 'polar', 'kaleidoscope', 'liquid'])
def test_actual_project_render_random_access_and_original_sources(scene, style):
    compositor = Compositor(configured(scene, style))
    first, _ = compositor.frame(42, apply_quality=False)
    compositor.frame(100, apply_quality=False)
    second, _ = compositor.frame(42, apply_quality=False)
    assert first.size == (180, 320)
    assert first.mode == 'RGB'
    assert first.tobytes() == second.tobytes()
    compositor.close()


def test_named_dancerudiment_controls_drive_liquid_warp_and_progress(scene):
    controls = [
        {'id': 'progress', 'node': 'geom', 'property': 'progress',
         'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': [0, 1]},
        {'id': 'wave-phase', 'node': 'geom', 'property': 'phase',
         'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'y', 'range': [-3, 3]},
        {'id': 'wave-amplitude', 'node': 'geom', 'property': 'amplitude',
         'motion': {'kind': 'named', 'pattern': 'bounce'}, 'axis': 'x', 'range': [4, 48]},
    ]
    comp = Compositor(configured(scene, 'liquid', controls))
    early, _ = comp.frame(12, apply_quality=False)
    late, _ = comp.frame(47, apply_quality=False)
    assert early.tobytes() != late.tobytes()
    assert comp.frame(12, apply_quality=False)[0].tobytes() == early.tobytes()
    assert comp.effect_graph.nodes['geom'].config['progress'] == .4
    at_early = comp.effect_graph_controls.values(comp.context(12), comp.effect_graph)
    at_late = comp.effect_graph_controls.values(comp.context(47), comp.effect_graph)
    assert at_early != at_late
    assert {('geom', 'progress'), ('geom', 'phase'), ('geom', 'amplitude')} <= set(at_early)
    comp.close()
