"""Real Project/Compositor/native DanceRudiment acceptance; no substitute driver."""
from copy import deepcopy
import os
from pathlib import Path

from PIL import ImageDraw
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


# At 120 BPM/60 fps, multiples of 15 frames repeatedly sample circle's
# zero crossings. Include sub-beat phases; do not change native motion to
# make an aliased test fixture appear animated.
SAMPLE_FRAMES = (0, 1, 4, 9, 13, 22, 37, 53)


@pytest.fixture
def project(tmp_path):
    font = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        for candidate in ('C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
            if Path(candidate).is_file():
                font = candidate
                break
    if not font or not Path(font).is_file():
        pytest.skip('Installed TTF required for real compositor acceptance')
    loaded = Project.load(create_demo(tmp_path/'scene', font, width=180, height=320))
    data = deepcopy(loaded.data)
    data.update(layers=[], identity=[], render_quality={'enabled': False}, background={'colour': '#6080a0'})
    data.pop('captions', None)
    return Project(data, loaded.base)


def configured(project, style, controls=()):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'background', 'type': 'source'},
        {'id': 'paint', 'type': 'layer_style', 'inputs': ['background'], **style},
    ], 'output': 'paint'}
    if controls:
        data['effect_graph_controls'] = list(controls)
    return Project(data, project.base)


@pytest.mark.parametrize('space', ['srgb', 'linear_srgb', 'oklab'])
def test_native_gradient_phase_is_reproducible_and_does_not_modify_project(project, space):
    binding = dict(id='phase', node='paint', property='gradient_phase',
                   motion={'kind': 'named', 'pattern': 'circle'}, axis='x', range=[0, 1])
    authored = configured(project, {'style_type': 'gradient_overlay', 'gradient_space': space,
                                   'gradient_repeat': 'repeat'}, [binding])
    saved = deepcopy(authored.data)
    comp = Compositor(authored)
    try:
        frames = SAMPLE_FRAMES
        resolved = [comp.effect_graph_controls.values(comp.context(frame), comp.effect_graph)[
            ('paint', 'gradient_phase')] for frame in frames]
        assert max(resolved) > min(resolved)
        images = {frame: comp.frame(frame, apply_quality=False)[0].tobytes() for frame in frames}
        assert len(set(images.values())) > 1
        for frame in reversed(frames):
            assert comp.frame(frame, apply_quality=False)[0].tobytes() == images[frame]
        assert authored.data == saved
    finally:
        comp.close()


def test_native_stop_colour_component_binding_is_not_a_stub(project):
    controls = [dict(node='paint', property='gradient_stops.start.color.r',
                     motion={'kind': 'named', 'pattern': 'circle'}, axis='x', range=[0, 255])]
    comp = Compositor(configured(project, {'style_type': 'gradient_overlay'}, controls))
    try:
        values, images = [], []
        for frame in SAMPLE_FRAMES:
            values.append(comp.effect_graph_controls.values(comp.context(frame), comp.effect_graph)[
                ('paint', 'gradient_stops.start.color.r')])
            images.append(comp.frame(frame, apply_quality=False)[0].tobytes())
        assert max(values) > min(values)
        assert len(set(images)) > 1
        assert 'gradient_stops' not in comp.effect_graph.nodes['paint'].config
    finally:
        comp.close()


def test_native_categorical_blend_binding_uses_both_choices(project):
    controls = [dict(node='paint', property='style_blend', choices=['normal', 'multiply'],
                     motion={'kind': 'named', 'pattern': 'circle'}, axis='x')]
    comp = Compositor(configured(project, {'style_type': 'color_overlay', 'style_color': '#80ff40'}, controls))
    try:
        selected, pixels = set(), set()
        for frame in range(0, 220, 10):
            selected.add(comp.effect_graph_controls.values(comp.context(frame), comp.effect_graph)[('paint', 'style_blend')])
            pixels.add(comp.frame(frame, apply_quality=False)[0].getpixel((90, 160)))
        assert selected == {'normal', 'multiply'}
        assert len(pixels) == 2
    finally:
        comp.close()


def test_texture_from_real_scene_graph_provider(project):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'background', 'type': 'source'}, {'id': 'scene', 'type': 'source'},
        {'id': 'paint', 'type': 'layer_style', 'style_type': 'pattern_overlay',
         'pattern_kind': 'texture', 'pattern_scale': [18, 32], 'inputs': ['background', 'scene']},
    ], 'output': 'paint'}

    def scene(canvas, context):
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 89, 319), fill=(255, 30, 60, 255))
        draw.rectangle((90, 0, 179, 319), fill=(20, 100, 255, 128))
        return canvas

    comp = Compositor(Project(data, project.base), scene=scene)
    try:
        image, _ = comp.frame(15, apply_quality=False)
        assert image.getpixel((2, 10)) != image.getpixel((12, 10))
        assert comp.frame(15, apply_quality=False)[0].tobytes() == image.tobytes()
    finally:
        comp.close()


def test_transition_chain_preprocessing_uses_native_overlay_controls(project):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'background', 'type': 'source'},
        {'id': 'chain', 'type': 'transition_chain', 'inputs': ['background', 'background'],
         'input_effects': [[{'id': 'paint', 'type': 'layer_style', 'style_type': 'pattern_overlay',
                            'pattern_kind': 'stripes', 'pattern_scale': [12, 20]}], []],
         'stages': [{'id': 'fade', 'progress': 0}]},
    ], 'output': 'chain'}
    data['effect_graph_controls'] = [dict(node='chain::input::0::paint', property='pattern_offset.x',
                                        motion={'kind': 'named', 'pattern': 'circle'}, axis='x', range=[0, 12])]
    comp = Compositor(Project(data, project.base))
    try:
        resolved = [comp.effect_graph_controls.values(comp.context(f), comp.effect_graph)[
            ('chain::input::0::paint', 'pattern_offset.x')] for f in SAMPLE_FRAMES]
        assert max(resolved) > min(resolved)
        images = [comp.frame(f, apply_quality=False)[0].tobytes() for f in SAMPLE_FRAMES]
        assert len(set(images)) > 1
    finally:
        comp.close()


def test_bad_stop_order_fails_before_project_render(project):
    cfg = {'style_type': 'gradient_overlay', 'gradient_stops': [
        {'id': 'a', 'position': .8, 'color': 'red'}, {'id': 'b', 'position': .2, 'color': 'blue'}]}
    with pytest.raises(ValueError, match='increasing'):
        Compositor(configured(project, cfg))


def test_disabling_overlay_strength_matches_unstyled_background(project):
    plain, styled = Compositor(project), Compositor(configured(project, {'style_type': 'color_overlay', 'style_strength': 0}))
    try:
        assert styled.frame(30, apply_quality=False)[0].tobytes() == plain.frame(30, apply_quality=False)[0].tobytes()
    finally:
        plain.close()
        styled.close()
