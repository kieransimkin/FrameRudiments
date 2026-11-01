"""Independent reference checks for M6.1 masks and pixel-centre distances."""
import math

import numpy as np
from PIL import Image, ImageDraw
import pytest

from framerudiments.effect_graph_alpha_geometry import (
    blur_alpha, contour_masks, morph_alpha, padded_alpha, place_mask, signed_distance,
)


def mask():
    im = Image.new('L', (9, 9))
    ImageDraw.Draw(im).rectangle((3, 3, 5, 5), fill=180)
    return im


def test_crop_is_padded_even_outside_canvas_and_budget_precedes_allocation():
    im = Image.new('L', (4, 3), 255)
    cropped, bounds = padded_alpha(im, 2)
    assert bounds == (-2, -2, 6, 5)
    assert cropped.getpixel((0, 0)) == 0
    assert cropped.getpixel((2, 2)) == 255
    assert im.getpixel((0, 0)) == 255
    with pytest.raises(ValueError, match='budget'):
        padded_alpha(im, 2, max_pixels=12)
    assert padded_alpha(Image.new('L', (3, 3)), 1) == (None, None)


@pytest.mark.parametrize('bad', [-1, 1025, True, 1.2])
def test_invalid_padding(bad):
    with pytest.raises(ValueError):
        padded_alpha(mask(), bad)


def test_fractional_morphology_matches_neighbouring_integer_masks():
    source = mask()
    before = source.tobytes()
    for sign in (-1, 1):
        actual = morph_alpha(source, sign * 1.5)
        expected = Image.blend(morph_alpha(source, sign), morph_alpha(source, sign * 2), .5)
        assert actual.tobytes() == expected.tobytes()
    assert source.tobytes() == before
    assert morph_alpha(source, 1).getbbox() == (2, 2, 7, 7)
    assert morph_alpha(source, -1).getbbox() == (4, 4, 5, 5)


def test_erosion_at_canvas_edge_sees_transparent_exterior():
    source = Image.new('L', (4, 4), 255)
    working, box = padded_alpha(source, 2)
    result = place_mask(morph_alpha(working, -1), source.size, *box[:2])
    assert result.getbbox() == (1, 1, 3, 3)


@pytest.mark.parametrize('position', ['outer', 'inner', 'center'])
def test_contour_bands_preserve_soft_coverage_and_do_not_mutate(position):
    source = mask()
    before = source.tobytes()
    outer, inner = contour_masks(source, 2, position)
    a = np.asarray(source)
    assert np.all(np.asarray(inner) <= a)
    assert np.all(np.asarray(outer)[a == 180] == 0)
    assert source.tobytes() == before
    assert outer.getextrema()[1] <= 180
    if position == 'outer':
        assert inner.getbbox() is None
    if position == 'inner':
        assert outer.getbbox() is None


def test_subpixel_mask_placement_and_large_offsets():
    source = Image.new('L', (1, 1), 200)
    result = place_mask(source, (4, 4), 1.5, 1.5)
    assert [result.getpixel(p) for p in ((1, 1), (2, 1), (1, 2), (2, 2))] == [50]*4
    assert int(np.asarray(result).sum()) == 200
    assert place_mask(source, (4, 4), 4096, -4096).getbbox() is None
    assert place_mask(source, (4, 4), -0.5, -0.5).getpixel((0, 0)) == 50
    assert source.getpixel((0, 0)) == 200


def test_blur_zero_is_independent_copy():
    source = mask()
    blurred = blur_alpha(source, 0)
    assert blurred is not source
    assert blurred.tobytes() == source.tobytes()


@pytest.mark.parametrize('function,args', [
    (morph_alpha, (float('nan'),)), (morph_alpha, (65,)),
    (blur_alpha, (float('inf'),)), (blur_alpha, (-1,)),
    (contour_masks, (2, 'bad')), (contour_masks, (True, 'outer')),
])
def test_geometry_rejects_bad_controls(function, args):
    with pytest.raises(ValueError):
        function(mask(), *args)


def brute_field(a, threshold=.5, limit=256):
    # Intentionally independent all-pairs reference, tiny fixtures only.
    binary = np.pad(a >= threshold*255, 1)
    inside_sites = np.argwhere(binary)
    outside_sites = np.argwhere(~binary)
    out = np.empty(a.shape, dtype=np.float32)
    for y in range(a.shape[0]):
        for x in range(a.shape[1]):
            inside = binary[y+1, x+1]
            sites = outside_sites if inside else inside_sites
            if len(sites) == 0:
                out[y, x] = limit
            else:
                distance = math.sqrt(min((int(yy)-y-1)**2 + (int(xx)-x-1)**2 for yy, xx in sites)) - .5
                out[y, x] = max(-limit, min(limit, -distance if inside else distance))
    return out


@pytest.mark.parametrize('seed', range(8))
def test_signed_distance_matches_independent_brute_force(seed):
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, size=(7, 9), dtype=np.uint8)
    source = Image.fromarray(a)
    got = signed_distance(source, limit=8)
    np.testing.assert_allclose(got, brute_field(a, limit=8), atol=1e-6)
    assert source.tobytes() == a.tobytes()


@pytest.mark.parametrize('fill', [0, 255])
def test_distance_degenerate_masks_and_image_edges(fill):
    source = Image.new('L', (5, 3), fill)
    np.testing.assert_allclose(signed_distance(source), brute_field(np.asarray(source)), atol=1e-6)


def test_distance_does_not_threshold_the_alpha_image_and_is_bounded():
    source = mask()
    before = source.tobytes()
    assert np.max(np.abs(signed_distance(source, limit=1))) <= 1
    assert source.tobytes() == before
    with pytest.raises(ValueError, match='budget'):
        signed_distance(source, max_pixels=2)
    for bad in (0, 1.5, float('nan'), True):
        with pytest.raises(ValueError):
            signed_distance(source, threshold=bad)
