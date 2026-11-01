"""Real Project/Compositor/native DanceRudiment acceptance for M6.3 (no stubs)."""
from copy import deepcopy
import os
from pathlib import Path

import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def project(tmp_path):
    font = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        for path in (Path('C:/Windows/Fonts/arial.ttf'),
                     Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')):
            if path.is_file():
                font = str(path)
                break
    if not font or not Path(font).is_file():
        pytest.skip('Installed TTF required for native compositor acceptance')
    file = create_demo(tmp_path / 'relief-demo', font, width=180, height=320)
    original = Project.load(file)
    data = deepcopy(original.data)
    data['render_quality'] = {'enabled': False}
    return Project(data, original.base)


def make_project(project, style, controls=(), **extra):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'layer:native', 'type': 'source'},
        {'id': 'body', 'type': 'layer_style', 'inputs': ['layer:native'],
         'style_type': style, 'style_color': '#ff8030', 'light_elevation': 0, **extra},
    ], 'output': 'body'}
    data['effect_graph_controls'] = list(controls)
    return Project(data, project.base)


@pytest.mark.parametrize('style', ['bevel', 'emboss', 'edge_light', 'satin', 'extrusion', 'long_shadow'])
def test_native_compositor_renders_each_style_and_is_seekable(project, style):
    comp = Compositor(make_project(project, style))
    try:
        first = comp.frame(27, apply_quality=False)[0]
        comp.frame(60, apply_quality=False)
        assert comp.frame(27, apply_quality=False)[0].tobytes() == first.tobytes()
        assert first.size == (180, 320)
        assert first.getbbox() is not None
    finally:
        comp.close()


@pytest.mark.parametrize('style,prop,limits', [
    ('bevel', 'light_angle', [0, 360]),
    ('emboss', 'bevel_depth', [1, 10]),
    ('extrusion', 'extrude_length', [2, 24]),
    ('long_shadow', 'extrude_angle', [0, 180]),
])
def test_real_native_motion_matches_explicit_resolved_control(project, style, prop, limits):
    control = {'id': 'native-style', 'node': 'body', 'property': prop,
               'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': limits}
    configured = make_project(project, style, [control])
    animated = Compositor(configured)
    try:
        observed = []
        # Compare each native result with an independently constructed static graph
        # using that frame's resolved parameter, not a guessed phase inequality.
        for frame in (7, 23, 41):
            value = animated.effect_graph_controls.values(animated.context(frame), animated.effect_graph)[('body', prop)]
            observed.append(value)
            static_data = deepcopy(configured.data)
            static_data['effect_graph_controls'] = []
            static_data['effect_graph']['nodes'][-1][prop] = value
            static = Compositor(Project(static_data, project.base))
            try:
                expected = static.frame(frame, apply_quality=False)[0]
                actual = animated.frame(frame, apply_quality=False)[0]
                assert actual.tobytes() == expected.tobytes()
            finally:
                static.close()
        assert max(observed) > min(observed)
    finally:
        animated.close()


def test_native_categorical_style_control_uses_existing_choice_driver(project):
    control = {'id': 'native-choice', 'node': 'body', 'property': 'style_type',
               'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x',
               'choices': ['bevel', 'extrusion']}
    comp = Compositor(make_project(project, 'bevel', [control]))
    try:
        values = [comp.effect_graph_controls.values(comp.context(frame), comp.effect_graph)[('body', 'style_type')]
                  for frame in range(0, 60, 3)]
        assert set(values).issubset({'bevel', 'extrusion'})
        expected = make_project(project, values[0])
        static = Compositor(expected)
        try:
            assert comp.frame(0, apply_quality=False)[0].tobytes() == static.frame(0, apply_quality=False)[0].tobytes()
        finally:
            static.close()
        comp.frame(17, apply_quality=False)
        assert comp.effect_graph.nodes['body'].config['style_type'] == 'bevel'
    finally:
        comp.close()
