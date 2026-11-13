"""M7.3 stateless, alpha-safe spatial warps for ordinary effect-graph layers.

The established geometric-transition and overlay samplers remain the source of
RGBA interpolation semantics. This module supplies inverse coordinate fields,
not another clock, renderer or spectral implementation. Coordinates are pixel
centres (0..width-1); alpha lineage is warped alongside the visible surface.
"""
from __future__ import annotations

from copy import deepcopy
import math

from PIL import Image

from .effect_graph_transition_geometry import _sample, _rotate
from .effect_graph_layer_overlays import _texture_paint
from .effect_graph_ripples import RIPPLE_VARIANTS, ripple_vectors

WARP_TYPES = frozenset({'wave', 'turbulence', 'swirl', 'radial', 'mirror', 'kaleidoscope', 'displacement',
                        'polar_unwrap', 'polar_wrap', 'raindrop_ripples'})
GPU_WARP_TYPES = ('wave', 'turbulence', 'swirl', 'radial', 'mirror', 'kaleidoscope',
                  'polar_unwrap', 'polar_wrap', 'displacement')
GPU_WARP_MAX_AMPLITUDE = 128
GPU_WARP_MIN_WAVELENGTH = 16
GPU_WARP_MIN_RADIAL_AMOUNT = 0
GPU_POLAR_WRAP_MIN_RADIUS = 16
WARP_ENUMS = {
    'warp_type': WARP_TYPES,
    'warp_border': frozenset({'transparent', 'clamp', 'repeat', 'mirror'}),
    'warp_axis': frozenset({'x', 'y', 'xy'}),
    'warp_map_x': frozenset({'r', 'g', 'b', 'a', 'luminance'}),
    'warp_map_y': frozenset({'r', 'g', 'b', 'a', 'luminance'}),
    'warp_ripple_variant': RIPPLE_VARIANTS,
}
WARP_RANGES = {
    'warp_mix': (0, 1),
    'warp_amplitude.x': (-4096, 4096), 'warp_amplitude.y': (-4096, 4096),
    'warp_scale.x': (1, 8192), 'warp_scale.y': (1, 8192),
    'warp_center.x': (0, 1), 'warp_center.y': (0, 1),
    'warp_angle': (-720, 720), 'warp_radius': (.5, 8192),
    'warp_amount': (-2, 2), 'warp_phase': (-64 * math.pi, 64 * math.pi),
    'warp_octaves': (1, 6), 'warp_seed': (0, 10000), 'warp_segments': (2, 32),
    'warp_neutral': (1 / 255, 254 / 255),
}
WARP_DEFAULTS = {
    'warp_type': 'wave', 'warp_border': 'transparent', 'warp_mix': 1,
    'warp_amplitude': [16, 16], 'warp_scale': [64, 64],
    'warp_center': [.5, .5], 'warp_angle': 90, 'warp_radius': 128,
    'warp_amount': .5, 'warp_phase': 0, 'warp_octaves': 3,
    'warp_seed': 0, 'warp_segments': 6, 'warp_axis': 'x',
    'warp_map_x': 'r', 'warp_map_y': 'g', 'warp_neutral': 128 / 255,
    'warp_ripple_variant': 'rings',
}
WARP_FIELDS = frozenset(WARP_DEFAULTS)
WARP_CONTROLS = frozenset(WARP_ENUMS) | frozenset(WARP_RANGES)
_INTEGER_CONTROLS = frozenset({'warp_octaves', 'warp_seed', 'warp_segments'})
_VECTORS = frozenset({'warp_amplitude', 'warp_scale', 'warp_center'})
_GRAPH_FIELDS = frozenset({'id', 'type', 'inputs', 'opacity'})
MAX_PIXELS = 33_177_600  # 7680 x 4320 (8K UHD); no silent quality reduction.
MAX_DIMENSION = 16384
MAX_TILE_PIXELS = 262144
TILE_ROWS = 48


def _finite(value, name, low, high, *, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not low <= value <= high or (integer and type(value) is not int)):
        raise ValueError(f'{name} must be {"an integer" if integer else "finite"} in [{low}, {high}]')


def validate_spatial(config, input_count=None):
    """Reject invalid fields and topology before allocating image buffers."""
    if not isinstance(config, dict) or set(config) - WARP_FIELDS - _GRAPH_FIELDS:
        raise ValueError('unknown spatial_warp property')
    for name, choices in WARP_ENUMS.items():
        value = config.get(name, WARP_DEFAULTS[name])
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f'invalid {name}')
    for name in _VECTORS:
        vector = config.get(name, WARP_DEFAULTS[name])
        if not isinstance(vector, (list, tuple)) or len(vector) != 2:
            raise ValueError(f'{name} requires a two-component vector')
        for axis, value in zip('xy', vector):
            _finite(value, f'{name}.{axis}', *WARP_RANGES[f'{name}.{axis}'])
    for name, bounds in WARP_RANGES.items():
        if '.' not in name:
            _finite(config.get(name, WARP_DEFAULTS[name]), name, *bounds, integer=name in _INTEGER_CONTROLS)
    _finite(config.get('opacity', 1), 'opacity', 0, 1)
    expected = 2 if config.get('warp_type', 'wave') == 'displacement' else 1
    if input_count is not None and (type(input_count) is not int or input_count != expected):
        raise ValueError(f'spatial_warp {config.get("warp_type", "wave")} requires {expected} inputs')


def spatial_control_base(config, name):
    if name not in WARP_CONTROLS:
        raise ValueError('unknown spatial_warp control')
    if '.' in name:
        vector, axis = name.split('.')
        return config.get(vector, WARP_DEFAULTS[vector])['xy'.index(axis)]
    return config.get(name, WARP_DEFAULTS[name])


def spatial_control_apply(config, name, value):
    """Apply one validated frame-local control without mutating authored data."""
    if name not in WARP_CONTROLS:
        raise ValueError('unknown spatial_warp control')
    if name in WARP_ENUMS:
        if not isinstance(value, str) or value not in WARP_ENUMS[name]:
            raise ValueError(f'invalid {name}')
    else:
        _finite(value, name, *WARP_RANGES[name])
        if name in _INTEGER_CONTROLS:
            value = round(value)
    out = dict(config)
    if '.' in name:
        field, axis = name.split('.')
        out[field] = list(config.get(field, WARP_DEFAULTS[field]))
        out[field]['xy'.index(axis)] = value
    else:
        out[name] = value
    return out


def _active(kind):
    common = {'opacity', 'warp_mix', 'warp_type', 'warp_border'}
    specific = {
        'wave': {'warp_amplitude', 'warp_scale', 'warp_phase'},
        'turbulence': {'warp_amplitude', 'warp_scale', 'warp_phase', 'warp_seed', 'warp_octaves'},
        'swirl': {'warp_center', 'warp_angle', 'warp_radius'},
        'radial': {'warp_center', 'warp_amount', 'warp_radius'},
        'mirror': {'warp_center', 'warp_axis'},
        'kaleidoscope': {'warp_center', 'warp_angle', 'warp_segments'},
        'displacement': {'warp_amplitude', 'warp_map_x', 'warp_map_y', 'warp_neutral'},
        'polar_unwrap': {'warp_center', 'warp_radius', 'warp_angle'},
        'polar_wrap': {'warp_center', 'warp_radius', 'warp_angle'},
        'raindrop_ripples': {'warp_center', 'warp_amplitude', 'warp_scale', 'warp_radius',
                             'warp_phase', 'warp_seed', 'warp_segments', 'warp_ripple_variant'},
    }
    return common | specific[kind]


def spatial_parameter_descriptors(config=None, *, input_count=None):
    config = {} if config is None else config
    validate_spatial(config, input_count)
    kind = config.get('warp_type', 'wave')
    active = _active(kind)
    descriptors = {}
    for name in sorted(WARP_CONTROLS | {'opacity'}):
        base = name.split('.')[0]
        value = config.get('opacity', 1) if name == 'opacity' else spatial_control_base(config, name)
        default = 1 if name == 'opacity' else spatial_control_base({}, name)
        item = dict(value=value, default=default, animatable=True, active=base in active)
        if name in WARP_ENUMS:
            choices = set(WARP_ENUMS[name])
            if name == 'warp_type' and input_count is not None:
                choices = ({'displacement'} if input_count == 2 else choices - {'displacement'})
            item.update(type='enum', choices=sorted(choices), interpolation='discrete', animatable=len(choices) > 1)
        else:
            units = ('pixels' if base in {'warp_amplitude', 'warp_scale', 'warp_radius'} else
                     'degrees' if name == 'warp_angle' else 'radians' if name == 'warp_phase' else
                     'integer' if name in _INTEGER_CONTROLS else 'ratio')
            item.update(type='integer' if name in _INTEGER_CONTROLS else 'number', units=units,
                        range=list((0, 1) if name == 'opacity' else WARP_RANGES[name]),
                        interpolation='nearest' if name in _INTEGER_CONTROLS else 'linear')
        descriptors[name] = item
    return deepcopy(descriptors)


def spatial_capabilities(config=None, *, input_count=None):
    config = {} if config is None else config
    validate_spatial(config, input_count)
    kind = config.get('warp_type', WARP_DEFAULTS['warp_type'])
    reason = gpu_spatial_rejection(config)
    gpu_native = reason is None
    return {
        'schema': 'framerudiments.spatial-warp-capability.v1',
        'warp_type': kind, 'backend': 'cpu', 'gpu_native': gpu_native,
        'gpu_bridge': gpu_native, 'gpu_resident_graph': gpu_native, 'gpu_rejection': reason,
        'deterministic': True, 'temporal_history': False,
        'input_count': 2 if config.get('warp_type') == 'displacement' else 1,
        'sampling': 'bilinear-premultiplied', 'alpha_lineage': 'same-coordinate-warp',
        'colour_space': 'display-referred', 'canvas_expansion': False,
        'max_pixels': MAX_PIXELS, 'max_dimension': MAX_DIMENSION, 'max_tile_pixels': MAX_TILE_PIXELS,
        'controls': spatial_parameter_descriptors(config, input_count=input_count),
    }


def _noise(x, y, seed):
    """Coherent quintic value noise with integer hashing, independent of global RNG."""
    import numpy as np

    ix, iy = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = x - ix, y - iy
    fx, fy = fx**3 * (fx * (fx * 6 - 15) + 10), fy**3 * (fy * (fy * 6 - 15) + 10)

    def lattice(dx, dy):
        # Reduce to uint32 explicitly; expected modular arithmetic never overflows
        # a numpy scalar, and negative lattice indices have defined wrap semantics.
        n = ((ix + dx) & 0xffffffff).astype(np.uint32) * np.uint32(0x1f123bb5)
        n ^= ((iy + dy) & 0xffffffff).astype(np.uint32) * np.uint32(0x5f356495)
        n ^= np.uint32(seed * 7919)
        n ^= n >> np.uint32(16)
        n *= np.uint32(0x7feb352d)
        n ^= n >> np.uint32(15)
        n *= np.uint32(0x846ca68b)
        n ^= n >> np.uint32(16)
        return (n.astype(np.float64) / 4294967295) * 2 - 1

    a = lattice(0, 0) * (1 - fx) + lattice(1, 0) * fx
    b = lattice(0, 1) * (1 - fx) + lattice(1, 1) * fx
    return a * (1 - fy) + b * fy


def _coordinates(x, y, size, config, field):
    import numpy as np

    kind = config.get('warp_type', 'wave')
    ax, ay = config.get('warp_amplitude', [16, 16])
    lx, ly = config.get('warp_scale', [64, 64])
    phase = config.get('warp_phase', 0)
    if kind == 'wave':
        return x - ax * np.sin(2 * math.pi * y / ly + phase), y - ay * np.sin(2 * math.pi * x / lx + phase)
    if kind == 'turbulence':
        dx, dy = np.zeros_like(x), np.zeros_like(y)
        total = 0.0
        seed = config.get('warp_seed', 0)
        for octave in range(config.get('warp_octaves', 3)):
            frequency, weight = 2**octave, .5**octave
            u, v = x / lx * frequency + phase, y / ly * frequency + phase * .61803398875
            dx += weight * _noise(u, v, seed + octave * 17)
            dy += weight * _noise(u + 31.7, v - 19.3, seed + octave * 17 + 101)
            total += weight
        return x - ax * dx / total, y - ay * dy / total
    if kind == 'displacement':
        neutral = config.get('warp_neutral', 128 / 255)

        def channel(name):
            if name == 'luminance':
                return field[..., :3] @ np.asarray([.2126, .7152, .0722])
            return field[..., 'rgba'.index(name)]

        def signed(name):
            value = channel(name)
            return (value - neutral) / np.where(value >= neutral, 1 - neutral, neutral) * field[..., 3]

        return x - ax * signed(config.get('warp_map_x', 'r')), y - ay * signed(config.get('warp_map_y', 'g'))
    if kind == 'raindrop_ripples':
        vx, vy = ripple_vectors(
            x, y, size,
            variant=config.get('warp_ripple_variant', 'rings'),
            centre=config.get('warp_center', [.5, .5]),
            radius=config.get('warp_radius', 128),
            wavelength=config.get('warp_scale', [64, 64])[0],
            phase=config.get('warp_phase', 0),
            count=config.get('warp_segments', 6),
            seed=config.get('warp_seed', 0),
        )
        return x - ax * vx, y - ay * vy
    cx, cy = config.get('warp_center', [.5, .5])
    cx, cy = cx * size[0] - .5, cy * size[1] - .5
    dx, dy = x - cx, y - cy
    if kind == 'mirror':
        axis = config.get('warp_axis', 'x')
        return (cx - np.abs(dx) if 'x' in axis else x,
                cy - np.abs(dy) if 'y' in axis else y)
    radius = np.hypot(dx, dy)
    angle = math.radians(config.get('warp_angle', 90))
    if kind == 'polar_unwrap':
        theta = (x + .5) / size[0] * 2 * math.pi + angle
        r = (y + .5) / size[1] * config.get('warp_radius', 128)
        return cx + r * np.cos(theta), cy + r * np.sin(theta)
    if kind == 'polar_wrap':
        theta = np.mod(np.arctan2(dy, dx) - angle, 2 * math.pi)
        return theta / (2 * math.pi) * size[0] - .5, radius / config.get('warp_radius', 128) * size[1] - .5
    if kind == 'kaleidoscope':
        sector = 2 * math.pi / config.get('warp_segments', 6)
        theta = np.arctan2(dy, dx) - angle
        folded = np.abs(np.mod(theta + sector / 2, sector) - sector / 2) + angle
        return cx + radius * np.cos(folded), cy + radius * np.sin(folded)
    limit = config.get('warp_radius', 128)
    if kind == 'swirl':
        turn = -angle * np.maximum(0, 1 - radius / limit)**2
        sx, sy = _rotate(dx, dy, turn)
        return cx + sx, cy + sy
    # Radial pinch/bulge is continuous at the authored radius and at the centre.
    power = math.exp(config.get('warp_amount', .5))
    mapped = limit * np.minimum(radius / limit, 1)**power
    ratio = np.divide(mapped, radius, out=np.ones_like(radius), where=radius > 0)
    ratio = np.where(radius < limit, ratio, 1)
    return cx + dx * ratio, cy + dy * ratio


def _sample_alpha(source, sx, sy, border):
    """The same bilinear footprint for single-channel source-alpha lineage."""
    import numpy as np

    height, width = source.shape
    x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    tx, ty = sx - x0, sy - y0
    out = np.zeros(sx.shape, np.float32)

    def index(v, size):
        if border == 'repeat':
            return np.mod(v, size)
        if border == 'mirror':
            q = np.mod(v, 2 * size)
            return np.where(q < size, q, 2 * size - 1 - q)
        return np.clip(v, 0, size - 1)

    for ox, oy, weight in ((0, 0, (1 - tx) * (1 - ty)), (1, 0, tx * (1 - ty)),
                           (0, 1, (1 - tx) * ty), (1, 1, tx * ty)):
        xi, yi = x0 + ox, y0 + oy
        valid = ((xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)) if border == 'transparent' else 1
        out += source[index(yi, height), index(xi, width)].astype(np.float32) / 255 * weight * valid
    return out


def spatial_is_identity(config):
    """Detect identities that need no access to the input pixels."""
    kind = config.get('warp_type', 'wave')
    if config.get('warp_mix', 1) == 0:
        return True
    if (kind in {'wave', 'turbulence', 'displacement', 'raindrop_ripples'}
            and not any(config.get('warp_amplitude', WARP_DEFAULTS['warp_amplitude']))):
        return True
    if kind == 'swirl' and config.get('warp_angle', 90) == 0:
        return True
    if kind == 'radial' and config.get('warp_amount', .5) == 0:
        return True
    return False


def spatial_map_is_transparent(map_image):
    """An entirely invisible control map is an exact identity."""
    return map_image is not None and map_image.getchannel('A').getbbox() is None


def gpu_spatial_rejection(config):
    """Report GPU support for one validated, resolved set of warp parameters."""
    kind = config.get('warp_type', WARP_DEFAULTS['warp_type'])
    if kind not in GPU_WARP_TYPES:
        return 'warp_type_not_implemented'
    if (kind == 'radial'
            and config.get('warp_amount', WARP_DEFAULTS['warp_amount']) < GPU_WARP_MIN_RADIAL_AMOUNT):
        # A negative amount gives the inverse map a steep derivative near the
        # centre; rounding that centre to a pixel can change its sampled colour.
        return 'warp_precision_limit_exceeded'
    if kind == 'polar_wrap' and not spatial_is_identity(config):
        if config.get('warp_border', WARP_DEFAULTS['warp_border']) != 'repeat':
            return 'polar_wrap_seam_requires_repeat'
        if config.get('warp_radius', WARP_DEFAULTS['warp_radius']) < GPU_POLAR_WRAP_MIN_RADIUS:
            return 'warp_precision_limit_exceeded'
    # Single-precision GLSL cannot preserve the fractional sampling footprint
    # at the combination of short wavelengths and very large displacement.
    if kind in {'wave', 'turbulence', 'displacement'} and not spatial_is_identity(config):
        amplitude = config.get('warp_amplitude', WARP_DEFAULTS['warp_amplitude'])
        wavelength = config.get('warp_scale', WARP_DEFAULTS['warp_scale'])
        if (max(map(abs, amplitude)) > GPU_WARP_MAX_AMPLITUDE
                or kind != 'displacement' and min(wavelength) < GPU_WARP_MIN_WAVELENGTH):
            return 'warp_precision_limit_exceeded'
    return None


def validate_spatial_images(image, *, map_image=None, original_alpha=None):
    if (not isinstance(image, Image.Image) or image.mode != 'RGBA' or min(image.size) < 1
            or image.width * image.height > MAX_PIXELS or max(image.size) > MAX_DIMENSION):
        raise ValueError('spatial_warp requires a nonempty RGBA canvas within its pixel budget')
    if map_image is not None and (not isinstance(map_image, Image.Image)
                                  or map_image.mode != 'RGBA' or map_image.size != image.size):
        raise ValueError('displacement map must be same-sized RGBA')
    if original_alpha is not None and (not isinstance(original_alpha, Image.Image)
                                      or original_alpha.mode != 'L' or original_alpha.size != image.size):
        raise ValueError('original alpha must be a same-sized L image')


def render_spatial_warp_with_alpha(image, config, *, map_image=None, original_alpha=None, tile_rows=TILE_ROWS):
    """Return (RGBA output, warped original-alpha metadata or None).

    No universal graph opacity is applied here. Source arrays are uint8; only
    row tiles become floating point. Visible colour and original alpha use the
    same inverse map, so downstream original-alpha styles follow deformed ink.
    """
    import numpy as np

    validate_spatial(config, 1 + (map_image is not None))
    validate_spatial_images(image, map_image=map_image, original_alpha=original_alpha)
    if type(tile_rows) is not int or not 1 <= tile_rows <= 256:
        raise ValueError('tile_rows must be 1..256')
    if spatial_is_identity(config) or spatial_map_is_transparent(map_image):
        return image.copy(), original_alpha.copy() if original_alpha is not None else None
    source = np.asarray(image, dtype=np.uint8)
    lineage = np.asarray(original_alpha, dtype=np.uint8) if original_alpha is not None else None
    out = Image.new('RGBA', image.size)
    out_alpha = Image.new('L', image.size) if lineage is not None else None
    border, mix = config.get('warp_border', 'transparent'), config.get('warp_mix', 1)
    tile_rows = min(tile_rows, max(1, MAX_TILE_PIXELS // image.width))
    for top in range(0, image.height, tile_rows):
        bottom = min(image.height, top + tile_rows)
        y, x = np.indices((bottom - top, image.width), dtype=np.float64)
        y += top
        field = (np.asarray(map_image.crop((0, top, image.width, bottom)), dtype=np.float64) / 255
                 if map_image is not None else None)
        sx, sy = _coordinates(x, y, image.size, config, field)
        if border == 'transparent':
            premul, alpha = _sample(source, sx, sy)
        else:
            rgb, alpha = _texture_paint(source, (sx + .5) / image.width, (sy + .5) / image.height, border)
            premul = rgb * alpha[..., None]
        if mix != 1:
            base = source[top:bottom].astype(np.float32) / 255
            premul = mix * premul + (1 - mix) * base[..., :3] * base[..., 3:4]
            alpha = mix * alpha + (1 - mix) * base[..., 3]
        rgb = np.divide(premul, alpha[..., None], out=np.zeros_like(premul), where=alpha[..., None] > 0)
        rgba = np.concatenate([rgb, alpha[..., None]], axis=-1)
        encoded = np.uint8(np.rint(np.clip(rgba, 0, 1) * 255))
        # Quantization can erase a tiny positive alpha. Canonicalize RGB after
        # quantization too, not only while dividing by floating-point alpha.
        encoded[encoded[..., 3] == 0, :3] = 0
        out.paste(Image.fromarray(encoded), (0, top))
        if lineage is not None:
            a = _sample_alpha(lineage, sx, sy, border)
            if mix != 1:
                a = mix * a + (1 - mix) * lineage[top:bottom].astype(np.float32) / 255
            out_alpha.paste(Image.fromarray(np.uint8(np.rint(np.clip(a, 0, 1) * 255))), (0, top))
    return out, out_alpha


def render_spatial_warp(image, config, *, map_image=None, tile_rows=TILE_ROWS):
    """Convenience API for callers without graph alpha-lineage metadata."""
    return render_spatial_warp_with_alpha(image, config, map_image=map_image, tile_rows=tile_rows)[0]
