"""M5.4 integration with a real generated, source-clocked FrameRudiments project."""
from __future__ import annotations

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
        pytest.skip('Installed TTF required for compositor integration')
    path = create_demo(tmp_path / 'chain-demo', font, width=180, height=320)
    original = Project.load(path)
    cfg = copy.deepcopy(original.data)
    cfg['render_quality'] = {'enabled': False}
    return Project(cfg, original.base)


def configured(project, controls=()):
    cfg = copy.deepcopy(project.data)
    cfg['effect_graph'] = {'nodes': [
        {'id': 'background', 'type': 'source'},
        {'id': 'layer:native', 'type': 'source'},
        {'id': 'layer:bands', 'type': 'source'},
        {'id': 'sequence', 'type': 'transition_chain',
         'inputs': ['background', 'layer:native', 'layer:bands'],
         'input_effects': [[], [{'id': 'dim', 'type': 'opacity', 'opacity': .85}], []],
         'stages': [
             {'id': 'intro', 'transition_style': 'wipe', 'progress': .1, 'feather': .2},
             {'id': 'outro', 'transition_style': 'dissolve', 'progress': .2},
         ]},
    ], 'output': 'sequence'}
    cfg['effect_graph_controls'] = list(controls)
    return Project(cfg, project.base)


def test_real_project_chained_layers_render_random_access(demo_project):
    compositor = Compositor(configured(demo_project))
    a, _ = compositor.frame(12, apply_quality=False)
    compositor.frame(40, apply_quality=False)
    repeated, _ = compositor.frame(12, apply_quality=False)
    assert repeated.tobytes() == a.tobytes()
    assert a.size == (180, 320)
    assert len(a.getcolors(maxcolors=100000)) > 1
    assert compositor.effect_graph.chain_expansions['sequence']['stages']['outro'] == 'sequence::stage::outro'


def test_native_dancerudiment_controls_independent_transition_stages(demo_project):
    controls = [
        {'id': 'kick-intro', 'node': 'sequence::stage::intro', 'property': 'progress',
         'motion': {'kind': 'named', 'pattern': 'bounce'}, 'axis': 'x', 'range': [0, 1]},
        {'id': 'bass-outro', 'node': 'sequence::stage::outro', 'property': 'progress',
         'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'y', 'range': [0, 1]},
    ]
    project = configured(demo_project, controls)
    animated = Compositor(project)
    static = Compositor(configured(demo_project))
    assert len(animated.effect_graph_controls.bindings) == 2
    frame = 45
    native = animated.effect_graph_controls.values(animated.context(frame), animated.effect_graph)
    assert ('sequence::stage::intro', 'progress') in native
    assert ('sequence::stage::outro', 'progress') in native
    assert all(0 <= value <= 1 for value in native.values())
    original, _ = static.frame(frame, apply_quality=False)
    changed, _ = animated.frame(frame, apply_quality=False)
    assert changed.tobytes() != original.tobytes()
    animated.frame(12, apply_quality=False)
    assert animated.frame(frame, apply_quality=False)[0].tobytes() == changed.tobytes()
    assert animated.effect_graph.nodes['sequence::stage::intro'].config['progress'] == .1


def test_non_graph_project_retains_identical_legacy_render(demo_project):
    legacy = Compositor(demo_project)
    no_graph, _ = legacy.frame(30, apply_quality=False)
    repeat, _ = Compositor(Project(copy.deepcopy(demo_project.data), demo_project.base)).frame(
        30, apply_quality=False)
    assert no_graph.tobytes() == repeat.tobytes()
