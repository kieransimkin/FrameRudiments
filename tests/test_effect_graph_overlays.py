"""M6.4 image-level coverage, gradient interpolation and texture sampling."""
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image, ImageDraw

from framerudiments.effect_graph_layer_styles import render_layer_style, style_bounds, validate_style
from framerudiments.effect_graph_layer_overlays import (
    OVERLAY_TYPES, OVERLAY_ENUMS, _from_lab, _to_lab,
)


def shape(size=(19, 17)):
    image = Image.new('RGBA', size, (255, 0, 255, 0))
    ImageDraw.Draw(image).rectangle((3, 3, size[0]-4, size[1]-4), fill=(60, 120, 180, 128))
    image.putpixel((size[0]//2, size[1]//2), (255, 0, 255, 0))
    return image


def stops(**overrides):
    return [dict(id='a', position=0, color='#ff0000', opacity=1, **overrides),
            dict(id='b', position=1, color='#0000ff', opacity=1)]


@pytest.mark.parametrize('kind', sorted(OVERLAY_TYPES))
@pytest.mark.parametrize('alpha_mode', ['current', 'original'])
def test_combined_overlay_preserves_semitransparency_and_holes(kind, alpha_mode):
    image = shape()
    original = image.tobytes()
    cfg = dict(style_type=kind, style_color='#ffcc00', style_strength=.65, alpha_source=alpha_mode)
    out = render_layer_style(image, cfg, original_alpha=Image.new('L', image.size, 255))
    assert out.getchannel('A').tobytes() == image.getchannel('A').tobytes()
    assert out.getpixel((0, 0)) == (0, 0, 0, 0)
    assert out.getpixel((9, 8)) == (0, 0, 0, 0)
    assert image.tobytes() == original
    assert style_bounds(image.getchannel('A'), cfg) == image.getchannel('A').getbbox()


@pytest.mark.parametrize('kind', sorted(OVERLAY_TYPES))
def test_strength_zero_is_exact_identity_including_hidden_rgb(kind):
    image = shape()
    assert render_layer_style(image, {'style_type': kind, 'style_strength': 0}).tobytes() == image.tobytes()
    effect = render_layer_style(image, {'style_type': kind, 'style_strength': 0, 'style_output': 'effect'})
    assert effect.getbbox() is None


def test_color_overlay_fill_and_strength_have_independent_alpha():
    image = Image.new('RGBA', (1, 1), (0, 0, 255, 128))
    cfg = dict(style_type='color_overlay', style_color='#ff0000', style_strength=.5)
    assert render_layer_style(image, cfg).getpixel((0, 0)) == (128, 0, 128, 128)
    assert render_layer_style(image, dict(cfg, fill_opacity=0)).getpixel((0, 0)) == (255, 0, 0, 64)
    assert render_layer_style(image, dict(cfg, fill_opacity=.5)).getpixel((0, 0)) == (170, 0, 85, 96)


def test_original_alpha_restricts_grown_coverage_but_effect_export_can_restore():
    im = Image.new('RGBA', (2, 1), (20, 30, 40, 255))
    reference = Image.new('L', (2, 1), 0)
    reference.putpixel((0, 0), 128)
    config = dict(style_type='color_overlay', style_color='#ff0000', alpha_source='original')
    out = render_layer_style(im, config, original_alpha=reference)
    assert out.getpixel((1, 0)) == im.getpixel((1, 0))
    assert out.getpixel((0, 0))[3] == 255
    assert 136 <= out.getpixel((0, 0))[0] <= 139
    keyed = Image.new('RGBA', (2, 1))
    combined = render_layer_style(keyed, config, original_alpha=reference)
    assert combined.getbbox() is None
    effect = render_layer_style(keyed, dict(config, style_output='effect'), original_alpha=reference)
    assert effect.getpixel((0, 0)) == (255, 0, 0, 128)


@pytest.mark.parametrize('space', ['srgb', 'linear_srgb', 'oklab'])
def test_gradient_transparent_stop_has_no_hidden_colour_bleed(space):
    cfg = dict(style_type='gradient_overlay', overlay_space='canvas', gradient_space=space,
               gradient_stops=[{'id': 'a', 'position': 0, 'color': '#ff0000', 'opacity': 0},
                               {'id': 'b', 'position': 1, 'color': '#0000ff'}], style_output='effect')
    out = render_layer_style(Image.new('RGBA', (1, 1), 'white'), cfg).getpixel((0, 0))
    assert out[0] <= 1 and out[1] <= 1 and out[2] >= 254 and out[3] == 128


def test_linear_light_midpoint_differs_from_display_space():
    image = Image.new('RGBA', (1, 1), 'white')
    cfg = dict(style_type='gradient_overlay', overlay_space='canvas')
    assert render_layer_style(image, cfg).getpixel((0, 0)) == (128, 128, 128, 255)
    assert render_layer_style(image, dict(cfg, gradient_space='linear_srgb')).getpixel((0, 0)) == (188, 188, 188, 255)


def test_oklab_transform_roundtrip():
    rgb = np.random.default_rng(31).random((100, 3), dtype=np.float32)
    assert np.allclose(_from_lab(_to_lab(rgb)), rgb, atol=2e-5)


@pytest.mark.parametrize('kind', sorted(OVERLAY_ENUMS['gradient_kind']))
@pytest.mark.parametrize('repeat', sorted(OVERLAY_ENUMS['gradient_repeat']))
def test_gradient_modes_repeat_seek_and_inputs_immutable(kind, repeat):
    image = shape()
    cfg = dict(style_type='gradient_overlay', gradient_kind=kind, gradient_repeat=repeat,
               gradient_phase=-.2, gradient_angle=39, gradient_scale=.6, gradient_stops=stops())
    original = deepcopy(cfg)
    first = render_layer_style(image, cfg)
    render_layer_style(image, dict(cfg, gradient_phase=.3))
    assert render_layer_style(image, cfg).tobytes() == first.tobytes()
    assert cfg == original
    assert first.getchannel('A').tobytes() == image.getchannel('A').tobytes()


def test_canvas_vs_source_space_and_gradient_reverse():
    im = shape((31, 13))
    cfg = dict(style_type='gradient_overlay', gradient_stops=stops())
    local = render_layer_style(im, cfg)
    canvas = render_layer_style(im, dict(cfg, overlay_space='canvas'))
    reverse = render_layer_style(im, dict(cfg, gradient_direction='reverse'))
    assert local.tobytes() != canvas.tobytes()
    assert local.getpixel((4, 5))[0] == reverse.getpixel((4, 5))[2]


@pytest.mark.parametrize('interpolation', ['linear', 'smoothstep', 'constant'])
def test_gradient_interpolation_and_extension(interpolation):
    im = Image.new('RGBA', (5, 1), 'white')
    cfg = dict(style_type='gradient_overlay', overlay_space='canvas',
               gradient_interpolation=interpolation,
               gradient_stops=[{'id': 'a', 'position': .3, 'color': '#ff0000'},
                               {'id': 'b', 'position': .7, 'color': '#0000ff'}])
    out = render_layer_style(im, cfg)
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)
    assert out.getpixel((4, 0)) == (0, 0, 255, 255)
    assert out.getpixel((2, 0)) == ((255, 0, 0, 255) if interpolation == 'constant' else (128, 0, 128, 255))


@pytest.mark.parametrize('kind', ['checker', 'stripes', 'dots', 'grid'])
def test_procedural_patterns_move_and_keep_coverage(kind):
    im = shape((42, 31))
    cfg = dict(style_type='pattern_overlay', pattern_kind=kind, pattern_scale=[8, 9],
               style_color='#ee0088', pattern_background='#00aaff')
    a = render_layer_style(im, cfg)
    b = render_layer_style(im, dict(cfg, pattern_offset=[1.5, -.75], pattern_angle=17))
    assert a.tobytes() != b.tobytes()
    assert b.getchannel('A').tobytes() == im.getchannel('A').tobytes()
    assert render_layer_style(im, cfg).tobytes() == a.tobytes()


@pytest.mark.parametrize('wrap', ['repeat', 'mirror', 'clamp'])
def test_texture_sampling_is_premultiplied_and_independent_of_hidden_colour(wrap):
    im = Image.new('RGBA', (19, 7), (0, 0, 0, 128))
    tex = Image.new('RGBA', (2, 1), (255, 0, 0, 0))
    tex.putpixel((1, 0), (0, 0, 255, 255))
    clean = tex.copy()
    clean.putpixel((0, 0), (0, 0, 0, 0))
    cfg = dict(style_type='pattern_overlay', pattern_kind='texture', style_output='effect',
               pattern_wrap=wrap, pattern_scale=[4, 4], pattern_offset=[.25, 0])
    a = render_layer_style(im, cfg, pattern_image=tex)
    b = render_layer_style(im, cfg, pattern_image=clean)
    assert a.tobytes() == b.tobytes()
    assert np.asarray(a)[..., 0].max() == 0
    assert tex.getpixel((0, 0)) == (255, 0, 0, 0)


def test_repeated_texture_exact_at_native_texel_centres():
    tex = Image.new('RGBA', (2, 1))
    tex.putdata([(255, 0, 0, 255), (0, 255, 0, 128)])
    im = Image.new('RGBA', (4, 1), 'white')
    cfg = dict(style_type='pattern_overlay', pattern_kind='texture', pattern_scale=[2, 1], style_output='effect')
    out = render_layer_style(im, cfg, pattern_image=tex)
    assert [out.getpixel((x, 0)) for x in range(4)] == [(255, 0, 0, 255), (0, 255, 0, 128)]*2


@pytest.mark.parametrize('kind', sorted(OVERLAY_TYPES))
def test_tile_size_does_not_change_output(kind, monkeypatch):
    im = shape((39, 130))
    cfg = dict(style_type=kind, gradient_space='oklab', pattern_angle=31, pattern_scale=[5, 7])
    a = render_layer_style(im, cfg)
    monkeypatch.setattr('framerudiments.effect_graph_layer_overlays.TILE_ROWS', 1)
    assert render_layer_style(im, cfg).tobytes() == a.tobytes()


@pytest.mark.parametrize('bad', [[], [{}], [{'id': 'x', 'position': 0, 'color': 'red'}]*2,
                                [{'id': 'x', 'position': .6, 'color': 'red'}, {'id': 'y', 'position': .4, 'color': 'blue'}],
                                [{'id': 'x', 'position': 0, 'color': '#ff000080'}, {'id': 'y', 'position': 1, 'color': 'blue'}]])
def test_invalid_stop_tables(bad):
    with pytest.raises(ValueError):
        validate_style(dict(style_type='gradient_overlay', gradient_stops=bad))


@pytest.mark.parametrize('cfg', [dict(pattern_scale=[0, 1]), dict(pattern_angle=float('nan')),
                                dict(gradient_center=[1]), dict(gradient_scale=True),
                                dict(gradient_repeat='bad'), dict(pattern_wrap=[]),
                                dict(pattern_background='rgba(0,0,0,0.5)')])
def test_bad_parameters_fail_validation(cfg):
    with pytest.raises(ValueError):
        validate_style(dict(style_type='pattern_overlay', **cfg))


def test_budget_precedes_texture_array_creation(monkeypatch):
    monkeypatch.setattr('framerudiments.effect_graph_layer_overlays.MAX_TEXTURE_PIXELS', 1)
    with pytest.raises(ValueError, match='texture.*budget'):
        render_layer_style(Image.new('RGBA', (2, 2)), {'style_type': 'pattern_overlay', 'pattern_kind': 'texture'},
                           pattern_image=Image.new('RGBA', (2, 1)))


def test_nearby_valid_stop_positions_cannot_collapse_to_division_by_zero():
    cfg = dict(style_type='gradient_overlay', gradient_stops=[
        {'id': 'a', 'position': .500000001, 'color': 'red'},
        {'id': 'b', 'position': .500000002, 'color': 'blue'}])
    with np.errstate(divide='raise', invalid='raise'):
        out = render_layer_style(Image.new('RGBA', (3, 1), 'white'), cfg)
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)
    assert out.getpixel((2, 0)) == (0, 0, 255, 255)
