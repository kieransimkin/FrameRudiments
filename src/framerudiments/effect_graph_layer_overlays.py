"""M6.4 alpha-masked colour, gradient and procedural/graph-texture paint.

Paint is generated independently of the silhouette. Combined output shades
within current coverage; effect-only output uses the selected original/current
alpha. Temporary colour arrays are row-tiled. No file I/O, random state, new
clock or audio analysis is introduced.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from PIL import Image, ImageColor

from .effect_graph_alpha_geometry import finite
from .effect_graph_blend import BLEND_MODES, blend_rgb

OVERLAY_TYPES = frozenset({'color_overlay', 'gradient_overlay', 'pattern_overlay'})
OVERLAY_COLOURS = frozenset({'pattern_background'})
OVERLAY_ENUMS = {
    'style_blend': BLEND_MODES,
    'overlay_space': frozenset({'canvas', 'source'}),
    'gradient_kind': frozenset({'linear', 'radial', 'angular', 'reflected', 'diamond'}),
    'gradient_repeat': frozenset({'clamp', 'repeat', 'reflect'}),
    'gradient_direction': frozenset({'forward', 'reverse'}),
    'gradient_space': frozenset({'srgb', 'linear_srgb', 'oklab'}),
    'gradient_interpolation': frozenset({'linear', 'smoothstep', 'constant'}),
    'pattern_kind': frozenset({'checker', 'stripes', 'dots', 'grid', 'texture'}),
    'pattern_wrap': frozenset({'repeat', 'mirror', 'clamp'}),
}
VECTOR_DEFAULTS = {
    'gradient_center': [.5, .5], 'pattern_scale': [32, 32], 'pattern_offset': [0, 0],
}
OVERLAY_RANGES = {
    'gradient_angle': (-36000, 36000), 'gradient_scale': (.01, 100),
    'gradient_phase': (-1024, 1024),
    'gradient_center.x': (-4, 5), 'gradient_center.y': (-4, 5),
    'pattern_angle': (-36000, 36000),
    'pattern_scale.x': (1, 4096), 'pattern_scale.y': (1, 4096),
    'pattern_offset.x': (-65536, 65536), 'pattern_offset.y': (-65536, 65536),
    'pattern_duty': (0, 1), 'pattern_softness': (0, 1),
    'pattern_foreground_opacity': (0, 1), 'pattern_background_opacity': (0, 1),
    **{f'pattern_background.{c}': (0, 255) for c in 'rgb'},
}
DEFAULT_STOPS = (
    {'id': 'start', 'position': 0, 'color': '#000000', 'opacity': 1},
    {'id': 'end', 'position': 1, 'color': '#ffffff', 'opacity': 1},
)
OVERLAY_DEFAULTS = {
    **VECTOR_DEFAULTS,
    'style_blend': 'normal', 'overlay_space': 'source',
    'gradient_kind': 'linear', 'gradient_repeat': 'clamp', 'gradient_direction': 'forward',
    'gradient_space': 'srgb', 'gradient_interpolation': 'linear',
    'gradient_angle': 0, 'gradient_scale': 1, 'gradient_phase': 0,
    'gradient_stops': list(DEFAULT_STOPS),
    'pattern_kind': 'checker', 'pattern_wrap': 'repeat',
    'pattern_angle': 0, 'pattern_duty': .5, 'pattern_softness': 0,
    'pattern_background': '#ffffff',
    'pattern_foreground_opacity': 1, 'pattern_background_opacity': 1,
}
OVERLAY_FIELDS = frozenset(OVERLAY_DEFAULTS)
OVERLAY_CONTROLS = frozenset(OVERLAY_ENUMS) | frozenset(OVERLAY_RANGES)
MAX_STOPS = 16
MAX_PIXELS = 67_108_864
MAX_TEXTURE_PIXELS = 16_777_216
TILE_ROWS = 64
_ID = re.compile(r'[a-z][a-z0-9_-]{0,31}\Z')


def _colour(value):
    if not isinstance(value, str):
        raise ValueError('overlay colour must be an opaque CSS RGB string')
    try:
        rgb = ImageColor.getrgb(value)
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid overlay colour') from exc
    if len(rgb) != 3:
        raise ValueError('overlay colours must be opaque; use a separate opacity control')
    return rgb


def _stops(config):
    return config.get('gradient_stops', DEFAULT_STOPS)


def validate_overlay(config):
    """Validate nested stops as one resolved table, not during individual updates."""
    for name, accepted in OVERLAY_ENUMS.items():
        value = config.get(name, OVERLAY_DEFAULTS[name])
        if not isinstance(value, str) or value not in accepted:
            raise ValueError(f'invalid overlay {name}')
    for name, default in VECTOR_DEFAULTS.items():
        vec = config.get(name, default)
        if not isinstance(vec, (tuple, list)) or len(vec) != 2:
            raise ValueError(f'{name} requires two components')
        for axis, value in zip('xy', vec):
            finite(value, name, *OVERLAY_RANGES[f'{name}.{axis}'])
    for name, bounds in OVERLAY_RANGES.items():
        if '.' not in name:
            finite(config.get(name, OVERLAY_DEFAULTS[name]), name, *bounds)
    _colour(config.get('pattern_background', '#ffffff'))
    stops = _stops(config)
    if not isinstance(stops, (list, tuple)) or not 2 <= len(stops) <= MAX_STOPS:
        raise ValueError('gradient_stops requires 2..16 stops')
    ids, previous = set(), -1.0
    for stop in stops:
        if not isinstance(stop, dict) or set(stop) - {'id', 'position', 'color', 'opacity'}:
            raise ValueError('unknown gradient stop property')
        name = stop.get('id')
        if not isinstance(name, str) or _ID.fullmatch(name) is None or name in ids:
            raise ValueError('gradient stop ids must be unique stable lowercase identifiers')
        ids.add(name)
        position = stop.get('position')
        finite(position, 'gradient stop position', 0, 1)
        if position <= previous:
            raise ValueError('gradient stop positions must be strictly increasing')
        previous = position
        _colour(stop.get('color'))
        finite(stop.get('opacity', 1), 'gradient stop opacity', 0, 1)


def requires_texture(config):
    return (config.get('style_type') == 'pattern_overlay'
            and config.get('pattern_kind', 'checker') == 'texture')


def stop_control_parts(config, name):
    if not isinstance(name, str) or not name.startswith('gradient_stops.'):
        return None
    parts = name.split('.')
    if len(parts) not in (3, 4):
        return None
    suffix = '.'.join(parts[2:])
    if suffix not in {'position', 'opacity', 'color.r', 'color.g', 'color.b'}:
        return None
    for index, stop in enumerate(_stops(config)):
        if stop['id'] == parts[1]:
            return index, suffix
    return None


def overlay_control_base(config, name):
    parts = stop_control_parts(config, name)
    if parts is not None:
        index, suffix = parts
        stop = _stops(config)[index]
        if suffix.startswith('color.'):
            return _colour(stop['color'])['rgb'.index(suffix[-1])]
        return stop.get(suffix, 1)
    if name not in OVERLAY_CONTROLS:
        raise ValueError('unknown overlay control target')
    field = name.split('.')[0]
    if field in VECTOR_DEFAULTS:
        return config.get(field, VECTOR_DEFAULTS[field])['xy'.index(name[-1])]
    if field in OVERLAY_COLOURS:
        return _colour(config.get(field, OVERLAY_DEFAULTS[field]))['rgb'.index(name[-1])]
    return config.get(name, OVERLAY_DEFAULTS[name])


def overlay_control_apply(config, name, value):
    out = dict(config)
    parts = stop_control_parts(config, name)
    if parts is not None:
        index, suffix = parts
        finite(value, name, 0, 255 if suffix.startswith('color.') else 1)
        stops = deepcopy(list(_stops(config)))
        if suffix.startswith('color.'):
            rgb = list(_colour(stops[index]['color']))
            rgb['rgb'.index(suffix[-1])] = round(value)
            stops[index]['color'] = '#%02x%02x%02x' % tuple(rgb)
        else:
            stops[index][suffix] = value
        out['gradient_stops'] = stops
        return out
    if name not in OVERLAY_CONTROLS:
        raise ValueError('unknown overlay control target')
    if name in OVERLAY_ENUMS:
        if not isinstance(value, str) or value not in OVERLAY_ENUMS[name]:
            raise ValueError(f'invalid overlay {name}')
    else:
        finite(value, name, *OVERLAY_RANGES[name])
    field = name.split('.')[0]
    if field in VECTOR_DEFAULTS:
        vec = list(config.get(field, VECTOR_DEFAULTS[field]))
        vec['xy'.index(name[-1])] = value
        out[field] = vec
    elif field in OVERLAY_COLOURS:
        rgb = list(_colour(config.get(field, OVERLAY_DEFAULTS[field])))
        rgb['rgb'.index(name[-1])] = round(value)
        out[field] = '#%02x%02x%02x' % tuple(rgb)
    else:
        out[name] = value
    return out


def overlay_parameter_descriptors(config):
    descriptors = {}
    names = set(OVERLAY_CONTROLS)
    if config.get('style_type') == 'gradient_overlay':
        for stop in _stops(config):
            names.update(f"gradient_stops.{stop['id']}.{prop}"
                         for prop in ('position', 'opacity', 'color.r', 'color.g', 'color.b'))
    for name in sorted(names):
        item = dict(default=overlay_control_base(config, name), animatable=True)
        if name in OVERLAY_ENUMS:
            item.update(type='enum', choices=sorted(OVERLAY_ENUMS[name]), interpolation='discrete')
        else:
            bounds = OVERLAY_RANGES.get(name, (0, 255 if '.color.' in name else 1))
            units = ('channel_255' if bounds == (0, 255) else
                     'degrees' if name.endswith('_angle') else
                     'pixels' if name.startswith(('pattern_scale.', 'pattern_offset.')) else 'ratio')
            item.update(type='number', range=list(bounds), units=units, interpolation='linear')
        item['styles'] = (['gradient_overlay'] if name.startswith('gradient_') else
                          ['pattern_overlay'] if name.startswith('pattern_') else sorted(OVERLAY_TYPES))
        if name == 'style_blend':
            item['outputs'] = ['combined']
        descriptors[name] = item
    return deepcopy(descriptors)


def _linear(rgb):
    import numpy as np
    return np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)


def _srgb(rgb):
    import numpy as np
    return np.where(rgb <= .0031308, rgb*12.92,
                    1.055*np.maximum(rgb, 0)**(1/2.4)-.055)


def _to_lab(rgb):
    import numpy as np
    linear = _linear(rgb)
    lms = linear @ np.array([[.4122214708, .2119034982, .0883024619],
                             [.5363325363, .6806995451, .2817188376],
                             [.0514459929, .1073969566, .6299787005]], dtype=np.float32)
    return np.cbrt(lms) @ np.array([[.2104542553, 1.9779984951, .0259040371],
                                   [.7936177850, -2.4285922050, .7827717662],
                                   [-.0040720468, .4505937099, -.8086757660]], dtype=np.float32)


def _from_lab(lab):
    import numpy as np
    lms = lab @ np.array([[1, 1, 1], [.3963377774, -.1055613458, -.0894841775],
                          [.2158037573, -.0638541728, -1.2914855480]], dtype=np.float32)
    rgb = (lms**3) @ np.array([[4.0767416621, -1.2684380046, -.0041960863],
                               [-3.3077115913, 2.6097574011, -.7034186147],
                               [.2309699292, -.3413193965, 1.7076147010]], dtype=np.float32)
    return _srgb(rgb)


def _gradient_lut(config):
    import numpy as np
    stops = _stops(config)
    # Retain authored double precision for ordering: nearby valid stops must
    # not collapse to a zero-width interval when colour work uses float32.
    pos = np.asarray([s['position'] for s in stops], dtype=np.float64)
    rgb = np.asarray([_colour(s['color']) for s in stops], dtype=np.float32)/255
    alpha = np.asarray([s.get('opacity', 1) for s in stops], dtype=np.float32)
    space = config.get('gradient_space', 'srgb')
    rgb = _linear(rgb) if space == 'linear_srgb' else _to_lab(rgb) if space == 'oklab' else rgb
    return pos, rgb * alpha[:, None], alpha


def _gradient_paint(x, y, bounds, config, lut):
    import numpy as np
    left, top, right, bottom = bounds
    width, height = right-left, bottom-top
    cx, cy = config.get('gradient_center', [.5, .5])
    dx, dy = x-(left+cx*width), y-(top+cy*height)
    angle = math.radians(config.get('gradient_angle', 0))
    cosine, sine = math.cos(angle), math.sin(angle)
    scale = config.get('gradient_scale', 1)
    kind = config.get('gradient_kind', 'linear')
    radius = .5*min(width, height)*scale
    if kind == 'radial':
        coordinate = np.sqrt(dx*dx+dy*dy)/radius
    elif kind == 'diamond':
        u, v = cosine*dx+sine*dy, -sine*dx+cosine*dy
        coordinate = (np.abs(u)+np.abs(v))/radius
    elif kind == 'angular':
        coordinate = np.mod((np.arctan2(dy, dx)-angle)/(2*math.pi), 1)/scale
    else:
        span = (abs(cosine)*width+abs(sine)*height)*scale
        coordinate = .5+(cosine*dx+sine*dy)/span
        if kind == 'reflected':
            coordinate = np.abs(2*coordinate-1)
    if config.get('gradient_direction', 'forward') == 'reverse':
        coordinate = 1-coordinate
    coordinate = coordinate + config.get('gradient_phase', 0)
    repeat = config.get('gradient_repeat', 'clamp')
    if repeat == 'repeat':
        coordinate = np.mod(coordinate, 1)
    elif repeat == 'reflect':
        coordinate = 1-np.abs(np.mod(coordinate, 2)-1)
    else:
        coordinate = np.clip(coordinate, 0, 1)
    positions, colours, alphas = lut
    index = np.clip(np.searchsorted(positions, coordinate, side='right')-1, 0, len(positions)-2)
    t = np.clip((coordinate-positions[index])/(positions[index+1]-positions[index]), 0, 1).astype(np.float32)
    method = config.get('gradient_interpolation', 'linear')
    if method == 'smoothstep':
        t = t*t*(3-2*t)
    elif method == 'constant':
        t = (t >= 1).astype(np.float32)
    alpha = (1-t)*alphas[index]+t*alphas[index+1]
    premul = (1-t[..., None])*colours[index]+t[..., None]*colours[index+1]
    rgb = np.divide(premul, alpha[..., None], out=np.zeros_like(premul), where=alpha[..., None] > 0)
    space = config.get('gradient_space', 'srgb')
    rgb = _srgb(rgb) if space == 'linear_srgb' else _from_lab(rgb) if space == 'oklab' else rgb
    return np.clip(rgb, 0, 1), alpha


def _smooth(edge, value):
    import numpy as np
    t = np.clip(.5+value/(2*edge), 0, 1)
    return t*t*(3-2*t)


def _texture_paint(texture, u, v, wrap):
    """Periodic/clamped bilinear lookup in premultiplied colour, not straight RGB."""
    import numpy as np
    height, width = texture.shape[:2]
    sx, sy = u*width-.5, v*height-.5
    x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    tx, ty = sx-x0, sy-y0

    def wrapped(indices, size):
        if wrap == 'repeat':
            return np.mod(indices, size)
        if wrap == 'mirror':
            q = np.mod(indices, 2*size)
            return np.where(q < size, q, 2*size-1-q)
        return np.clip(indices, 0, size-1)

    shape = np.broadcast_shapes(u.shape, v.shape)
    out = np.zeros((*shape, 4), dtype=np.float32)
    for ox, oy, weight in ((0, 0, (1-tx)*(1-ty)), (1, 0, tx*(1-ty)),
                           (0, 1, (1-tx)*ty), (1, 1, tx*ty)):
        sample = texture[wrapped(y0+oy, height), wrapped(x0+ox, width)].astype(np.float32)/255
        sample[..., :3] *= sample[..., 3:4]
        out += sample * weight[..., None]
    rgb = np.divide(out[..., :3], out[..., 3:4], out=np.zeros_like(out[..., :3]), where=out[..., 3:4] > 0)
    return rgb, out[..., 3]


def _pattern_paint(x, y, bounds, config, texture):
    import numpy as np
    left, top, right, bottom = bounds
    angle = math.radians(config.get('pattern_angle', 0))
    cosine, sine = math.cos(angle), math.sin(angle)
    ox, oy = config.get('pattern_offset', [0, 0])
    scale_x, scale_y = config.get('pattern_scale', [32, 32])
    dx, dy = x-(left+right)/2-ox, y-(top+bottom)/2-oy
    u = (cosine*dx+sine*dy+(right-left)/2)/scale_x
    v = (-sine*dx+cosine*dy+(bottom-top)/2)/scale_y
    kind = config.get('pattern_kind', 'checker')
    if kind == 'texture':
        return _texture_paint(texture, u, v, config.get('pattern_wrap', 'repeat'))
    duty = config.get('pattern_duty', .5)
    softness = config.get('pattern_softness', 0)
    ex = max(1e-5, .5*(abs(cosine)+abs(sine))/scale_x + softness*.5)
    ey = max(1e-5, .5*(abs(cosine)+abs(sine))/scale_y + softness*.5)
    if kind == 'checker':
        # Smooth periodic square waves, with an explicit one-pixel AA footprint.
        qx = 2*_smooth(ex*math.pi, np.sin(math.pi*u))-1
        qy = 2*_smooth(ey*math.pi, np.sin(math.pi*v))-1
        weight = .5+.5*qx*qy
    else:
        du, dv = np.abs(np.mod(u+.5, 1)-.5), np.abs(np.mod(v+.5, 1)-.5)
        if kind == 'dots':
            weight = _smooth(max(ex, ey), duty*.5-np.sqrt(du*du+dv*dv))
        else:
            wu = _smooth(ex, duty*.5-du)
            weight = wu if kind == 'stripes' else np.maximum(wu, _smooth(ey, duty*.5-dv))
        if duty == 0:
            weight = np.zeros_like(weight)
        elif duty == 1 and kind != 'dots':
            weight = np.ones_like(weight)
    a = np.asarray(_colour(config.get('style_color', '#000000')), dtype=np.float32)/255
    b = np.asarray(_colour(config.get('pattern_background', '#ffffff')), dtype=np.float32)/255
    af, ab = config.get('pattern_foreground_opacity', 1), config.get('pattern_background_opacity', 1)
    alpha = weight*af+(1-weight)*ab
    premul = weight[..., None]*af*a+(1-weight[..., None])*ab*b
    rgb = np.divide(premul, alpha[..., None], out=np.zeros_like(premul), where=alpha[..., None] > 0)
    return rgb, alpha


def render_overlay(image, selected_alpha, config, *, pattern_image=None):
    """Paint over current coverage, or export selected-alpha paint on its own.

    At fill_opacity=1 combined output keeps current alpha byte-exact. Choosing
    original alpha restricts grown pixels but does not resurrect keyed pixels
    in combined mode. Effect-only mode explicitly exports original coverage.
    """
    import numpy as np
    validate_overlay(config)
    if (not isinstance(image, Image.Image) or image.mode != 'RGBA'
            or not isinstance(selected_alpha, Image.Image) or selected_alpha.mode != 'L'
            or selected_alpha.size != image.size or min(image.size) < 1):
        raise ValueError('overlay requires RGBA source and same-sized L alpha')
    if image.width*image.height > MAX_PIXELS:
        raise ValueError('overlay exceeds pixel budget')
    texture = None
    if requires_texture(config):
        if not isinstance(pattern_image, Image.Image) or pattern_image.mode != 'RGBA' or min(pattern_image.size) < 1:
            raise ValueError('texture pattern requires a second RGBA graph input')
        if pattern_image.width*pattern_image.height > MAX_TEXTURE_PIXELS:
            raise ValueError('pattern texture exceeds pixel budget')
        # uint8 storage: no full-frame float conversion, even for a large texture.
        texture = np.asarray(pattern_image)
    elif pattern_image is not None:
        raise ValueError('only texture patterns accept a second input')
    kind = config.get('style_type')
    if kind not in OVERLAY_TYPES:
        raise ValueError('unknown overlay style')
    bounds = ((0, 0, image.width, image.height) if config.get('overlay_space', 'source') == 'canvas'
              else selected_alpha.getbbox())
    effect_only = config.get('style_output', 'combined') == 'effect'
    if bounds is None or config.get('style_strength', 1) == 0:
        if effect_only:
            return Image.new('RGBA', image.size)
        result = image.copy()
        fill = config.get('fill_opacity', 1)
        if fill != 1:
            result.putalpha(result.getchannel('A').point(lambda a: round(a*fill)))
        return result
    lut = _gradient_lut(config) if kind == 'gradient_overlay' else None
    result = Image.new('RGBA', image.size)
    strength, fill = config.get('style_strength', 1), config.get('fill_opacity', 1)
    for y0 in range(0, image.height, TILE_ROWS):
        box = (0, y0, image.width, min(image.height, y0+TILE_ROWS))
        source = np.asarray(image.crop(box), dtype=np.float32)/255
        ref = np.asarray(selected_alpha.crop(box), dtype=np.float32)/255
        x = np.arange(image.width, dtype=np.float32)[None, :]+.5
        y = np.arange(y0, box[3], dtype=np.float32)[:, None]+.5
        if kind == 'color_overlay':
            rgb = np.asarray(_colour(config.get('style_color', '#000000')), dtype=np.float32)/255
            paint_alpha = np.ones(source.shape[:2], dtype=np.float32)
        elif kind == 'gradient_overlay':
            rgb, paint_alpha = _gradient_paint(x, y, bounds, config, lut)
        else:
            rgb, paint_alpha = _pattern_paint(x, y, bounds, config, texture)
        amount = paint_alpha*strength
        if effect_only:
            alpha = (ref*amount)[..., None]
            out_rgb = np.broadcast_to(rgb, source[..., :3].shape)
        else:
            coverage = source[..., 3]
            influence = np.divide(ref, coverage, out=np.zeros_like(ref), where=coverage > 0)
            amount = (amount*np.clip(influence, 0, 1))[..., None]
            denominator = amount+fill*(1-amount)
            alpha = source[..., 3:4]*denominator
            numerator = source[..., :3]*fill*(1-amount)
            numerator += blend_rgb(source[..., :3], rgb, config.get('style_blend', 'normal'))*amount
            out_rgb = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)
        out_rgb = np.where(alpha > 0, out_rgb, 0)
        pixels = np.concatenate((out_rgb, alpha), axis=-1)
        result.paste(Image.fromarray(np.uint8(np.rint(np.clip(pixels, 0, 1)*255))), (0, y0))
    return result
