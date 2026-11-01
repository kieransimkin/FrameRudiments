"""M6.1 alpha-only geometry. No colour, clock, renderer or project dependency.

Morphology uses a square structuring element, fractional radius interpolation
and explicit transparent borders. The separate distance helper is an exact
Euclidean pixel-centre transform; it is not substituted for coverage alpha.
"""
from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageFilter

MAX_WORK_PIXELS = 16_777_216
MAX_DISTANCE_PIXELS = 1_048_576


def check_alpha(alpha):
    if not isinstance(alpha, Image.Image) or alpha.mode != 'L':
        raise ValueError('alpha geometry requires an L image')
    if min(alpha.size) < 1:
        raise ValueError('alpha geometry requires a nonempty image')


def finite(value, name, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} must be finite and in [{low}, {high}]')
    return value


def padded_alpha(alpha, halo, *, max_pixels=MAX_WORK_PIXELS):
    """Return (zero-padded nonempty crop, canvas-space bounds), or (None, None).

    Do not clamp the crop to the canvas: a blurred or shifted silhouette outside
    the canvas may still contribute inside it. There is no wrapped edge sampling.
    """
    check_alpha(alpha)
    if type(halo) is not int or not 0 <= halo <= 1024:
        raise ValueError('alpha halo must be an integer in [0, 1024]')
    box = alpha.getbbox()
    if box is None:
        return None, None
    left, top, right, bottom = box
    box = left - halo, top - halo, right + halo, bottom + halo
    if (box[2] - box[0]) * (box[3] - box[1]) > max_pixels:
        raise ValueError('layer-style alpha workspace exceeds pixel budget')
    return alpha.crop(box), box


def morph_alpha(alpha, amount):
    """Square dilation (+) / erosion (-), interpolating neighbouring radii.

    Input must already include the transparent halo from padded_alpha(). This
    preserves coverage alpha, unlike thresholding a binary distance field.
    """
    check_alpha(alpha)
    finite(amount, 'morphology amount', -64, 64)
    radius = abs(float(amount))
    if radius == 0:
        return alpha.copy()
    lower, upper = math.floor(radius), math.ceil(radius)

    def sample(r):
        if not r:
            return alpha.copy()
        operation = ImageFilter.MaxFilter if amount > 0 else ImageFilter.MinFilter
        return alpha.filter(operation(2 * r + 1))

    first = sample(lower)
    return first if lower == upper else Image.blend(first, sample(upper), radius - lower)


def blur_alpha(alpha, radius):
    check_alpha(alpha)
    finite(radius, 'alpha blur', 0, 128)
    return alpha.copy() if radius == 0 else alpha.filter(ImageFilter.GaussianBlur(radius))


def contour_masks(alpha, width, position):
    """Return outer/inner alpha bands using soft-alpha morphology differences."""
    check_alpha(alpha)
    finite(width, 'outline width', 0, 64)
    if position not in {'outer', 'inner', 'center'}:
        raise ValueError('outline position must be outer, inner or center')
    outer_width = width if position == 'outer' else width / 2 if position == 'center' else 0
    inner_width = width if position == 'inner' else width / 2 if position == 'center' else 0
    outer = ImageChops.subtract(morph_alpha(alpha, outer_width), alpha)
    inner = ImageChops.subtract(alpha, morph_alpha(alpha, -inner_width))
    return outer, inner


def place_mask(mask, size, left, top):
    """Subpixel placement, bilinear zero-border sampling, one final quantisation.

    Integer placement is byte-exact. No source RGB is sampled, so hidden colour
    under zero alpha cannot generate halos. Off-canvas coordinates allocate no
    enlarged canvas, even for the largest allowed shadow offsets.
    """
    import numpy as np

    check_alpha(mask)
    finite(left, 'mask left', -100_000, 100_000)
    finite(top, 'mask top', -100_000, 100_000)
    width, height = size
    if width * height > 67_108_864 or min(size) < 1:
        raise ValueError('layer-style output canvas exceeds pixel budget')
    x0, y0 = math.floor(left), math.floor(top)
    fx, fy = left - x0, top - y0
    if fx == 0 and fy == 0:
        out = Image.new('L', size)
        out.paste(mask, (x0, y0))
        return out
    # Only the intersecting destination rectangle needs floating-point storage.
    bounds = max(0, x0), max(0, y0), min(width, x0 + mask.width + 1), min(height, y0 + mask.height + 1)
    out = Image.new('L', size)
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        return out
    accum = np.zeros((bounds[3] - bounds[1], bounds[2] - bounds[0]), dtype=np.float32)
    for ox, oy, weight in ((0, 0, (1-fx)*(1-fy)), (1, 0, fx*(1-fy)),
                           (0, 1, (1-fx)*fy), (1, 1, fx*fy)):
        if not weight:
            continue
        x, y = x0 + ox, y0 + oy
        lft, tp, rgt, btm = max(bounds[0], x), max(bounds[1], y), min(bounds[2], x+mask.width), min(bounds[3], y+mask.height)
        if lft < rgt and tp < btm:
            sample = np.asarray(mask.crop((lft-x, tp-y, rgt-x, btm-y)), dtype=np.float32)
            accum[tp-bounds[1]:btm-bounds[1], lft-bounds[0]:rgt-bounds[0]] += sample * weight
    out.paste(Image.fromarray(np.uint8(np.rint(np.clip(accum, 0, 255)))), bounds[:2])
    return out


def _squared_edt_rows(values):
    """Separable lower envelope of parabolas, vectorised over independent rows."""
    import numpy as np

    rows, width = values.shape
    row = np.arange(rows)
    vertices = np.zeros((rows, width), dtype=np.int32)
    boundaries = np.empty((rows, width + 1), dtype=np.float64)
    boundaries[:, 0], boundaries[:, 1] = -np.inf, np.inf
    count = np.zeros(rows, dtype=np.int32)
    for q in range(1, width):
        v = vertices[row, count]
        cross = ((values[:, q] + q*q) - (values[row, v] + v.astype(np.float64)**2)) / (2*(q-v))
        active = cross <= boundaries[row, count]
        while np.any(active):
            count[active] -= 1
            v = vertices[row, count]
            cross = ((values[:, q] + q*q) - (values[row, v] + v.astype(np.float64)**2)) / (2*(q-v))
            active = cross <= boundaries[row, count]
        count += 1
        vertices[row, count] = q
        boundaries[row, count] = cross
        boundaries[row, count + 1] = np.inf
    count[:] = 0
    out = np.empty_like(values, dtype=np.float64)
    for q in range(width):
        active = boundaries[row, count+1] < q
        while np.any(active):
            count[active] += 1
            active = boundaries[row, count+1] < q
        v = vertices[row, count]
        out[:, q] = (q-v).astype(np.float64)**2 + values[row, v]
    return out


def signed_distance(alpha, *, threshold=0.5, limit=256, max_pixels=MAX_DISTANCE_PIXELS):
    """Bounded Euclidean signed distance, negative inside, positive outside.

    Distances refer to opposite-class pixel centres with a half-pixel boundary
    correction; this is not an exact vector contour or an alpha reconstruction.
    An all-clear image is +limit. An opaque canvas sees transparent exterior.
    The field is computed on request only; ordinary styles need no distance map.
    """
    import numpy as np

    check_alpha(alpha)
    finite(threshold, 'distance threshold', 1/255, 1)
    finite(limit, 'distance limit', 1, 4096)
    if (alpha.width + 2) * (alpha.height + 2) > max_pixels:
        raise ValueError('distance field exceeds pixel budget')
    inside = np.pad(np.asarray(alpha, dtype=np.float64) / 255 >= threshold, 1)
    if not inside.any():
        return np.full((alpha.height, alpha.width), limit, dtype=np.float32)
    sentinel = float(4 * (inside.shape[0]**2 + inside.shape[1]**2 + 1))

    def distance_to(sites):
        values = np.where(sites, 0.0, sentinel)
        values = _squared_edt_rows(values)
        values = _squared_edt_rows(values.T).T
        return np.sqrt(np.maximum(values, 0))

    outside_distance = distance_to(inside)
    inside_distance = distance_to(~inside)
    result = np.where(inside, -(inside_distance - 0.5), outside_distance - 0.5)
    return np.asarray(np.clip(result[1:-1, 1:-1], -limit, limit), dtype=np.float32)
