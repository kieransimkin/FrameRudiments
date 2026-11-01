"""Full-package M6 integration: real Project/Compositor/native motion, no stand-ins."""
from copy import deepcopy
import os
from pathlib import Path

from PIL import ImageDraw
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def project(tmp_path):
    font = os.environ.get('FRAMERUDIMENTS_TEST_FONT')
    if not font:
        font = next((str(p) for p in (Path('C:/Windows/Fonts/arial.ttf'),
                     Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')) if p.is_file()), None)
    if not font or not Path(font).is_file():
        pytest.skip('An installed TTF is required for real-project M6 tests')
    file = create_demo(tmp_path / 'styles', font, width=180, height=320)
    original = Project.load(file)
    data = deepcopy(original.data)
    data['render_quality'] = {'enabled': False}
    return Project(data, original.base)


def configured(project, *, provider='layer:native', controls=None, **settings):
    data = deepcopy(project.data)
    data['effect_graph'] = {'nodes': [
        {'id': provider, 'type': 'source'},
        {'id': 'styled', 'type': 'layer_style', 'inputs': [provider],
         'style_type': 'outline', 'style_color': '#00ffff', 'style_width': 2, **settings},
    ], 'output': 'styled'}
    if controls is not None:
        data['effect_graph_controls'] = controls
    return Project(data, project.base)


@pytest.mark.parametrize('provider', ['layer:native', 'layer:bands', 'identity:0', 'caption'])
def test_same_layer_style_operates_on_sprite_bars_and_text(project, provider):
    plain = Compositor(configured(project, provider=provider, style_strength=0))
    styled = Compositor(configured(project, provider=provider))
    try:
        a = plain.frame(45, apply_quality=False)[0]
        b = styled.frame(45, apply_quality=False)[0]
        assert a.mode == b.mode == 'RGB' and b.size == (180, 320)
        assert a.tobytes() != b.tobytes()
        styled.frame(12, apply_quality=False)
        assert styled.frame(45, apply_quality=False)[0].tobytes() == b.tobytes()
    finally:
        plain.close()
        styled.close()


def test_real_native_control_changes_style_at_the_same_source_frame(project):
    controls = [{'id': 'edge-width', 'node': 'styled', 'property': 'style_width',
                 'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': [1, 5]}]
    plain = Compositor(configured(project, style_width=0))
    driven = Compositor(configured(project, style_width=0, controls=controls))
    try:
        ctx = driven.context(45)
        resolved = driven.effect_graph_controls.values(ctx, driven.effect_graph)
        assert 1 <= resolved[('styled', 'style_width')] <= 5
        baseline = plain.frame(45, apply_quality=False)[0]
        rendered = driven.frame(45, apply_quality=False)[0]
        assert rendered.tobytes() != baseline.tobytes()
        driven.frame(12, apply_quality=False)
        assert driven.frame(45, apply_quality=False)[0].tobytes() == rendered.tobytes()
        assert driven.effect_graph.nodes['styled'].config['style_width'] == 0
    finally:
        plain.close()
        driven.close()


def test_native_colour_offset_and_categorical_controls_use_existing_adapter(project):
    controls = [
        {'node': 'styled', 'property': 'style_color.r', 'motion': {'kind': 'named', 'pattern': 'circle'},
         'axis': 'x', 'range': [10, 240]},
        {'node': 'styled', 'property': 'style_offset.x', 'motion': {'kind': 'named', 'pattern': 'circle'},
         'axis': 'y', 'range': [-8, 8]},
        {'node': 'styled', 'property': 'style_type', 'motion': {'kind': 'named', 'pattern': 'circle'},
         'axis': 'x', 'choices': ['drop_shadow', 'inner_shadow']},
    ]
    comp = Compositor(configured(project, controls=controls, style_output='effect'))
    try:
        a = comp.frame(12, apply_quality=False)[0]
        comp.frame(45, apply_quality=False)
        assert comp.frame(12, apply_quality=False)[0].tobytes() == a.tobytes()
        assert comp.effect_graph.nodes['styled'].config['style_color'] == '#00ffff'
    finally:
        comp.close()


def test_styles_in_custom_scene_are_isolated_and_empty_scene_has_no_halo(project):
    def scene(surface, ctx):
        if ctx.frame < 30:
            ImageDraw.Draw(surface).rectangle((30, 40, 60, 70), fill=(250, 40, 20, 180))
        return surface
    comp = Compositor(configured(project, provider='scene', style_type='outer_glow',
                                  style_spread=3), scene=scene)
    try:
        first = comp.frame(12, apply_quality=False)[0]
        blank = comp.frame(45, apply_quality=False)[0]
        assert first.getbbox() is not None
        assert blank.getbbox() is None
        assert comp.frame(12, apply_quality=False)[0].tobytes() == first.tobytes()
    finally:
        comp.close()


def test_temporal_supersampling_and_source_evidence_survive_style_node(project):
    p = configured(project, style_type='inner_glow', style_spread=2, style_blur=1)
    data = deepcopy(p.data)
    data['render_quality'] = {'enabled': True, 'spatial_scale': 1, 'temporal_samples': 2, 'antialias': 0}
    comp = Compositor(Project(data, p.base))
    try:
        first = comp.frame(15)[0]
        comp.frame(30)
        assert comp.frame(15)[0].tobytes() == first.tobytes()
        assert comp.project.summary()['effect_graph']['nodes'][1]['style_type'] == 'inner_glow'
    finally:
        comp.close()
