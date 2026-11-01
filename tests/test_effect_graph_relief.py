"""M6.3 CPU reference contracts, including independent sweep/alpha oracles."""
from copy import deepcopy
import math

import numpy as np
from PIL import Image, ImageDraw
import pytest

from framerudiments.effect_graph_layer_styles import (
    STYLE_CONTROLS, render_layer_style, style_bounds, style_control_apply,
    style_control_base, style_parameter_descriptors, validate_style,
)
from framerudiments.effect_graph_layer_relief import (
    LIGHT_TYPES, RELIEF_COLOURS, RELIEF_DEFAULTS, RELIEF_ENUMS, RELIEF_RANGES,
    RELIEF_TYPES, SWEEP_TYPES,
)


def source(alpha=255):
    image = Image.new('RGBA', (48, 40), (255, 0, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 9, 30, 28), fill=(80, 120, 180, alpha))
    draw.rectangle((18, 15, 24, 22), fill=(255, 255, 0, 0))
    return image


@pytest.mark.parametrize('style', sorted(RELIEF_TYPES))
def test_every_new_style_is_visible_finite_immutable_and_deterministic(style):
    image = source(180)
    cfg = dict(style_type=style, style_color='#ff9020', extrude_color='#004080',
               style_output='effect', style_offset=[4.25, 2.75], style_blur=1)
    initial, definition = image.tobytes(), deepcopy(cfg)
    first = render_layer_style(image, cfg)
    render_layer_style(image, {**cfg, 'style_strength': .3})
    assert first.tobytes() == render_layer_style(image, cfg).tobytes()
    assert first.getchannel('A').getbbox() is not None
    values = np.asarray(first)
    assert np.all(values[values[..., 3] == 0, :3] == 0)
    assert image.tobytes() == initial and cfg == definition


@pytest.mark.parametrize('style', ['bevel', 'emboss', 'satin'])
@pytest.mark.parametrize('alpha', [31, 127, 200, 255])
def test_internal_relief_never_thickens_or_leaks_coverage(style, alpha):
    image = source(alpha)
    out = render_layer_style(image, dict(style_type=style))
    assert out.getchannel('A').tobytes() == image.getchannel('A').tobytes()
    assert out.getpixel((20, 18))[3] == 0
    assert out.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize('style', ['bevel', 'emboss', 'satin'])
def test_internal_relief_preserves_arbitrary_soft_alpha(style):
    rgba = np.random.default_rng(73).integers(0, 256, (15, 19, 4), dtype=np.uint8)
    image = Image.fromarray(rgba)
    out = render_layer_style(image, dict(style_type=style, style_blur=2))
    assert np.array_equal(np.asarray(out)[..., 3], rgba[..., 3])


def test_lighting_angle_reversal_reverses_left_right_highlights():
    image = Image.new('RGBA', (31, 31))
    ImageDraw.Draw(image).rectangle((5, 5, 25, 25), fill=(110, 110, 110, 255))
    cfg = dict(style_type='bevel', style_width=4, bevel_depth=6, bevel_softness=0,
               bevel_profile='linear', light_elevation=0, highlight_strength=1,
               shadow_strength=1)
    right = render_layer_style(image, {**cfg, 'light_angle': 0})
    left = render_layer_style(image, {**cfg, 'light_angle': 180})
    assert right.getpixel((23, 15))[0] > left.getpixel((23, 15))[0]
    assert left.getpixel((7, 15))[0] > right.getpixel((7, 15))[0]
    assert right.getpixel((15, 15)) == image.getpixel((15, 15))
    assert np.array_equal(np.asarray(left), np.asarray(right)[:, ::-1])


@pytest.mark.parametrize('style', ['bevel', 'emboss'])
def test_up_down_reverses_lighting_at_zero_elevation(style):
    cfg = dict(style_type=style, light_elevation=0, bevel_softness=0)
    up = render_layer_style(source(), cfg)
    down = render_layer_style(source(), {**cfg, 'bevel_direction': 'down'})
    assert up.tobytes() != down.tobytes()
    assert up.getchannel('A').tobytes() == down.getchannel('A').tobytes()


@pytest.mark.parametrize('style', sorted(LIGHT_TYPES))
def test_width_zero_is_byte_exact_no_op_and_effect_empty(style):
    image = source()
    assert render_layer_style(image, dict(style_type=style, style_width=0)).tobytes() == image.tobytes()
    assert render_layer_style(image, dict(style_type=style, style_width=0,
                                         style_output='effect')).getchannel('A').getbbox() is None


@pytest.mark.parametrize('style', ['bevel', 'emboss'])
def test_zero_depth_no_op_and_uniform_alpha_fade_retains_geometry(style):
    image = source()
    cfg = dict(style_type=style, bevel_depth=0)
    assert render_layer_style(image, cfg).tobytes() == image.tobytes()
    opaque = render_layer_style(image, dict(style_type=style))
    faded = render_layer_style(source(64), dict(style_type=style))
    visible = np.asarray(image)[..., 3] > 0
    # Lighting masks quantise to 8 bits; conditional coverage may differ by a few codes.
    difference = np.abs(np.asarray(opaque)[..., :3].astype(int) - np.asarray(faded)[..., :3].astype(int))
    assert difference[visible].max() <= 5
    assert faded.getchannel('A').tobytes() == source(64).getchannel('A').tobytes()


@pytest.mark.parametrize('position', ['inner', 'outer', 'center'])
def test_directional_rim_placement(position):
    image = source()
    cfg = dict(style_type='edge_light', outline_position=position, style_width=3,
               light_angle=0, light_elevation=0, style_output='effect')
    out = render_layer_style(image, cfg)
    alpha = np.asarray(image)[..., 3]
    edge = np.asarray(out)[..., 3]
    assert edge.max() > 0
    if position == 'inner':
        assert not np.any(edge[alpha == 0])
    elif position == 'outer':
        assert not np.any(edge[alpha == 255])
    else:
        assert np.any(edge[alpha == 0]) and np.any(edge[alpha == 255])


def test_satin_zero_offsets_and_inversion():
    image = source(128)
    cfg = dict(style_type='satin', style_offset=[0, 0], style_output='effect')
    assert render_layer_style(image, cfg).getchannel('A').getbbox() is None
    inverse = render_layer_style(image, {**cfg, 'satin_mode': 'inverted'})
    assert inverse.getchannel('A').tobytes() == image.getchannel('A').tobytes()


@pytest.mark.parametrize('kind', sorted(SWEEP_TYPES))
@pytest.mark.parametrize('angle,step', [(0, (1, 0)), (90, (0, 1)), (180, (-1, 0)), (270, (0, -1))])
def test_integer_sweep_matches_independent_max_union_oracle(kind, angle, step):
    image = source(110)
    cfg = dict(style_type=kind, extrude_length=5, extrude_angle=angle,
               style_output='effect', style_color='#102030', extrude_color='#102030')
    out = render_layer_style(image, cfg)
    reference = np.zeros((image.height, image.width), dtype=np.uint8)
    for i in range(6):
        stamp = Image.new('L', image.size)
        stamp.paste(image.getchannel('A'), (step[0]*i, step[1]*i))
        reference = np.maximum(reference, np.asarray(stamp))
    assert np.array_equal(np.asarray(out)[..., 3], reference)
    assert reference.max() == 110  # not 255 from repeated source-over stamping
    assert np.all(np.asarray(out)[reference > 0, :3] == [16, 32, 48])


def test_fractional_sweep_terminal_is_antialiased_and_colour_gradient_is_visible():
    image = Image.new('RGBA', (12, 5))
    image.putpixel((2, 2), (220, 30, 10, 180))
    out = render_layer_style(image, dict(style_type='extrusion', extrude_length=2.5,
                                        extrude_angle=0, style_color='#ff0000',
                                        extrude_color='#0000ff', style_output='effect'))
    assert out.getpixel((5, 2))[3] == 90
    assert out.getpixel((2, 2)) == (255, 0, 0, 180)
    assert out.getpixel((5, 2))[:3] == (0, 0, 255)
    assert out.getpixel((6, 2))[3] == 0
    assert out.getchannel('A').getextrema()[1] == 180


def test_extrude_fade_and_zero_length():
    image = Image.new('RGBA', (12, 3))
    image.putpixel((1, 1), (255, 255, 255, 255))
    cfg = dict(style_type='long_shadow', extrude_length=4, extrude_angle=0,
               extrude_fade=1, style_output='effect')
    out = render_layer_style(image, cfg)
    assert [out.getpixel((x, 1))[3] for x in range(1, 6)] == [255, 191, 128, 64, 0]
    empty = render_layer_style(image, {**cfg, 'extrude_length': 0})
    assert empty.getchannel('A').getbbox() is None
    identity = render_layer_style(image, {**cfg, 'extrude_length': 0, 'style_output': 'combined'})
    assert identity.tobytes() == image.tobytes()


@pytest.mark.parametrize('kind', sorted(RELIEF_TYPES))
def test_original_alpha_controls_effect_without_restoring_current_fill(kind):
    image = source()
    original = image.getchannel('A')
    image.putalpha(original.point(lambda a: round(a*.2)))
    cfg = dict(style_type=kind, style_output='effect', light_elevation=0, style_color='#4080ff')
    current = render_layer_style(image, cfg, original_alpha=original)
    before = image.tobytes()
    restored = render_layer_style(image, {**cfg, 'alpha_source': 'original'}, original_alpha=original)
    assert restored.getchannel('A').getextrema()[1] > current.getchannel('A').getextrema()[1]
    assert image.tobytes() == before


@pytest.mark.parametrize('name', sorted(RELIEF_RANGES))
def test_every_numeric_control_has_descriptor_and_rejects_nonfinite_bool_and_bounds(name):
    assert name in STYLE_CONTROLS
    desc = style_parameter_descriptors()[name]
    low, high = RELIEF_RANGES[name]
    assert desc['range'] == [low, high]
    cfg = style_control_apply({}, name, (low + high)/2)
    validate_style(cfg)
    assert isinstance(style_control_base(cfg, name), (int, float))
    for invalid in (True, float('nan'), float('inf'), low - 1, high + 1):
        with pytest.raises(ValueError):
            style_control_apply({}, name, invalid)


@pytest.mark.parametrize('field', sorted(RELIEF_COLOURS))
def test_colour_channels_are_independent_and_invalid_colours_fail(field):
    base = {field: '#102030'}
    out = style_control_apply(base, field + '.g', 90)
    assert out[field] == '#105a30'
    assert base[field] == '#102030'
    for value in (13, '#ffffff80', 'not-a-colour', [1, 2, 3]):
        with pytest.raises(ValueError):
            validate_style({field: value})


@pytest.mark.parametrize('field', sorted(RELIEF_ENUMS))
def test_categorical_controls_and_metadata_are_copy_safe(field):
    for choice in RELIEF_ENUMS[field]:
        assert style_control_base(style_control_apply({}, field, choice), field) == choice
    with pytest.raises(ValueError):
        style_control_apply({}, field, False)
    descriptor = style_parameter_descriptors()
    descriptor[field]['choices'].append('not-real')
    assert 'not-real' not in style_parameter_descriptors()[field]['choices']
    assert RELIEF_DEFAULTS[field] in RELIEF_ENUMS[field]


def test_allocations_are_bounded_before_distance_and_sweep_work(monkeypatch):
    import framerudiments.effect_graph_layer_relief as module

    monkeypatch.setattr(module, 'MAX_DISTANCE_PIXELS', 100)
    with pytest.raises(ValueError, match='budget'):
        render_layer_style(source(), dict(style_type='bevel'))
    monkeypatch.setattr(module, 'MAX_SWEEP_VISITS', 100)
    with pytest.raises(ValueError, match='work budget'):
        render_layer_style(source(), dict(style_type='long_shadow'))


@pytest.mark.parametrize('kind', sorted(RELIEF_TYPES))
def test_reported_bounds_enclose_actual_output(kind):
    image = source()
    cfg = dict(style_type=kind, style_output='effect', extrude_angle=-135,
               extrude_length=11.5, style_offset=[2.5, 3.25])
    box = style_bounds(image.getchannel('A'), cfg)
    actual = render_layer_style(image, cfg).getchannel('A').getbbox()
    assert actual is not None and box is not None
    assert box[0] <= actual[0] <= actual[2] <= box[2]
    assert box[1] <= actual[1] <= actual[3] <= box[3]
    assert all(math.isfinite(v) for v in box)


@pytest.mark.parametrize('style', ['bevel', 'emboss'])
@pytest.mark.parametrize('elevation', [0, 30, 60, 90])
def test_extreme_lighting_effect_coverage_cannot_exceed_reference_alpha(style, elevation):
    image = source(100)
    cfg = dict(style_type=style, bevel_depth=64, style_width=1, bevel_softness=0,
               light_elevation=elevation, light_angle=180, highlight_strength=1,
               shadow_strength=1, style_output='effect')
    out = render_layer_style(image, cfg)
    assert np.all(np.asarray(out)[..., 3] <= np.asarray(image)[..., 3])
