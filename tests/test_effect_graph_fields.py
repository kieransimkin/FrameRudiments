"""CPU numerical, coverage, palette and resource contracts for M7.5."""
from copy import deepcopy
import math

import numpy as np
from PIL import Image
import pytest

from framerudiments import effect_graph_fields as fields
from framerudiments.effect_graph_fields import (
    FIELD_CONTROLS, FIELD_DEFAULTS, FIELD_ENUMS, FIELD_TYPES, _scalar, _transform, _vector,
    field_capabilities, field_control_apply, field_control_base, field_control_valid,
    field_parameter_descriptors, render_procedural_field, validate_field,
)
from framerudiments.effect_graph_spatial import _noise


def reference(size=(37, 29)):
    y, x = np.indices((size[1], size[0]))
    return Image.fromarray(np.stack([(x*31+y)%256, (x+y*11)%256, (x*7+y*13)%256,
                                    (x*13+y*19)%256], axis=-1).astype(np.uint8))


@pytest.mark.parametrize('kind', sorted(FIELD_TYPES))
@pytest.mark.parametrize('mode', sorted(FIELD_ENUMS['field_output']))
def test_every_kind_output_is_tiled_deterministic_and_immutable(kind, mode):
    image = reference()
    config = dict(field_type=kind, field_output=mode, field_scale=[7, 9], field_phase=-.23,
                  field_angle=31, field_vector_gain=12)
    before, saved = image.tobytes(), deepcopy(config)
    first = render_procedural_field(image, config, tile_rows=1)
    for rows in (7, 48):
        assert render_procedural_field(image, config, tile_rows=rows).tobytes() == first.tobytes()
    assert first.size == image.size and first.mode == 'RGBA'
    assert image.tobytes() == before and config == saved


@pytest.mark.parametrize('size', [(1, 1), (1, 9), (9, 1), (2, 2)])
@pytest.mark.parametrize('kind', ['linear', 'radial', 'cellular', 'fbm', 'checker'])
def test_tiny_odd_canvases(size, kind):
    out = render_procedural_field(reference(size), dict(field_type=kind, field_output='vector'))
    assert out.size == size


@pytest.mark.parametrize('mode', sorted(FIELD_ENUMS['field_output']))
@pytest.mark.parametrize('coverage', ['opaque', 'current', 'original'])
def test_reference_rgb_is_never_a_generator_input(mode, coverage):
    a = reference()
    b = Image.new('RGBA', a.size, (255, 0, 255, 0))
    b.putalpha(a.getchannel('A'))
    alpha = Image.new('L', a.size, 173)
    config = dict(field_output=mode, field_alpha_source=coverage)
    assert render_procedural_field(a, config, original_alpha=alpha).tobytes() == render_procedural_field(
        b, config, original_alpha=alpha).tobytes()


@pytest.mark.parametrize('coverage,expected', [('opaque', 255), ('current', 75), ('original', 123)])
@pytest.mark.parametrize('mode', ['grayscale', 'alpha', 'color', 'vector'])
def test_coverage_and_scalar_mask_are_not_multiplied_twice(coverage, expected, mode):
    image = Image.new('RGBA', (7, 9), (217, 12, 77, 75))
    cfg = dict(field_type='constant', field_value=.4, field_alpha_source=coverage, field_output=mode)
    result = np.asarray(render_procedural_field(image, cfg, original_alpha=Image.new('L', image.size, 123)))
    assert np.all(result[..., 3] == round(expected * (.4 if mode == 'alpha' else 1)))
    if mode == 'alpha':
        assert np.all(result[..., :3] == 255)


@pytest.mark.parametrize('mode', ['grayscale', 'alpha', 'color', 'vector'])
def test_zero_alpha_rgb_is_canonical_after_quantization(mode):
    image = Image.new('RGBA', (13, 11), (255, 200, 33, 0))
    out = np.asarray(render_procedural_field(image, dict(field_output=mode, field_alpha_source='current')))
    assert not out.any()
    out = render_procedural_field(image, dict(field_output=mode))
    assert out.getchannel('A').getbbox() is not None


def test_linear_pixel_centres_repeat_and_mirror():
    image = Image.new('RGBA', (8, 2))
    cfg = dict(field_type='linear', field_scale=[8, 2])
    expected = np.rint((np.arange(8)+.5)/8*255).astype(np.uint8)
    assert np.array_equal(np.asarray(render_procedural_field(image, cfg))[0, :, 0], expected)
    a = render_procedural_field(image, {**cfg, 'field_repeat': 'repeat', 'field_phase': 1})
    assert np.array_equal(np.asarray(a)[0, :, 0], expected)
    b = render_procedural_field(image, {**cfg, 'field_repeat': 'mirror', 'field_phase': 1})
    assert np.array_equal(np.asarray(b)[0, :, 0], expected[::-1])


def test_analytic_radial_and_angular_values():
    u = np.array([[0., 1., 0., -1.]])
    v = np.array([[0., 0., 1., 0.]])
    assert np.allclose(_scalar(u, v, {'field_type': 'radial'}), [[0, 1, 1, 1]])
    assert np.allclose(_scalar(u, v, {'field_type': 'angular'}), [[.5, .5, .75, 1]])


@pytest.mark.parametrize('kind', ['stripes', 'rings'])
@pytest.mark.parametrize('duty', [0, 1])
def test_empty_and_full_band_duty(kind, duty):
    image = reference()
    out = np.asarray(render_procedural_field(image, dict(field_type=kind, field_duty=duty)))
    assert np.all(out[..., :3] == 255*duty)


def test_transform_rotation_and_radial_invariance():
    y, x = np.indices((13, 17), dtype=float)
    cfg = dict(field_scale=[3, 7], field_offset=[-2.25, 1.5])
    u, v = _transform(x, y, (17, 13), cfg)
    a, b = _transform(x, y, (17, 13), {**cfg, 'field_angle': 90})
    assert np.allclose(a, v) and np.allclose(b, -u)
    assert np.allclose(np.hypot(u, v), np.hypot(a, b))


def test_shared_noise_and_fbm_single_octave_identity():
    y, x = np.indices((13, 17), dtype=float)
    u, v = x/5-.3, y/7+.1
    cfg = dict(field_type='noise', field_seed=21, field_phase=.3)
    expected = (_noise(u+.3, v+.3*.61803398875, 21)+1)/2
    assert np.allclose(_scalar(u, v, cfg), expected)
    assert np.allclose(_scalar(u, v, {**cfg, 'field_type': 'fbm', 'field_octaves': 1}), expected)
    assert np.allclose(_scalar(u, v, {**cfg, 'field_type': 'fbm', 'field_persistence': 0}), expected)


def test_cellular_matches_exhaustive_larger_neighbourhood():
    y, x = np.indices((21, 25), dtype=float)
    u, v = x/9-1.77, y/11-.31
    nearest = np.full_like(u, np.inf)
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            a, b = np.floor(u)+dx, np.floor(v)+dy
            fx = a+.5+.25*_noise(a, b, 17)
            fy = b+.5+.25*_noise(a, b, 118)
            nearest = np.minimum(nearest, (u-fx)**2+(v-fy)**2)
    expected = np.sqrt(nearest)/math.sqrt(2)
    assert np.allclose(_scalar(u, v, dict(field_type='cellular', field_seed=17)), expected)


def test_scalar_remapping_order():
    u = np.zeros((3, 4))
    cfg = dict(field_type='constant', field_value=.7, field_contrast=2,
               field_bias=-.1, field_gamma=2, field_invert=True)
    assert np.allclose(_scalar(u, u, cfg), 1 - .8**2)


@pytest.mark.parametrize('space', ['srgb', 'linear_srgb', 'oklab'])
def test_palette_transparency_does_not_leak_hidden_blue(space):
    palette = [dict(id='a', position=0, color='#ff0000', opacity=1),
               dict(id='b', position=1, color='#0000ff', opacity=0)]
    cfg = dict(field_type='constant', field_value=.5, field_output='color',
               field_palette=palette, field_color_space=space)
    out = render_procedural_field(reference(), cfg)
    r, g, b, a = out.getpixel((0, 0))
    assert r >= 254 and g <= 1 and b <= 1 and a == 128


def test_palette_spaces_and_endpoints():
    img = reference()
    cfg = dict(field_type='constant', field_value=.5, field_output='color')
    assert render_procedural_field(img, cfg).getpixel((0, 0)) == (128, 128, 128, 255)
    assert render_procedural_field(img, {**cfg, 'field_color_space': 'linear_srgb'}).getpixel((0, 0)) == (188, 188, 188, 255)
    for value in (0, 1):
        assert render_procedural_field(img, {**cfg, 'field_value': value}).getpixel((0, 0)) == (255*value,)*3+(255,)


@pytest.mark.parametrize('mode', ['gradient', 'curl'])
def test_constant_potential_is_exact_neutral_vector(mode):
    cfg = dict(field_output='vector', field_vector=mode, field_type='constant', field_value=.8)
    out = render_procedural_field(reference(), cfg)
    assert out.getextrema() == ((128, 128), (128, 128), (128, 128), (255, 255))


def test_vector_derivatives_are_in_screen_coordinates_and_curl_is_rotated_gradient():
    y, x = np.indices((9, 13), dtype=float)
    cfg = dict(field_type='linear', field_scale=[100, 80], field_angle=37, field_vector='gradient')
    u, v = _transform(x, y, (13, 9), cfg)
    a, b = _vector(u, v, cfg)
    assert np.allclose(a, math.cos(math.radians(37))/100)
    assert np.allclose(b, math.sin(math.radians(37))/80)
    c, d = _vector(u, v, {**cfg, 'field_vector': 'curl'})
    assert np.allclose(c, b) and np.allclose(d, -a)


@pytest.mark.parametrize('value,byte', [(0, 0), (.5, 128), (1, 255)])
def test_signed_vector_byte_encoding(value, byte):
    cfg = dict(field_output='vector', field_vector='independent', field_type='constant', field_value=value)
    assert render_procedural_field(reference(), cfg).getpixel((0, 0)) == (byte, byte, 128, 255)


def test_zero_vector_gain_is_exact_neutral_even_on_noise():
    out = render_procedural_field(reference(), dict(field_output='vector', field_vector_gain=0))
    assert out.getpixel((12, 7)) == (128, 128, 128, 255)


@pytest.mark.parametrize('name', sorted(FIELD_CONTROLS))
def test_all_controls_have_detached_descriptors_and_apply_default(name):
    schema = field_parameter_descriptors()
    assert name in schema
    value = field_control_base({}, name)
    cfg = field_control_apply({}, name, value)
    validate_field(cfg)
    assert schema[name]['default'] == value
    assert schema[name]['value'] == value
    assert schema[name]['animatable']
    schema[name]['default'] = 'changed'
    assert field_parameter_descriptors()[name]['default'] == value


def test_palette_control_paths_and_factory_defaults():
    cfg = dict(field_palette=[dict(id='cool', position=0, color='#123456'),
                              dict(id='hot', position=1, color='#fedcba')], field_output='color')
    before = deepcopy(cfg)
    assert field_control_valid(cfg, 'field_palette.hot.color.g')
    out = field_control_apply(cfg, 'field_palette.hot.color.g', 33)
    assert out['field_palette'][1]['color'] == '#fe21ba' and cfg == before
    descriptor = field_parameter_descriptors(cfg)['field_palette.hot.color.g']
    assert descriptor['value'] == 220 and descriptor['default'] is None and descriptor['active']
    assert 'field_palette.start.position' in field_parameter_descriptors()


@pytest.mark.parametrize('config', [
    {'field_type': 'perlin'}, {'unknown': 1}, {'field_phase': float('nan')},
    {'field_phase': float('inf')}, {'field_value': True}, {'field_seed': 2.5},
    {'field_octaves': 0}, {'field_invert': 1}, {'field_scale': [0, 1]},
    {'field_scale': [1]}, {'field_output': None}, {'field_vector_gain': -1},
    {'field_palette': []}, {'field_alpha_source': 'luminance'}, {'opacity': 2},
    {'field_palette': [dict(id='x', position=0, color='red'), dict(id='x', position=1, color='blue')]},
    {'field_palette': [dict(id='x', position=.5, color='red'), dict(id='y', position=.5, color='blue')]},
])
def test_invalid_authored_configs(config):
    with pytest.raises(ValueError):
        validate_field(config)


@pytest.mark.parametrize('count', [0, 2, True, 1.0, '1'])
def test_invalid_input_topology(count):
    with pytest.raises(ValueError):
        validate_field({}, count)


@pytest.mark.parametrize('name,value', [('field_value', True), ('field_phase', float('nan')),
                                       ('field_invert', 1), ('field_seed', -1), ('field_scale.x', 0)])
def test_invalid_control_values(name, value):
    with pytest.raises(ValueError):
        field_control_apply({}, name, value)


def test_integer_controls_round_only_native_overrides():
    assert field_control_apply({}, 'field_octaves', 2.7)['field_octaves'] == 3
    with pytest.raises(ValueError):
        validate_field({'field_octaves': 2.7})


def test_capability_manifest_detached_and_honest():
    cap = field_capabilities()
    assert not cap['gpu_native'] and not cap['reference_rgb_used']
    assert cap['alpha_lineage'] == 'new-generated-surface'
    cap['controls'].clear()
    assert field_capabilities()['controls']
    assert FIELD_DEFAULTS['field_scale'] == [64, 64]


@pytest.mark.parametrize('limit', ['MAX_PIXELS', 'MAX_DIMENSION', 'MAX_WORK_UNITS'])
def test_resource_limits_reject_before_output_allocation(monkeypatch, limit):
    image = reference()
    monkeypatch.setattr(fields, limit, 1)
    def unexpected(*args, **kwargs):
        pytest.fail('output was allocated before budget validation')
    monkeypatch.setattr(Image, 'new', unexpected)
    with pytest.raises(ValueError, match='budget'):
        render_procedural_field(image, {})


@pytest.mark.parametrize('rows', [0, 257, True, 1.5])
def test_invalid_tile_rows(rows):
    with pytest.raises(ValueError):
        render_procedural_field(reference(), {}, tile_rows=rows)


def test_invalid_reference_and_original_alpha():
    with pytest.raises(ValueError):
        render_procedural_field(Image.new('RGB', (2, 2)), {})
    with pytest.raises(ValueError):
        render_procedural_field(reference(), {}, original_alpha=Image.new('L', (1, 1)))
    with pytest.raises(ValueError):
        render_procedural_field(reference(), {}, original_alpha=Image.new('RGB', (37, 29)))
