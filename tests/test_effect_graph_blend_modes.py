"""Independent numerical references for W3C colour blending and alpha overlap."""
import numpy as np
import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_blend import BLEND_MODES, blend_rgb, composite_blend


@pytest.mark.parametrize('mode,expected', [
    ('normal', .75), ('multiply', .1875), ('screen', .8125), ('overlay', .375),
    ('darken', .25), ('lighten', .75), ('hard_light', .625), ('soft_light', .375),
    ('difference', .5), ('exclusion', .625), ('color_dodge', 1), ('color_burn', 0),
    ('hue', .25), ('saturation', .25), ('color', .25), ('luminosity', .75),
])
def test_known_gray_blends(mode, expected):
    b = np.full((1, 1, 3), .25, dtype=np.float32)
    s = np.full((1, 1, 3), .75, dtype=np.float32)
    assert np.allclose(blend_rgb(b, s, mode), expected, atol=1e-6)


@pytest.mark.parametrize('mode', sorted(BLEND_MODES))
def test_blends_preserve_alpha_and_ignore_hidden_rgb(mode):
    b = Image.new('RGBA', (3, 1))
    b.putdata([(255, 0, 255, 0), (80, 120, 170, 128), (80, 120, 170, 255)])
    s = Image.new('RGBA', (3, 1))
    s.putdata([(20, 180, 50, 128), (255, 90, 30, 64), (200, 0, 150, 0)])
    originals = b.tobytes(), s.tobytes()
    out = composite_blend(b, s, mode)
    assert out.getpixel((0, 0)) == (20, 180, 50, 128)
    assert out.getpixel((2, 0)) == (80, 120, 170, 255)
    assert abs(out.getpixel((1, 0))[3] - 160) <= 1
    assert originals == (b.tobytes(), s.tobytes())


@pytest.mark.parametrize('mode', sorted(BLEND_MODES))
def test_transparent_black_or_coloured_backdrop_never_tints_foreground(mode):
    source = Image.new('RGBA', (1, 1), (10, 100, 230, 57))
    a = composite_blend(Image.new('RGBA', (1, 1), (255, 0, 0, 0)), source, mode)
    b = composite_blend(Image.new('RGBA', (1, 1)), source, mode)
    assert a.tobytes() == b.tobytes() == source.tobytes()


def test_overlap_formula_is_not_straight_rgb_lerp():
    b = Image.new('RGBA', (1, 1), (51, 102, 153, 128))
    s = Image.new('RGBA', (1, 1), (204, 153, 102, 64))
    ab, alpha_s = 128/255, 64/255
    cb, cs = np.array([.2, .4, .6]), np.array([.8, .6, .4])
    alpha = alpha_s+ab*(1-alpha_s)
    rgb = (cs*alpha_s*(1-ab)+cb*ab*(1-alpha_s)+cb*cs*ab*alpha_s)/alpha
    expected = tuple(np.rint(np.r_[rgb, alpha]*255).astype(int))
    assert composite_blend(b, s, 'multiply').getpixel((0, 0)) == expected


def test_dodge_burn_boundary_priorities_and_softlight_high_branch():
    b = np.array([0, 1, .5], dtype=np.float32)
    s = np.array([1, 0, .75], dtype=np.float32)
    assert blend_rgb(b, s, 'color_dodge')[0] == 0
    assert blend_rgb(b, s, 'color_burn')[1] == 1
    assert blend_rgb(b, s, 'soft_light')[2] == pytest.approx(.5+.5*(np.sqrt(.5)-.5))


@pytest.mark.parametrize('mode', ['hue', 'saturation', 'color', 'luminosity'])
def test_nonseparable_luminance_and_gamut(mode):
    rng = np.random.default_rng(7)
    b, s = rng.random((20, 10, 3), dtype=np.float32), rng.random((20, 10, 3), dtype=np.float32)
    out = blend_rgb(b, s, mode)
    assert np.isfinite(out).all() and out.min() >= 0 and out.max() <= 1
    weights = np.array([.3, .59, .11])
    assert np.allclose(out@weights, (s if mode == 'luminosity' else b)@weights, atol=2e-6)


@pytest.mark.parametrize('mode', sorted(BLEND_MODES))
def test_graph_modes_are_modulatable_and_do_not_mutate(mode):
    spec = {'nodes': [{'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
                      {'id': 'm', 'type': 'composite', 'inputs': ['a', 'b']}], 'output': 'm'}
    g = EffectGraph(spec)
    images = {'a': Image.new('RGBA', (2, 3), (40, 60, 90, 180)),
              'b': Image.new('RGBA', (2, 3), (160, 100, 70, 130))}
    out = g.render(images, parameter_values={('m', 'mode'): mode})
    assert out.tobytes() == composite_blend(images['a'], images['b'], mode).tobytes()
    assert 'mode' not in spec['nodes'][2]


def test_legacy_add_formula_and_normal_over_remain_exact():
    rng = np.random.default_rng(31)
    images = [Image.fromarray(rng.integers(0, 256, (17, 19, 4), dtype=np.uint8)) for _ in range(2)]
    g = EffectGraph({'nodes': [{'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
                              {'id': 'm', 'type': 'composite', 'inputs': ['a', 'b']}], 'output': 'm'})
    assert g.render(dict(zip('ab', images))).tobytes() == Image.alpha_composite(*images).tobytes()
    a, b = [np.asarray(x, dtype=np.float32)/255 for x in images]
    ap, bp = a[..., 3:4], b[..., 3:4]
    alpha = 1-(1-ap)*(1-bp)
    premul = np.clip(a[..., :3]*ap+b[..., :3]*bp, 0, alpha)
    rgb = np.divide(premul, alpha, out=np.zeros_like(premul), where=alpha > 0)
    expected = Image.fromarray(np.uint8(np.rint(np.concatenate([rgb, alpha], axis=-1)*255)))
    assert g.render(dict(zip('ab', images)), parameter_values={('m', 'mode'): 'add'}).tobytes() == expected.tobytes()


@pytest.mark.parametrize('mode', ['oops', 0, {}, None])
def test_bad_mode_rejected(mode):
    with pytest.raises(ValueError):
        composite_blend(Image.new('RGBA', (1, 1)), Image.new('RGBA', (1, 1)), mode)


def test_shape_and_pixel_budget_rejected(monkeypatch):
    with pytest.raises(ValueError):
        composite_blend(Image.new('RGBA', (2, 1)), Image.new('RGBA', (1, 1)), 'multiply')
    monkeypatch.setattr('framerudiments.effect_graph_blend.MAX_BLEND_PIXELS', 1)
    with pytest.raises(ValueError, match='budget'):
        composite_blend(Image.new('RGBA', (2, 1)), Image.new('RGBA', (2, 1)), 'multiply')
