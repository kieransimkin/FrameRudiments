"""M6.4 colour blending, separate from Porter-Duff alpha compositing.

Formula reference: W3C Compositing and Blending Level 1, sections 9 and 10.
The existing renderer is display-referred SDR, not a scene-linear/HDR pipeline.
NumPy is loaded only when a numerical blend is requested. ``over`` and ``add``
retain the legacy Pillow/NumPy paths in EffectGraph, byte for byte.
"""
from PIL import Image

BLEND_MODES = frozenset({
    'normal', 'multiply', 'screen', 'overlay', 'darken', 'lighten',
    'color_dodge', 'color_burn', 'hard_light', 'soft_light', 'difference',
    'exclusion', 'hue', 'saturation', 'color', 'luminosity',
})
COMPOSITE_MODES = BLEND_MODES | {'over', 'add'}
MAX_BLEND_PIXELS = 67_108_864
TILE_ROWS = 64


def _lum(rgb):
    return .3 * rgb[..., 0:1] + .59 * rgb[..., 1:2] + .11 * rgb[..., 2:3]


def _sat(rgb):
    import numpy as np
    return np.max(rgb, axis=-1, keepdims=True) - np.min(rgb, axis=-1, keepdims=True)


def _clip_color(rgb):
    import numpy as np
    lum = _lum(rgb)
    low = np.min(rgb, axis=-1, keepdims=True)
    high = np.max(rgb, axis=-1, keepdims=True)
    # The spec defines n and x before either clipping branch.
    low_scale = np.divide(lum, lum-low, out=np.zeros_like(lum), where=lum != low)
    rgb = np.where(low < 0, lum + (rgb-lum)*low_scale, rgb)
    high_scale = np.divide(1-lum, high-lum, out=np.zeros_like(lum), where=high != lum)
    return np.where(high > 1, lum + (rgb-lum)*high_scale, rgb)


def _set_lum(rgb, lum):
    return _clip_color(rgb + lum - _lum(rgb))


def _set_sat(rgb, saturation):
    import numpy as np
    order = np.argsort(rgb, axis=-1, kind='stable')
    sorted_rgb = np.take_along_axis(rgb, order, axis=-1)
    low, mid, high = (sorted_rgb[..., i:i+1] for i in range(3))
    span = high-low
    middle = np.divide((mid-low)*saturation, span,
                       out=np.zeros_like(mid), where=span > 0)
    sorted_result = np.concatenate((np.zeros_like(low), middle,
                                    np.where(span > 0, saturation, 0)), axis=-1)
    result = np.empty_like(rgb)
    np.put_along_axis(result, order, sorted_result, axis=-1)
    return result


def blend_rgb(backdrop, paint, mode):
    """Return B(Cb,Cs) on straight RGB arrays; this function does not blend alpha."""
    import numpy as np
    if not isinstance(mode, str) or mode not in BLEND_MODES:
        raise ValueError('unsupported colour blend mode')
    b, s = np.broadcast_arrays(backdrop, paint)
    if mode == 'normal':
        result = s
    elif mode == 'multiply':
        result = b*s
    elif mode == 'screen':
        result = b+s-b*s
    elif mode == 'overlay':
        result = np.where(b <= .5, 2*b*s, 1-2*(1-b)*(1-s))
    elif mode == 'hard_light':
        result = np.where(s <= .5, 2*b*s, 1-2*(1-b)*(1-s))
    elif mode == 'soft_light':
        d = np.where(b <= .25, ((16*b-12)*b+4)*b, np.sqrt(np.maximum(b, 0)))
        result = np.where(s <= .5, b-(1-2*s)*b*(1-b), b+(2*s-1)*(d-b))
    elif mode == 'darken':
        result = np.minimum(b, s)
    elif mode == 'lighten':
        result = np.maximum(b, s)
    elif mode == 'color_dodge':
        ratio = np.divide(b, 1-s, out=np.ones_like(b), where=s < 1)
        result = np.where(b == 0, 0, np.minimum(1, ratio))
    elif mode == 'color_burn':
        ratio = np.divide(1-b, s, out=np.ones_like(b), where=s > 0)
        result = np.where(b == 1, 1, 1-np.minimum(1, ratio))
    elif mode == 'difference':
        result = np.abs(b-s)
    elif mode == 'exclusion':
        result = b+s-2*b*s
    elif mode == 'hue':
        result = _set_lum(_set_sat(s, _sat(b)), _lum(b))
    elif mode == 'saturation':
        result = _set_lum(_set_sat(b, _sat(s)), _lum(b))
    elif mode == 'color':
        result = _set_lum(s, _lum(b))
    else:  # luminosity
        result = _set_lum(b, _lum(s))
    return np.clip(result, 0, 1)


def composite_blend(backdrop, source, mode='normal'):
    """Alpha-aware source-over with a colour blend at overlapping coverage.

    Premultiplied result = Cs*As*(1-Ab) + Cb*Ab*(1-As) + B(Cb,Cs)*Ab*As.
    Fully transparent stored RGB never contributes to visible output. The
    foreground-only area remains its unblended colour, even for multiply.
    """
    import numpy as np
    if not isinstance(mode, str) or mode not in BLEND_MODES:
        raise ValueError('unsupported colour blend mode')
    if (not all(isinstance(x, Image.Image) and x.mode == 'RGBA' for x in (backdrop, source))
            or backdrop.size != source.size or min(backdrop.size) < 1):
        raise ValueError('blend requires equal nonempty RGBA images')
    if backdrop.width * backdrop.height > MAX_BLEND_PIXELS:
        raise ValueError('blend exceeds output pixel budget')
    if mode == 'normal':
        return Image.alpha_composite(backdrop, source)
    result = Image.new('RGBA', backdrop.size)
    for y in range(0, result.height, TILE_ROWS):
        box = (0, y, result.width, min(result.height, y+TILE_ROWS))
        b = np.asarray(backdrop.crop(box), dtype=np.float32)/255
        s = np.asarray(source.crop(box), dtype=np.float32)/255
        ab, a_s = b[..., 3:4], s[..., 3:4]
        alpha = a_s + ab*(1-a_s)
        premul = s[..., :3]*a_s*(1-ab) + b[..., :3]*ab*(1-a_s)
        premul += blend_rgb(b[..., :3], s[..., :3], mode)*ab*a_s
        rgb = np.divide(premul, alpha, out=np.zeros_like(premul), where=alpha > 0)
        pixels = np.concatenate((rgb, alpha), axis=-1)
        result.paste(Image.fromarray(np.uint8(np.rint(np.clip(pixels, 0, 1)*255))), (0, y))
    return result
