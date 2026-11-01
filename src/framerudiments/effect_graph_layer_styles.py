"""M6.1/M6.2 alpha-derived styles for any unary effect-graph surface.

CPU reference in the existing display-referred, straight-RGBA contract. Shape
comes exclusively from selected alpha; RGB under transparency is never sampled
for morphology, blur or offset. The final canvas size is unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import math

from PIL import Image, ImageChops, ImageColor

from .effect_graph_alpha_geometry import (
    blur_alpha, check_alpha, contour_masks, finite, morph_alpha, padded_alpha, place_mask,
)

STYLE_TYPES = frozenset({'drop_shadow', 'inner_shadow', 'outer_glow', 'inner_glow', 'outline'})
STYLE_ENUMS = {
    'style_type': STYLE_TYPES,
    'style_output': frozenset({'combined', 'effect'}),
    'outline_position': frozenset({'outer', 'inner', 'center'}),
    'alpha_source': frozenset({'current', 'original'}),
}
STYLE_RANGES = {
    'style_width': (0, 64),
    'style_blur': (0, 128),
    'style_spread': (-64, 64),
    'style_strength': (0, 1),
    'fill_opacity': (0, 1),
    'style_offset.x': (-4096, 4096),
    'style_offset.y': (-4096, 4096),
    **{f'style_color.{c}': (0, 255) for c in 'rgb'},
}
STYLE_DEFAULTS = {
    'style_type': 'drop_shadow', 'style_output': 'combined',
    'outline_position': 'outer', 'alpha_source': 'current',
    'style_width': 3, 'style_blur': 0, 'style_spread': 0,
    'style_strength': 1, 'fill_opacity': 1,
    'style_offset': [4, 4], 'style_color': '#000000',
}
STYLE_FIELDS = frozenset(STYLE_DEFAULTS)
STYLE_CONTROLS = frozenset(STYLE_ENUMS) | frozenset(STYLE_RANGES)
_GRAPH_FIELDS = frozenset({'id', 'type', 'inputs', 'opacity'})


def _colour(value):
    if not isinstance(value, str):
        raise ValueError('style_color must be an opaque CSS RGB colour')
    try:
        result = ImageColor.getrgb(value)
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid style_color') from exc
    if len(result) != 3:
        raise ValueError('style_color must not include alpha; use style_strength')
    return result


def validate_style(config):
    if not isinstance(config, dict) or set(config) - STYLE_FIELDS - _GRAPH_FIELDS:
        raise ValueError('unknown layer_style property')
    for name, accepted in STYLE_ENUMS.items():
        value = config.get(name, STYLE_DEFAULTS[name])
        if not isinstance(value, str) or value not in accepted:
            raise ValueError(f'invalid layer_style {name}')
    _colour(config.get('style_color', STYLE_DEFAULTS['style_color']))
    offset = config.get('style_offset', STYLE_DEFAULTS['style_offset'])
    if not isinstance(offset, (list, tuple)) or len(offset) != 2:
        raise ValueError('style_offset must be a two-component vector')
    for value in offset:
        finite(value, 'style_offset', -4096, 4096)
    for name, bounds in STYLE_RANGES.items():
        if '.' not in name:
            finite(config.get(name, STYLE_DEFAULTS[name]), name, *bounds)


def style_control_base(config, name):
    if name not in STYLE_CONTROLS:
        raise ValueError('unknown layer_style control target')
    if name.startswith('style_offset.'):
        return config.get('style_offset', STYLE_DEFAULTS['style_offset'])[0 if name.endswith('.x') else 1]
    if name.startswith('style_color.'):
        return _colour(config.get('style_color', STYLE_DEFAULTS['style_color']))['rgb'.index(name[-1])]
    return config.get(name, STYLE_DEFAULTS[name])


def style_control_apply(config, name, value):
    """Frame-local updates; never mutate authored nested vectors or colours."""
    if name not in STYLE_CONTROLS:
        raise ValueError('unknown layer_style control target')
    if name in STYLE_ENUMS:
        if not isinstance(value, str) or value not in STYLE_ENUMS[name]:
            raise ValueError(f'invalid layer_style {name}')
    else:
        finite(value, name, *STYLE_RANGES[name])
    out = dict(config)
    if name.startswith('style_offset.'):
        offset = list(config.get('style_offset', STYLE_DEFAULTS['style_offset']))
        offset[0 if name.endswith('.x') else 1] = value
        out['style_offset'] = offset
    elif name.startswith('style_color.'):
        rgb = list(_colour(config.get('style_color', STYLE_DEFAULTS['style_color'])))
        rgb['rgb'.index(name[-1])] = round(value)
        out['style_color'] = '#%02x%02x%02x' % tuple(rgb)
    else:
        out[name] = value
    return out


def style_parameter_descriptors(config=None):
    """Discover native-control paths, values, units, ranges and applicability.

    This supplements, not replaces, the existing DanceRudiment binding engine.
    All returned metadata is independent of module defaults and caller config.
    """
    config = config or {}
    validate_style(config)
    descriptors = {}
    for name in sorted(STYLE_CONTROLS):
        item = {'default': style_control_base(config, name), 'animatable': True}
        if name in STYLE_ENUMS:
            item.update(type='enum', choices=sorted(STYLE_ENUMS[name]), interpolation='discrete')
        else:
            unit = ('channel_255' if name.startswith('style_color.') else
                    'ratio' if name in {'style_strength', 'fill_opacity'} else 'pixels')
            item.update(type='number', range=list(STYLE_RANGES[name]), units=unit,
                        interpolation='linear')
        if name.startswith('style_offset.'):
            item['styles'] = ['drop_shadow', 'inner_shadow']
        elif name in {'style_width', 'outline_position'}:
            item['styles'] = ['outline']
        elif name in {'style_blur', 'style_spread'}:
            item['styles'] = sorted(STYLE_TYPES - {'outline'})
        else:
            item['styles'] = sorted(STYLE_TYPES)
        descriptors[name] = item
    # Overall output opacity is applied once by EffectGraph, as for other nodes.
    descriptors['opacity'] = dict(type='number', default=config.get('opacity', 1),
                                  range=[0, 1], units='ratio', animatable=True,
                                  interpolation='linear', styles=sorted(STYLE_TYPES))
    return deepcopy(descriptors)


def style_bounds(alpha, config):
    """Conservative un-clipped canvas-space bounds, including blur/offset reach."""
    validate_style(config)
    check_alpha(alpha)
    box = alpha.getbbox()
    if box is None:
        return None
    kind = config.get('style_type', STYLE_DEFAULTS['style_type'])
    width = config.get('style_width', 3)
    radius = (width if kind == 'outline' else
              abs(config.get('style_spread', 0)) + 4*config.get('style_blur', 0))
    halo = math.ceil(radius) + 2
    left, top, right, bottom = box
    if kind in {'inner_shadow', 'inner_glow'}:
        return box
    dx, dy = config.get('style_offset', [4, 4]) if kind == 'drop_shadow' else (0, 0)
    outer = (math.floor(left-halo+dx), math.floor(top-halo+dy),
             math.ceil(right+halo+dx), math.ceil(bottom+halo+dy))
    return min(left, outer[0]), min(top, outer[1]), max(right, outer[2]), max(bottom, outer[3])


def _filled_source(image, amount):
    result = image.copy()
    if amount != 1:
        result.putalpha(result.getchannel('A').point(lambda a: round(a * amount)))
    return result


def _tint(mask, colour, strength):
    alpha = mask if strength == 1 else mask.point(lambda a: round(a * strength))
    # Do not manufacture coloured RGB underneath zero-alpha effect pixels.
    channels = [alpha.point(lambda a, c=c: c if a else 0) for c in colour]
    return Image.merge('RGBA', (*channels, alpha))


def _inner_surface(image, inner_mask, reference, colour, strength, fill):
    """Shade inside existing coverage without increasing its alpha at soft edges.

    Work in conditional coverage within the source silhouette. A fully filled
    layer keeps exactly its original alpha. Fill=0 leaves an effect-only edge.
    Float temporaries are bounded to 128 output rows, even on large canvases.
    """
    import numpy as np

    result = image.copy()
    for y in range(0, image.height, 128):
        box = (0, y, image.width, min(image.height, y+128))
        source = np.asarray(image.crop(box), dtype=np.float32) / 255
        ref = np.asarray(reference.crop(box), dtype=np.float32) / 255
        mask = np.asarray(inner_mask.crop(box), dtype=np.float32) / 255
        influence = np.divide(mask, ref, out=np.zeros_like(mask), where=ref > 0)
        influence = (np.clip(influence, 0, 1) * strength)[..., None]
        alpha = source[..., 3:4] * (influence + fill*(1-influence))
        rgb_numerator = (source[..., :3]*fill*(1-influence)
                         + np.asarray(colour, dtype=np.float32)/255*influence)
        denominator = influence + fill*(1-influence)
        rgb = np.divide(rgb_numerator, denominator, out=source[..., :3].copy(), where=denominator > 0)
        rgb = np.where(alpha > 0, rgb, 0)
        out = np.concatenate([rgb, alpha], axis=-1)
        result.paste(Image.fromarray(np.uint8(np.rint(np.clip(out, 0, 1)*255))), box[:2])
    return result


def render_layer_style(image, config, *, original_alpha=None):
    """Process one immutable RGBA input; return a same-size independent image.

    Styles extend beyond the source ink bounds, but the project canvas remains
    the explicit clipping boundary. Mask padding is independent of shadow offset.
    Matte/key operations before or after this node remain ordinary graph nodes.
    """
    validate_style(config)
    if not isinstance(image, Image.Image) or image.mode != 'RGBA' or min(image.size) < 1:
        raise ValueError('layer_style needs a nonempty RGBA input')
    if image.width * image.height > 67_108_864:
        raise ValueError('layer-style output canvas exceeds pixel budget')
    selected = image.getchannel('A')
    if config.get('alpha_source', 'current') == 'original' and original_alpha is not None:
        if (not isinstance(original_alpha, Image.Image) or original_alpha.mode != 'L'
                or original_alpha.size != image.size):
            raise ValueError('layer_style original alpha must be a same-size L image')
        selected = original_alpha
    effect_only = config.get('style_output', 'combined') == 'effect'
    fill = config.get('fill_opacity', 1)
    strength = config.get('style_strength', 1)
    if not strength or selected.getbbox() is None:
        return Image.new('RGBA', image.size) if effect_only else _filled_source(image, fill)
    kind = config.get('style_type', 'drop_shadow')
    spread, blur = config.get('style_spread', 0), config.get('style_blur', 0)
    width = config.get('style_width', 3)
    halo = math.ceil((width if kind == 'outline' else abs(spread) + 4*blur)) + 2
    mask, bounds = padded_alpha(selected, halo)
    left, top = bounds[:2]
    outer = Image.new('L', image.size)
    inner = Image.new('L', image.size)
    if kind == 'outline':
        outside, inside = contour_masks(mask, width, config.get('outline_position', 'outer'))
        outer = place_mask(outside, image.size, left, top)
        inner = place_mask(inside, image.size, left, top)
    elif kind == 'drop_shadow':
        shadow = blur_alpha(morph_alpha(mask, spread), blur)
        dx, dy = config.get('style_offset', [4, 4])
        outer = place_mask(shadow, image.size, left+dx, top+dy)
    elif kind == 'outer_glow':
        grown = blur_alpha(morph_alpha(mask, spread), blur)
        outer = place_mask(ImageChops.subtract(grown, mask), image.size, left, top)
    else:
        # Complement of eroded/blurred source coverage, bounded by its alpha.
        reduced = blur_alpha(morph_alpha(mask, -spread), blur)
        dx, dy = config.get('style_offset', [4, 4]) if kind == 'inner_shadow' else (0, 0)
        shifted = place_mask(reduced, image.size, left+dx, top+dy)
        inner = ImageChops.subtract(selected, shifted)
    colour = _colour(config.get('style_color', '#000000'))
    if effect_only:
        return _tint(ImageChops.lighter(outer, inner), colour, strength)
    source = (_inner_surface(image, inner, selected, colour, strength, fill)
              if inner.getbbox() else _filled_source(image, fill))
    if outer.getbbox() is None:
        return source
    return Image.alpha_composite(_tint(outer, colour, strength), source)
