"""M6.2 alpha-derived styles: silhouette, colour, bounds and controls."""
from copy import deepcopy

import numpy as np
from PIL import Image, ImageDraw
import pytest

from framerudiments.effect_graph_layer_styles import (
    STYLE_TYPES, render_layer_style, style_bounds, style_control_apply,
    style_control_base, style_parameter_descriptors, validate_style,
)


def source(alpha=255):
    image = Image.new('RGBA', (32, 24))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 6, 22, 18), fill=(210, 80, 30, alpha))
    draw.rectangle((12, 10, 18, 14), fill=(0, 0, 0, 0))
    return image


@pytest.mark.parametrize('style', sorted(STYLE_TYPES))
def test_empty_silhouette_never_produces_effect_from_hidden_colour(style):
    hidden = Image.new('RGBA', (20, 16), (255, 0, 255, 0))
    cfg = dict(style_type=style, style_spread=3, style_blur=2, style_output='effect')
    assert render_layer_style(hidden, cfg).getchannel('A').getbbox() is None


@pytest.mark.parametrize('style', sorted(STYLE_TYPES))
def test_source_is_immutable_and_zero_strength_is_exact_bypass(style):
    image = source(180)
    before = image.tobytes()
    cfg = dict(style_type=style, style_strength=0, style_offset=[3.25, -1.5])
    config_before = deepcopy(cfg)
    result = render_layer_style(image, cfg)
    assert result is not image and result.tobytes() == before
    assert cfg == config_before and image.tobytes() == before


def test_drop_shadow_uses_holes_and_offset_not_rectangle():
    image = source(180)
    cfg = dict(style_type='drop_shadow', style_offset=[3, 2], style_color='#102030', style_output='effect')
    result = render_layer_style(image, cfg)
    assert result.getpixel((11, 8)) == (16, 32, 48, 180)
    assert result.getpixel((18, 14))[3] == 0  # the shifted hole
    assert result.getpixel((6, 5))[3] == 0
    assert result.getchannel('A').getbbox() == (11, 8, 26, 21)


def test_shadow_fractional_offsets_and_strength():
    image = Image.new('RGBA', (6, 6))
    image.putpixel((2, 2), (9, 18, 27, 200))
    out = render_layer_style(image, dict(style_output='effect', style_offset=[.5, .5], style_strength=.5))
    assert [out.getpixel(p)[3] for p in ((2, 2), (3, 2), (2, 3), (3, 3))] == [25]*4


def test_drop_shadow_normal_underlay_preserves_opaque_source_colour():
    image = source()
    out = render_layer_style(image, dict(style_color='#00ffff', style_blur=2, style_offset=[2, 3]))
    assert out.getpixel((10, 8)) == image.getpixel((10, 8))
    assert out.getchannel('A').getbbox() != image.getchannel('A').getbbox()


def test_outer_glow_grows_beyond_ink_and_illuminates_hole():
    image = source()
    out = render_layer_style(image, dict(style_type='outer_glow', style_blur=1, style_spread=1,
                                         style_color='#00ffff', style_output='effect'))
    assert out.getpixel((7, 9))[3] > 0
    assert out.getpixel((13, 11))[3] > 0
    assert out.getpixel((10, 8))[3] == 0


@pytest.mark.parametrize('style', ['inner_shadow', 'inner_glow'])
def test_inner_styles_preserve_partial_alpha_exactly(style):
    image = source(123)
    cfg = dict(style_type=style, style_offset=[2, 0], style_spread=1, style_blur=1, style_color='#ffffff')
    out = render_layer_style(image, cfg)
    assert out.getchannel('A').tobytes() == image.getchannel('A').tobytes()
    assert out.tobytes() != image.tobytes()
    assert out.getpixel((15, 12))[3] == 0


@pytest.mark.parametrize('position', ['outer', 'inner', 'center'])
def test_outline_placement_and_width_zero(position):
    image = source()
    cfg = dict(style_type='outline', outline_position=position, style_width=2,
               style_color='#123456', style_output='effect')
    out = render_layer_style(image, cfg)
    a = np.asarray(image.getchannel('A'))
    e = np.asarray(out.getchannel('A'))
    if position == 'inner':
        assert np.all(e[a == 0] == 0)
        assert out.getpixel((8, 8))[3] > 0
    if position == 'outer':
        assert np.all(e[a == 255] == 0)
        assert out.getpixel((7, 8))[3] > 0
    if position == 'center':
        assert out.getpixel((7, 8))[3] > 0
        assert out.getpixel((8, 8))[3] > 0
    identity = render_layer_style(image, {**cfg, 'style_width': 0, 'style_output': 'combined'})
    assert identity.tobytes() == image.tobytes()


def test_style_extends_outside_local_ink_without_changing_canvas():
    image = source()
    cfg = dict(style_offset=[-9.5, 0], style_blur=2)
    out = render_layer_style(image, cfg)
    assert out.size == image.size
    assert out.getpixel((0, 8))[3] > 0
    assert style_bounds(image.getchannel('A'), cfg)[0] < 0


def test_shift_can_bring_blur_from_outside_canvas_back_into_view():
    image = Image.new('RGBA', (10, 10))
    image.putpixel((0, 4), (255, 255, 255, 255))
    cfg = dict(style_output='effect', style_blur=1, style_offset=[2, 0])
    result = render_layer_style(image, cfg)
    assert result.getpixel((1, 4))[3] > 0
    assert result.getpixel((2, 4))[3] > result.getpixel((1, 4))[3]


def test_large_offsets_return_empty_effect_without_large_padded_canvas():
    result = render_layer_style(source(), dict(style_offset=[4096, -4096], style_output='effect'))
    assert result.getchannel('A').getbbox() is None


def test_fill_opacity_leaves_styles_visible_and_does_not_dim_mask():
    image = source()
    ImageDraw.Draw(image).rectangle((8, 6, 22, 18), fill=(210, 80, 30, 255))
    cfg = dict(style_type='outline', outline_position='inner', style_width=2, style_color='#ffffff')
    filled = render_layer_style(image, cfg)
    no_fill = render_layer_style(image, {**cfg, 'fill_opacity': 0})
    assert no_fill.getpixel((8, 8))[3] == filled.getpixel((8, 8))[3] == 255
    assert no_fill.getpixel((15, 12))[3] == 0


def test_original_alpha_changes_effect_mask_without_restoring_source_fill():
    image = source()
    original = image.getchannel('A')
    faded = image.copy()
    faded.putalpha(original.point(lambda a: round(a*.25)))
    cfg = dict(style_output='effect', style_offset=[3, 0])
    current = render_layer_style(faded, cfg, original_alpha=original)
    restored = render_layer_style(faded, {**cfg, 'alpha_source': 'original'}, original_alpha=original)
    assert restored.getpixel((11, 8))[3] == 255
    assert current.getpixel((11, 8))[3] == 64
    inside = render_layer_style(faded, dict(style_type='inner_glow', style_spread=2,
                                            alpha_source='original'), original_alpha=original)
    assert inside.getchannel('A').tobytes() == faded.getchannel('A').tobytes()


@pytest.mark.parametrize('bad', [
    {'style_type': 'bad'}, {'style_output': 'invalid'}, {'outline_position': 'centre'},
    {'style_color': '#ffffff00'}, {'style_color': 3}, {'style_blur': -1}, {'style_blur': 129},
    {'style_width': True}, {'style_spread': float('nan')}, {'style_strength': float('inf')},
    {'style_offset': [1]}, {'style_offset': [4097, 0]}, {'fill_opacity': 2},
    {'alpha_source': 'invalid'}, {'unexpected': 7},
])
def test_style_validation_is_strict(bad):
    with pytest.raises(ValueError):
        validate_style(bad)


def test_invalid_original_alpha_rejected():
    with pytest.raises(ValueError, match='original alpha'):
        render_layer_style(source(), {'alpha_source': 'original'}, original_alpha=Image.new('L', (1, 1)))


def test_descriptors_and_ephemeral_component_controls():
    cfg = {'style_offset': [4, 5], 'style_color': '#102030'}
    saved = deepcopy(cfg)
    a = style_control_apply(cfg, 'style_offset.x', 7.25)
    b = style_control_apply(a, 'style_color.b', 255)
    assert b['style_offset'] == [7.25, 5]
    assert b['style_color'] == '#1020ff'
    assert cfg == saved
    assert style_control_base(b, 'style_color.b') == 255
    descriptors = style_parameter_descriptors(cfg)
    assert descriptors['style_offset.x']['units'] == 'pixels'
    assert descriptors['style_type']['type'] == 'enum'
    descriptors['style_width']['range'][0] = -999
    assert style_parameter_descriptors()['style_width']['range'] == [0, 64]


@pytest.mark.parametrize('style', ['inner_glow', 'inner_shadow'])
def test_random_soft_alpha_is_unchanged_by_full_fill_inner_effects(style):
    rng = np.random.default_rng(17)
    pixels = rng.integers(0, 256, (16, 21, 4), dtype=np.uint8)
    image = Image.fromarray(pixels)
    result = render_layer_style(image, dict(style_type=style, style_spread=1.25,
                                            style_blur=2, style_offset=[.5, -1.25]))
    assert result.getchannel('A').tobytes() == image.getchannel('A').tobytes()


def test_generated_effect_has_zero_rgb_under_zero_alpha():
    result = render_layer_style(source(), dict(style_output='effect', style_color='#ff00ff'))
    a = np.asarray(result)
    assert np.all(a[a[..., 3] == 0, :3] == 0)
