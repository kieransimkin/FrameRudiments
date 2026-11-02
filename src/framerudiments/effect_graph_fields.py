"""M7.5 deterministic procedural RGBA textures, mattes and vector fields.

One reference input supplies canvas dimensions and optional coverage, never RGB.
Resolved DanceRudiment values are the sole animation input. Reuse M7.3 value
noise and M6.4 palette interpolation; do not introduce a second clock or RNG.
"""
from __future__ import annotations

from copy import deepcopy
import math

from PIL import Image

from .effect_graph_spatial import _noise
from .fractals import julia_escape_field
from .effect_graph_layer_overlays import (
    DEFAULT_STOPS, _gradient_lut, _gradient_paint, overlay_control_apply,
    overlay_control_base, overlay_parameter_descriptors, stop_control_parts, validate_overlay,
)

FIELD_TYPES = frozenset({
    'constant', 'linear', 'radial', 'angular', 'stripes', 'rings', 'checker',
    'noise', 'fbm', 'ridged', 'cellular', 'julia',
})
FIELD_ENUMS = {
    'field_type': FIELD_TYPES,
    'field_output': frozenset({'grayscale', 'alpha', 'color', 'vector'}),
    'field_alpha_source': frozenset({'opaque', 'current', 'original'}),
    'field_repeat': frozenset({'clamp', 'repeat', 'mirror'}),
    'field_invert': frozenset({False, True}),
    'field_vector': frozenset({'independent', 'gradient', 'curl'}),
    'field_color_space': frozenset({'srgb', 'linear_srgb', 'oklab'}),
    'field_interpolation': frozenset({'linear', 'smoothstep', 'constant'}),
}
FIELD_RANGES = {
    'field_value': (0, 1),
    'field_scale.x': (.25, 8192), 'field_scale.y': (.25, 8192),
    'field_center.x': (0, 1), 'field_center.y': (0, 1),
    'field_offset.x': (-4096, 4096), 'field_offset.y': (-4096, 4096),
    'field_angle': (-720, 720), 'field_phase': (-10000, 10000),
    'field_seed': (0, 10000), 'field_octaves': (1, 6),
    'field_lacunarity': (1, 4), 'field_persistence': (0, 1),
    'field_duty': (0, 1), 'field_softness': (0, 1), 'field_jitter': (0, 1),
    'field_contrast': (0, 8), 'field_bias': (-1, 1), 'field_gamma': (.1, 8),
    'field_vector_gain': (0, 64), 'field_derivative_step': (.25, 32),
    'field_julia_c.x': (-2, 2), 'field_julia_c.y': (-2, 2),
    'field_julia_orbit_radius': (0, 2), 'field_julia_phase': (-1, 1),
    'field_julia_zoom': (.05, 32), 'field_julia_rotation': (-1, 1),
    'field_julia_iterations': (1, 512), 'field_julia_escape_radius': (2, 64),
    'field_julia_power': (2, 8),
}
FIELD_DEFAULTS = {
    'field_type': 'noise', 'field_output': 'grayscale', 'field_alpha_source': 'opaque',
    'field_value': .5, 'field_scale': [64, 64], 'field_center': [.5, .5],
    'field_offset': [0, 0], 'field_angle': 0, 'field_phase': 0,
    'field_seed': 0, 'field_octaves': 3, 'field_lacunarity': 2, 'field_persistence': .5,
    'field_repeat': 'clamp', 'field_duty': .5, 'field_softness': .1, 'field_jitter': 1,
    'field_contrast': 1, 'field_bias': 0, 'field_gamma': 1, 'field_invert': False,
    'field_vector': 'curl', 'field_vector_gain': 1, 'field_derivative_step': 1,
    'field_color_space': 'srgb', 'field_interpolation': 'linear',
    'field_julia_c': [-.8, .156], 'field_julia_orbit_radius': 0,
    'field_julia_phase': 0, 'field_julia_zoom': 1, 'field_julia_rotation': 0,
    'field_julia_iterations': 96, 'field_julia_escape_radius': 4,
    'field_julia_power': 2,
}
FIELD_FIELDS = frozenset(FIELD_DEFAULTS) | {'field_palette'}
FIELD_CONTROLS = frozenset(FIELD_ENUMS) | frozenset(FIELD_RANGES)
_VECTORS = {'field_scale', 'field_center', 'field_offset', 'field_julia_c'}
_INTEGERS = {'field_seed', 'field_octaves', 'field_julia_iterations', 'field_julia_power'}
_GRAPH_FIELDS = {'id', 'type', 'inputs', 'opacity'}
MAX_PIXELS = 33_177_600
MAX_DIMENSION = 16384
MAX_TILE_PIXELS = 262144
MAX_WORK_UNITS = 1_000_000_000
TILE_ROWS = 48


def _number(value, name, bounds, *, integer=False):
    if (type(value) not in (float, int) or not math.isfinite(value)
            or not bounds[0] <= value <= bounds[1] or (integer and type(value) is not int)):
        raise ValueError(f'{name} must be {"an integer" if integer else "finite"} in {bounds}')


def _palette_config(config):
    # Do not expose the overlay's geometric settings in the field's namespace.
    return {
        'style_type': 'gradient_overlay',
        'gradient_stops': config.get('field_palette', DEFAULT_STOPS),
        'gradient_space': config.get('field_color_space', 'srgb'),
        'gradient_interpolation': config.get('field_interpolation', 'linear'),
    }


def _palette_path(name):
    if isinstance(name, str) and name.startswith('field_palette.'):
        return 'gradient_stops.' + name.removeprefix('field_palette.')
    return None


def field_control_valid(config, name):
    if not isinstance(name, str):
        return False
    return (name in FIELD_CONTROLS or
            (_palette_path(name) is not None and
             stop_control_parts(_palette_config(config), _palette_path(name)) is not None))


def validate_field(config, input_count=None):
    """Validate the complete resolved table before allocating image buffers."""
    if not isinstance(config, dict) or set(config) - FIELD_FIELDS - _GRAPH_FIELDS:
        raise ValueError('unknown procedural_field property')
    if input_count is not None and (type(input_count) is not int or input_count != 1):
        raise ValueError('procedural_field needs one canvas/coverage reference input')
    for name, choices in FIELD_ENUMS.items():
        value = config.get(name, FIELD_DEFAULTS[name])
        correct_type = type(value) is bool if name == 'field_invert' else isinstance(value, str)
        if not correct_type or value not in choices:
            raise ValueError(f'invalid {name}')
    for name in _VECTORS:
        values = config.get(name, FIELD_DEFAULTS[name])
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            raise ValueError(f'{name} requires two components')
        for axis, value in zip('xy', values):
            _number(value, f'{name}.{axis}', FIELD_RANGES[f'{name}.{axis}'])
    for name, bounds in FIELD_RANGES.items():
        if '.' not in name:
            _number(config.get(name, FIELD_DEFAULTS[name]), name, bounds, integer=name in _INTEGERS)
    _number(config.get('opacity', 1), 'opacity', (0, 1))
    validate_overlay(_palette_config(config))


def field_control_base(config, name):
    if not field_control_valid(config, name):
        raise ValueError('unknown procedural_field control')
    if _palette_path(name):
        return overlay_control_base(_palette_config(config), _palette_path(name))
    if '.' in name:
        base, axis = name.split('.')
        return config.get(base, FIELD_DEFAULTS[base])['xy'.index(axis)]
    return config.get(name, FIELD_DEFAULTS[name])


def field_control_apply(config, name, value):
    """Detached single-property update; joint palette constraints checked later."""
    if not field_control_valid(config, name):
        raise ValueError('unknown procedural_field control')
    out = dict(config)
    if _palette_path(name):
        updated = overlay_control_apply(_palette_config(config), _palette_path(name), value)
        out['field_palette'] = updated['gradient_stops']
        return out
    if name in FIELD_ENUMS:
        correct_type = type(value) is bool if name == 'field_invert' else isinstance(value, str)
        if not correct_type or value not in FIELD_ENUMS[name]:
            raise ValueError(f'invalid {name}')
    else:
        _number(value, name, FIELD_RANGES[name])
        if name in _INTEGERS:
            value = round(value)
    if '.' in name:
        base, axis = name.split('.')
        out[base] = list(config.get(base, FIELD_DEFAULTS[base]))
        out[base]['xy'.index(axis)] = value
    else:
        out[name] = value
    return out


def _active(config):
    kind = config.get('field_type', 'noise')
    active = {'opacity', 'field_type', 'field_output', 'field_alpha_source',
              'field_contrast', 'field_bias', 'field_gamma', 'field_invert'}
    if kind == 'constant':
        active.add('field_value')
    elif kind == 'julia':
        active.update({
            'field_scale', 'field_center', 'field_offset', 'field_angle',
            'field_julia_c', 'field_julia_orbit_radius', 'field_julia_phase',
            'field_julia_zoom', 'field_julia_rotation', 'field_julia_iterations',
            'field_julia_escape_radius', 'field_julia_power',
        })
    else:
        active.update({'field_scale', 'field_center', 'field_offset', 'field_phase'})
        # Rotation does not change radial scalar distance.
        if kind not in {'radial', 'rings'}:
            active.add('field_angle')
    if kind in {'noise', 'fbm', 'ridged', 'cellular'}:
        active.add('field_seed')
    if kind in {'fbm', 'ridged'}:
        active.update({'field_octaves', 'field_lacunarity', 'field_persistence'})
    if kind in {'stripes', 'rings', 'checker'}:
        active.update({'field_softness', 'field_duty'})
    if kind in {'linear', 'radial', 'angular'}:
        active.add('field_repeat')
    if kind == 'cellular':
        active.add('field_jitter')
    if config.get('field_output', 'grayscale') == 'color':
        active.update({'field_palette', 'field_color_space', 'field_interpolation'})
    if config.get('field_output') == 'vector':
        active.update({'field_vector', 'field_vector_gain'})
        if config.get('field_vector', 'curl') != 'independent':
            active.add('field_derivative_step')
    return active


def field_parameter_descriptors(config=None, *, input_count=None):
    config = {} if config is None else config
    validate_field(config, input_count)
    active, result = _active(config), {}
    for name in sorted(FIELD_CONTROLS | {'opacity'}):
        base = name.split('.')[0]
        value = config.get('opacity', 1) if name == 'opacity' else field_control_base(config, name)
        default = 1 if name == 'opacity' else field_control_base({}, name)
        item = dict(value=value, default=default, animatable=True, active=base in active)
        if name in FIELD_ENUMS:
            item.update(type='boolean' if name == 'field_invert' else 'enum',
                        choices=sorted(FIELD_ENUMS[name]), interpolation='discrete')
        else:
            unit = ('pixels' if base in {'field_scale', 'field_offset', 'field_derivative_step'} else
                    'degrees' if name == 'field_angle' else
                    'normalized_turn' if name in {'field_julia_phase', 'field_julia_rotation'} else
                    'iterations' if name == 'field_julia_iterations' else
                    'field_units' if name == 'field_phase' else
                    'integer' if name in _INTEGERS else 'ratio')
            item.update(type='integer' if name in _INTEGERS else 'number', units=unit,
                        range=list((0, 1) if name == 'opacity' else FIELD_RANGES[name]),
                        interpolation='nearest' if name in _INTEGERS else 'linear')
        result[name] = item
    for name, item in overlay_parameter_descriptors(_palette_config(config)).items():
        if name.startswith('gradient_stops.'):
            key = name.replace('gradient_stops.', 'field_palette.', 1)
            item = deepcopy(item)
            item.pop('styles', None)
            item.pop('outputs', None)
            # The overlay descriptor uses its configured value as "default".
            item['value'] = field_control_base(config, key)
            factory = field_control_valid({}, key)
            item['default'] = field_control_base({}, key) if factory else None
            item['active'] = 'field_palette' in active
            result[key] = item
    return deepcopy(result)


def field_capabilities(config=None, *, input_count=None):
    config = {} if config is None else config
    validate_field(config, input_count)
    result = {
        'schema': 'framerudiments.procedural-field.v1', 'backend': 'cpu', 'gpu_native': False,
        'input_count': 1, 'reference_rgb_used': False, 'canvas_expansion': False,
        'deterministic': True, 'temporal_history': False, 'alpha_lineage': 'new-generated-surface',
        'field_type': config.get('field_type', 'noise'),
        'output': config.get('field_output', 'grayscale'),
        'vector_encoding': {'channels': 'RG', 'neutral_byte': 128, 'negative_scale': 128,
                            'positive_scale': 127, 'blue_byte': 128},
        'max_pixels': MAX_PIXELS, 'max_dimension': MAX_DIMENSION,
        'max_tile_pixels': MAX_TILE_PIXELS, 'max_work_units': MAX_WORK_UNITS,
        'controls': field_parameter_descriptors(config, input_count=input_count),
    }
    if config.get('field_type', 'noise') == 'julia':
        result['fractal'] = {
            'family': 'julia', 'escape_time': True,
            'motion_normalized_range': [-1, 1],
            'periodic_control_span': 2,
            'periodic_control_radians': 2 * math.pi,
            'joined_endpoints': True,
        }
    return result


def _repeat(value, mode):
    import numpy as np
    if mode == 'repeat':
        return np.mod(value, 1)
    if mode == 'mirror':
        return 1 - np.abs(np.mod(value, 2) - 1)
    return np.clip(value, 0, 1)


def _band(coordinate, duty, softness):
    import numpy as np
    if duty == 0 or duty == 1:
        return np.full_like(coordinate, duty)
    # A centred band gives symmetric feathering at both wrap boundaries.
    distance = np.abs(np.mod(coordinate + .5, 1) - .5)
    half = duty * .5
    edge = min(duty, 1 - duty) * softness * .5
    if edge == 0:
        return (distance < half).astype(np.float64)
    t = np.clip((half + edge - distance) / (2 * edge), 0, 1)
    return t * t * (3 - 2 * t)


def _scalar(u, v, config):
    import numpy as np
    kind, phase = config.get('field_type', 'noise'), config.get('field_phase', 0)
    seed = config.get('field_seed', 0)
    if kind == 'constant':
        value = np.full_like(u, config.get('field_value', .5))
    elif kind == 'julia':
        value = julia_escape_field(
            u, v,
            constant=config.get('field_julia_c', [-.8, .156]),
            orbit_radius=config.get('field_julia_orbit_radius', 0),
            phase=config.get('field_julia_phase', 0),
            zoom=config.get('field_julia_zoom', 1),
            rotation=config.get('field_julia_rotation', 0),
            iterations=config.get('field_julia_iterations', 96),
            escape_radius=config.get('field_julia_escape_radius', 4),
            power=config.get('field_julia_power', 2),
        )
    elif kind in {'linear', 'radial', 'angular'}:
        coordinate = (u + .5 if kind == 'linear' else np.hypot(u, v) if kind == 'radial'
                      else np.arctan2(v, u) / (2 * math.pi) + .5)
        value = _repeat(coordinate + phase, config.get('field_repeat', 'clamp'))
    elif kind in {'stripes', 'rings', 'checker'}:
        duty, softness = config.get('field_duty', .5), config.get('field_softness', .1)
        value = _band((np.hypot(u, v) if kind == 'rings' else u) + phase, duty, softness)
        if kind == 'checker':
            other = _band(v + phase, duty, softness)
            value = value * (1 - other) + other * (1 - value)
    elif kind == 'cellular':
        x, y = u + phase, v + phase * .61803398875
        ix, iy = np.floor(x), np.floor(y)
        nearest = np.full_like(u, np.inf)
        jitter = .25 * config.get('field_jitter', 1)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                a, b = ix + dx, iy + dy
                fx = a + .5 + jitter * _noise(a, b, seed)
                fy = b + .5 + jitter * _noise(a, b, seed + 101)
                nearest = np.minimum(nearest, (x - fx)**2 + (y - fy)**2)
        # Features stay in the central half-cell: the 3x3 search is sufficient.
        value = np.clip(np.sqrt(nearest) / math.sqrt(2), 0, 1)
    else:
        count = 1 if kind == 'noise' else config.get('field_octaves', 3)
        frequency, weight, total = 1.0, 1.0, 0.0
        value = np.zeros_like(u)
        for octave in range(count):
            n = _noise(u * frequency + phase, v * frequency + phase * .61803398875, seed + octave * 17)
            value += weight * (1 - np.abs(n) if kind == 'ridged' else (n + 1) * .5)
            total += weight
            frequency *= config.get('field_lacunarity', 2)
            weight *= config.get('field_persistence', .5)
        value /= total
    value = np.clip((value - .5) * config.get('field_contrast', 1) + .5 + config.get('field_bias', 0), 0, 1)
    value = value ** config.get('field_gamma', 1)
    return 1 - value if config.get('field_invert', False) else value


def _transform(x, y, size, config):
    sx, sy = config.get('field_scale', [64, 64])
    cx, cy = config.get('field_center', [.5, .5])
    ox, oy = config.get('field_offset', [0, 0])
    angle = math.radians(config.get('field_angle', 0))
    c, s = math.cos(angle), math.sin(angle)
    # Scale first so rotation preserves the radial distance even for unequal axes.
    dx, dy = (x + .5 - cx * size[0] - ox) / sx, (y + .5 - cy * size[1] - oy) / sy
    return c * dx + s * dy, -s * dx + c * dy


def _vector(u, v, config):
    import numpy as np
    gain = config.get('field_vector_gain', 1)
    if gain == 0:
        return np.zeros_like(u), np.zeros_like(v)
    mode = config.get('field_vector', 'curl')
    if mode == 'independent':
        second = {**config, 'field_seed': config.get('field_seed', 0) + 101}
        a, b = 2 * _scalar(u, v, config) - 1, 2 * _scalar(u + 31.7, v - 19.3, second) - 1
    else:
        h = config.get('field_derivative_step', 1)
        sx, sy = config.get('field_scale', [64, 64])
        angle = math.radians(config.get('field_angle', 0))
        c, s = math.cos(angle), math.sin(angle)
        du, dv = h * c / sx, -h * s / sx
        a = (_scalar(u + du, v + dv, config) - _scalar(u - du, v - dv, config)) / (2 * h)
        du, dv = h * s / sy, h * c / sy
        b = (_scalar(u + du, v + dv, config) - _scalar(u - du, v - dv, config)) / (2 * h)
        if mode == 'curl':
            a, b = b, -a
    return np.clip(a * gain, -1, 1), np.clip(b * gain, -1, 1)


def field_work_units(size, config):
    """Conservative weighted scalar samples, not a promised time/RAM estimate."""
    kind = config.get('field_type', 'noise')
    per_sample = (config.get('field_julia_iterations', 96) if kind == 'julia' else
                  72 if kind == 'cellular' else
                  4 * config.get('field_octaves', 3) if kind in {'fbm', 'ridged'} else
                  4 if kind == 'noise' else 1)
    evaluations = 1
    if config.get('field_output') == 'vector':
        evaluations = 2 if config.get('field_vector', 'curl') == 'independent' else 4
    return size[0] * size[1] * per_sample * evaluations


def render_procedural_field(reference, config, *, original_alpha=None, tile_rows=TILE_ROWS):
    """Produce a new same-canvas surface; graph applies output opacity once.

    grayscale: scalar RGB, coverage alpha. alpha: white RGB, scalar*coverage
    alpha. color: shared premultiplied palette interpolation. vector: signed RG
    with exact byte-128 neutral. Coverage is opaque/current/original explicitly.
    """
    import numpy as np

    validate_field(config, 1)
    if (not isinstance(reference, Image.Image) or reference.mode != 'RGBA' or min(reference.size) < 1
            or max(reference.size) > MAX_DIMENSION or reference.width * reference.height > MAX_PIXELS):
        raise ValueError('procedural_field needs a nonempty RGBA reference within its canvas budget')
    if original_alpha is not None and (not isinstance(original_alpha, Image.Image)
            or original_alpha.mode != 'L' or original_alpha.size != reference.size):
        raise ValueError('original alpha must be a same-sized L image')
    if type(tile_rows) is not int or not 1 <= tile_rows <= 256:
        raise ValueError('tile_rows must be 1..256')
    if field_work_units(reference.size, config) > MAX_WORK_UNITS:
        raise ValueError('procedural_field exceeds its deterministic work budget')
    mode = config.get('field_output', 'grayscale')
    alpha_source = config.get('field_alpha_source', 'opaque')
    coverage = (None if alpha_source == 'opaque' else
                original_alpha if alpha_source == 'original' and original_alpha is not None else
                reference.getchannel('A'))
    paint_config = _palette_config(config)
    lut = _gradient_lut(paint_config) if mode == 'color' else None
    result = Image.new('RGBA', reference.size)
    tile_rows = min(tile_rows, max(1, MAX_TILE_PIXELS // reference.width))
    for top in range(0, reference.height, tile_rows):
        bottom = min(top + tile_rows, reference.height)
        y, x = np.indices((bottom - top, reference.width), dtype=np.float64)
        y += top
        u, v = _transform(x, y, reference.size, config)
        alpha = (np.ones_like(u) if coverage is None else
                 np.asarray(coverage.crop((0, top, reference.width, bottom)), dtype=np.float64) / 255)
        if mode == 'vector':
            a, b = _vector(u, v, config)
            def encode(q):
                return (128 + q * np.where(q >= 0, 127, 128)) / 255
            rgb = np.stack([encode(a), encode(b), np.full_like(a, 128 / 255)], axis=-1)
        else:
            value = _scalar(u, v, config)
            if mode == 'color':
                rgb, paint_alpha = _gradient_paint(value, np.zeros_like(value), (0, 0, 1, 1), paint_config, lut)
                alpha *= paint_alpha
            elif mode == 'alpha':
                rgb = np.ones((*u.shape, 3), dtype=np.float64)
                alpha *= value
            else:
                rgb = np.repeat(value[..., None], 3, axis=-1)
        rgba = np.uint8(np.rint(np.clip(np.concatenate([rgb, alpha[..., None]], axis=-1), 0, 1) * 255))
        rgba[rgba[..., 3] == 0, :3] = 0
        result.paste(Image.fromarray(rgba), (0, top))
    return result
