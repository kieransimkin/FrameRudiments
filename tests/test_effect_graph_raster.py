"""M7.4 mathematical, alpha, validation, bounded-work and deterministic tests."""
from copy import deepcopy
import math

import numpy as np
from PIL import Image
import pytest

from framerudiments import effect_graph_raster as r


@pytest.fixture
def ink():
    y, x = np.indices((19, 23))
    a = np.stack([(x * 37 + y * 19) % 256, (y * 43 + 23) % 256,
                  (x * 11 + 41) % 256, (x * 17 + y * 29) % 256], axis=-1).astype(np.uint8)
    a[4:9, 5:12, 3] = 0
    return Image.fromarray(a)


@pytest.mark.parametrize('kind', sorted(r.RASTER_TYPES))
@pytest.mark.parametrize('mix', [0, .25, 1])
def test_shape_mode_determinism_immutability(ink, kind, mix):
    cfg = {'raster_type': kind, 'raster_mix': mix}
    saved, data = deepcopy(cfg), ink.tobytes()
    out = r.render_raster(ink, cfg)
    assert out.mode == 'RGBA' and out.size == ink.size and out is not ink
    r.render_raster(ink, {**cfg, 'raster_seed': 194, 'raster_phase': 7.2})
    assert out.tobytes() == r.render_raster(ink, cfg).tobytes()
    assert cfg == saved and ink.tobytes() == data
    if mix == 0:
        assert out.tobytes() == data
    else:
        a = np.asarray(out)
        assert np.all(a[a[..., 3] == 0, :3] == 0)


@pytest.mark.parametrize('kind', sorted(r.RASTER_TYPES))
@pytest.mark.parametrize('rows', [1, 7, 48])
def test_tiling_changes_neither_pixels_nor_lineage(ink, kind, rows):
    cfg = {'raster_type': kind, 'raster_mix': .37, 'raster_phase': -2.4,
           'glitch_density': 1, 'glitch_chromatic': 2.25, 'pixel_alpha': 'average'}
    ref = ink.getchannel('A').point(lambda a: min(255, a * 2))
    expected, ea = r.render_raster_with_alpha(ink, cfg, original_alpha=ref, tile_rows=48)
    out, oa = r.render_raster_with_alpha(ink, cfg, original_alpha=ref, tile_rows=rows)
    assert out.tobytes() == expected.tobytes()
    assert oa.tobytes() == ea.tobytes()


@pytest.mark.parametrize('field', list(r.RASTER_RANGES))
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True])
def test_invalid_numeric_controls_fail_closed(field, bad):
    with pytest.raises(ValueError):
        r.raster_control_apply({}, field, bad)


@pytest.mark.parametrize('field', list(r.RASTER_ENUMS))
def test_invalid_categorical_fields(field):
    for bad in (None, [], {}, 'not-a-mode', 1):
        with pytest.raises(ValueError):
            r.raster_control_apply({}, field, bad)


@pytest.mark.parametrize('cfg', [
    {'pixel_size': [0, 3]}, {'pixel_size': [2.2, 4]}, {'pixel_size': [2]},
    {'pixel_origin': [1, False]}, {'sort_low': .8, 'sort_high': .2},
    {'sort_min_run': 20, 'sort_span': 10}, {'glitch_band_height': 0},
    {'sort_alpha_floor': 0}, {'raster_seed': -1}, {'crt_gain': 5},
    {'unknown': 1}, {'raster_type': []}, {'dither_levels': 1},
])
def test_invalid_authored_configs(cfg):
    with pytest.raises(ValueError):
        r.validate_raster(cfg, 1)


@pytest.mark.parametrize('field', sorted(r._INTEGER))
def test_discrete_controls_use_half_up(field):
    low, high = r.RASTER_RANGES[field]
    value = max(low, 3) + .5
    assert value <= high
    cfg = r.raster_control_apply({}, field, value)
    assert r.raster_control_base(cfg, field) == math.floor(value + .5)


def test_copy_on_write_vectors_and_detached_descriptors():
    cfg = {'pixel_size': [3, 4]}
    out = r.raster_control_apply(cfg, 'pixel_size.x', 8)
    assert cfg == {'pixel_size': [3, 4]} and out['pixel_size'] == [8, 4]
    desc = r.raster_parameter_descriptors(cfg)
    desc['pixel_size.x']['range'][0] = -99
    assert r.raster_parameter_descriptors(cfg)['pixel_size.x']['range'][0] == 1
    assert desc['pixel_size.x']['value'] == 3 and desc['pixel_size.x']['default'] == 8
    assert not desc['crt_gain']['active']
    assert set(desc) == r.RASTER_CONTROLS | {'opacity'}


@pytest.mark.parametrize('size', [(1, 1), (1, 7), (9, 1)])
@pytest.mark.parametrize('kind', sorted(r.RASTER_TYPES))
def test_single_pixel_dimensions(size, kind):
    out = r.render_raster(Image.new('RGBA', size, (50, 100, 200, 127)), {'raster_type': kind})
    assert out.size == size


def test_pixelate_ignores_hidden_rgb_and_averages_actual_partial_cell_area():
    src = Image.fromarray(np.array([[[255, 0, 0, 255], [0, 255, 255, 0], [0, 0, 255, 128]]], np.uint8))
    out = np.asarray(r.render_raster(src, {'pixel_size': [2, 1], 'pixel_alpha': 'average'}))
    assert out[0, 0].tolist() == [255, 0, 0, 128]
    assert out[0, 1].tolist() == [255, 0, 0, 128]
    assert out[0, 2].tolist() == [0, 0, 255, 128]
    preserved = r.render_raster(src, {'pixel_size': [2, 1], 'pixel_alpha': 'preserve'})
    assert preserved.getchannel('A').tobytes() == src.getchannel('A').tobytes()


def test_pixelate_lineage_uses_same_grid_average():
    src = Image.new('RGBA', (3, 1), (0, 0, 255, 80))
    a = Image.fromarray(np.array([[0, 128, 255]], np.uint8))
    _, out = r.render_raster_with_alpha(src, {'pixel_size': [2, 1], 'pixel_alpha': 'average'}, original_alpha=a)
    assert np.asarray(out).tolist() == [[64, 64, 255]]


def test_pixel_grid_origin_shifts_boundaries():
    im = Image.fromarray(np.array([[[x * 30, 0, 0, 255] for x in range(5)]], np.uint8))
    a = r.render_raster(im, {'pixel_size': [3, 1], 'pixel_origin': [0, 0]})
    b = r.render_raster(im, {'pixel_size': [3, 1], 'pixel_origin': [1, 0]})
    assert a.tobytes() != b.tobytes()
    assert b.getpixel((0, 0))[0] == 0


def test_sort_breaks_at_transparency_thresholds_and_keeps_lineage():
    rgba = np.array([[[200, 9, 0, 255], [10, 7, 0, 128], [255, 0, 0, 0],
                      [90, 1, 0, 255], [30, 2, 0, 255], [250, 3, 0, 255]]], np.uint8)
    ref = Image.fromarray(np.array([[10, 20, 30, 40, 50, 60]], np.uint8))
    out, alpha = r.render_raster_with_alpha(Image.fromarray(rgba),
        {'raster_type': 'pixel_sort', 'sort_key': 'red', 'sort_high': .8}, original_alpha=ref)
    assert np.asarray(out)[0, :, 0].tolist() == [10, 200, 0, 30, 90, 250]
    assert np.asarray(alpha).tolist() == [[20, 10, 30, 50, 40, 60]]
    assert np.asarray(out)[0, :, 3].tolist() == [128, 255, 0, 255, 255, 255]


@pytest.mark.parametrize('reverse', [True, False])
def test_sort_is_stable_for_equal_keys(reverse):
    a = np.array([[[20, 3, 0, 255], [20, 1, 0, 200], [10, 4, 0, 128], [20, 2, 0, 255]]], np.uint8)
    out = np.asarray(r.render_raster(Image.fromarray(a), {'raster_type': 'pixel_sort', 'sort_key': 'red', 'sort_reverse': reverse}))
    assert out[0, out[0, :, 0] == 20, 1].tolist() == [3, 1, 2]


def test_sort_vertical_span_and_minimum():
    im = Image.fromarray(np.array([[[v, v, v, 255]] for v in [9, 3, 8, 1, 7]], np.uint8))
    out = r.render_raster(im, {'raster_type': 'pixel_sort', 'sort_axis': 'y', 'sort_span': 2})
    assert np.asarray(out)[:, 0, 0].tolist() == [3, 9, 1, 8, 7]


@pytest.mark.parametrize('pattern', ['bayer2', 'bayer4', 'bayer8', 'noise', 'none'])
@pytest.mark.parametrize('space', ['srgb', 'linear_srgb'])
def test_dither_quantization_endpoints_alpha_and_monochrome(pattern, space):
    src = np.zeros((8, 8, 4), np.uint8)
    src[..., :3] = 128
    src[..., 3] = 173
    src[0, 0, :3], src[0, 1, :3] = 0, 255
    cfg = {'raster_type': 'dither', 'dither_pattern': pattern, 'dither_space': space,
           'dither_levels': 2, 'dither_monochrome': True}
    out = np.asarray(r.render_raster(Image.fromarray(src), cfg))
    assert set(np.unique(out[..., :3])) <= {0, 255}
    assert np.array_equal(out[..., 3], src[..., 3])
    assert np.all(out[0, 0, :3] == 0) and np.all(out[0, 1, :3] == 255)
    assert np.array_equal(out[..., 0], out[..., 1])


def test_bayer_half_gray_has_balanced_coverage():
    src = Image.new('RGBA', (8, 8), (128, 128, 128, 255))
    out = np.asarray(r.render_raster(src, {'raster_type': 'dither', 'dither_levels': 2}))
    assert np.count_nonzero(out[..., 0]) == 32


def test_seeded_noise_does_not_modify_numpy_random_state(ink):
    before = np.random.get_state()
    cfg = {'raster_type': 'dither', 'dither_pattern': 'noise', 'raster_seed': 45}
    a = r.render_raster(ink, cfg)
    b = r.render_raster(ink, {**cfg, 'raster_phase': 1})
    after = np.random.get_state()
    assert a.tobytes() != b.tobytes()
    assert before[0] == after[0] and np.array_equal(before[1], after[1]) and before[2:] == after[2:]


@pytest.mark.parametrize('phase', [0, .13, .5, 19.7, -2.2])
def test_scanline_pixel_coverage_integrates_to_duty_cycle(phase):
    x = np.arange(48, dtype=np.float64)
    c = r._box_coverage(x, 4, .3, phase)
    assert c.min() >= 0 and c.max() <= 1
    assert c.mean() == pytest.approx(.3, abs=1e-12)


@pytest.mark.parametrize('kind,extra', [
    ('scanlines', {}), ('dither', {}), ('pixelate', {'pixel_alpha': 'preserve'}),
    ('crt', {'crt_curvature': 0}),
])
def test_colour_only_operations_preserve_both_alpha_channels(ink, kind, extra):
    lineage = ink.getchannel('A').point(lambda a: 255 - a)
    out, ref = r.render_raster_with_alpha(ink, {'raster_type': kind, **extra}, original_alpha=lineage)
    assert out.getchannel('A').tobytes() == ink.getchannel('A').tobytes()
    assert ref.tobytes() == lineage.tobytes()


@pytest.mark.parametrize('border', ['transparent', 'clamp', 'repeat', 'mirror'])
def test_spatial_hidden_rgb_invariance_and_matching_lineage(border):
    a = np.zeros((13, 17, 4), np.uint8)
    a[3:9, 4:12] = [80, 140, 220, 200]
    b = a.copy()
    b[b[..., 3] == 0, :3] = [255, 0, 50]
    for kind in ('glitch', 'crt'):
        cfg = {'raster_type': kind, 'raster_border': border, 'glitch_density': 1,
               'glitch_offset': [2.3, 1.7], 'glitch_chromatic': 1.25, 'crt_curvature': .45}
        out, lineage = r.render_raster_with_alpha(Image.fromarray(a), cfg, original_alpha=Image.fromarray(a[..., 3]))
        assert out.tobytes() == r.render_raster(Image.fromarray(b), cfg).tobytes()
        np.testing.assert_allclose(np.asarray(out.getchannel('A')), np.asarray(lineage), atol=1)


def test_glitch_phase_is_explicit_and_integer_bucketed(ink):
    cfg = {'raster_type': 'glitch', 'glitch_density': 1, 'glitch_band_height': 3, 'glitch_offset': [5, 2]}
    a = r.render_raster(ink, {**cfg, 'raster_phase': .1})
    b = r.render_raster(ink, {**cfg, 'raster_phase': .9})
    c = r.render_raster(ink, {**cfg, 'raster_phase': 1.1})
    assert a.tobytes() == b.tobytes() != c.tobytes()


def test_dimensions_and_work_limits_are_checked_before_sampling(ink, monkeypatch):
    monkeypatch.setattr(r, 'MAX_PIXELS', 10)
    with pytest.raises(ValueError, match='budget'):
        r.render_raster(ink, {})
    with pytest.raises(ValueError):
        r.render_raster(Image.new('RGB', (2, 2)), {})


def test_row_work_is_bounded(ink, monkeypatch):
    monkeypatch.setattr(r, 'MAX_TILE_PIXELS', 50)
    original = r._sample_border
    shapes = []
    def checked(source, x, y, border):
        shapes.append(x.size)
        return original(source, x, y, border)
    monkeypatch.setattr(r, '_sample_border', checked)
    r.render_raster(ink, {'raster_type': 'glitch', 'glitch_density': 1})
    assert shapes and max(shapes) <= 50


@pytest.mark.parametrize('bad', [0, 257, 1.5, True])
def test_invalid_tile_rows(ink, bad):
    with pytest.raises(ValueError, match='tile_rows'):
        r.render_raster(ink, {}, tile_rows=bad)


def test_linear_monochrome_luminance_is_computed_after_transfer_decode():
    im = Image.new('RGBA', (1, 1), (255, 0, 0, 211))
    result = r.render_raster(im, {'raster_type': 'dither', 'dither_monochrome': True,
                               'dither_space': 'linear_srgb', 'dither_pattern': 'none',
                               'dither_levels': 4})
    encoded = round((1.055 * (1 / 3)**(1 / 2.4) - .055) * 255)
    assert result.getpixel((0, 0)) == (encoded, encoded, encoded, 211)


def test_extremely_large_integer_fails_as_validation_not_overflow():
    with pytest.raises(ValueError):
        r.validate_raster({'raster_seed': 10**1000})


def test_descriptor_units_and_inactive_options_are_explicit():
    descriptors = r.raster_parameter_descriptors({'raster_type': 'dither', 'dither_pattern': 'none'})
    assert descriptors['pixel_size.x']['units'] == 'pixels'
    assert descriptors['raster_phase']['units'] == 'state_index'
    assert not descriptors['dither_strength']['active']
    assert not descriptors['dither_origin.x']['active']
    descriptors = r.raster_parameter_descriptors({'raster_type': 'crt', 'crt_mask': 'none'})
    assert not descriptors['crt_pitch']['active']
