"""M8.3 deterministic alpha-safe Gaussian blur and bloom CPU reference.

The reference deliberately quantizes each separable pass to RGBA8 premultiplied
channels to match the bounded OpenGL 3.3 implementation. Original source alpha
lineage belongs to the graph, not this filter. No clock, mutable RNG or history.
"""
from __future__ import annotations

from copy import deepcopy
import math

from PIL import Image

FILTER_TYPES = frozenset({'blur', 'bloom'})
FILTER_ENUMS = {
    'filter_type': FILTER_TYPES,
    'filter_border': frozenset({'clamp', 'transparent'}),
}
FILTER_DEFAULTS = {
    'filter_type': 'blur', 'filter_radius': 1.5, 'filter_mix': 1.0,
    'filter_threshold': 0.7, 'filter_strength': 1.0, 'filter_border': 'clamp',
}
FILTER_RANGES = {
    'filter_radius': (0, 4), 'filter_mix': (0, 1),
    'filter_threshold': (0, .99), 'filter_strength': (0, 4),
}
FILTER_FIELDS = frozenset(FILTER_DEFAULTS)
FILTER_CONTROLS = FILTER_FIELDS
MAX_FILTER_PIXELS = 16_777_216
MAX_FILTER_DIMENSION = 16384
_GRAPH_FIELDS = frozenset({'id', 'type', 'inputs', 'opacity'})


def validate_filter(config, input_count=None):
    if not isinstance(config, dict) or set(config) - FILTER_FIELDS - _GRAPH_FIELDS:
        raise ValueError('unknown filter node property')
    if input_count is not None and input_count != 1:
        raise ValueError('filter needs exactly one input')
    for key, accepted in FILTER_ENUMS.items():
        value = config.get(key, FILTER_DEFAULTS[key])
        if type(value) is not str or value not in accepted:
            raise ValueError(f'invalid {key}')
    for key, (lo, hi) in FILTER_RANGES.items():
        v = config.get(key, FILTER_DEFAULTS[key])
        if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f'{key} outside [{lo}, {hi}]')
    opacity = config.get('opacity', 1)
    if type(opacity) not in (float, int) or not math.isfinite(opacity) or not 0 <= opacity <= 1:
        raise ValueError('invalid filter output opacity')


def filter_control_base(config, name):
    if name not in FILTER_CONTROLS:
        raise ValueError('unknown filter control')
    return config.get(name, FILTER_DEFAULTS[name])


def filter_control_apply(config, name, value):
    if name not in FILTER_CONTROLS:
        raise ValueError('unknown filter control')
    result = dict(config)
    result[name] = value
    validate_filter(result)
    return result


def filter_parameter_descriptors(config=None):
    config = {} if config is None else config
    validate_filter(config, 1)
    active = {'filter_type', 'filter_radius', 'filter_mix', 'filter_border'}
    if config.get('filter_type', 'blur') == 'bloom':
        active |= {'filter_threshold', 'filter_strength'}
    result = {}
    for key in sorted(FILTER_CONTROLS | {'opacity'}):
        value = config.get(key, FILTER_DEFAULTS.get(key, 1))
        default = FILTER_DEFAULTS.get(key, 1)
        row = dict(value=value, default=default, animatable=True, active=key in active or key == 'opacity')
        if key in FILTER_ENUMS:
            row.update(type='enum', choices=sorted(FILTER_ENUMS[key]), interpolation='discrete')
        else:
            row.update(type='number', range=list(FILTER_RANGES.get(key, (0, 1))),
                       units='pixels' if key == 'filter_radius' else 'ratio', interpolation='linear')
        result[key] = row
    return deepcopy(result)


def filter_capabilities(config=None):
    config = {} if config is None else config
    validate_filter(config, 1)
    return {
        'schema': 'framerudiments.effect-filter-capabilities.v1',
        'filter_type': config.get('filter_type', 'blur'),
        'gpu_native': True, 'gpu_bridge': True, 'gpu_resident_graph': False,
        'deterministic': True, 'alpha': 'premultiplied-separable-rgba8',
        'colour_space': 'display-referred', 'temporal_history': False,
        'max_pixels': MAX_FILTER_PIXELS, 'max_dimension': MAX_FILTER_DIMENSION,
        'controls': filter_parameter_descriptors(config),
    }


def _quantize(values):
    import numpy as np

    return (np.rint(np.clip(values, 0, 1) * 255).astype(np.uint8).astype(np.float32) / 255)


def _gaussian(premul, sigma, axis, border):
    import numpy as np

    if sigma <= 0:
        return premul.copy()
    radius = math.ceil(3 * sigma)
    weights = np.exp(-0.5 * (np.arange(-radius, radius + 1, dtype=np.float64) / sigma) ** 2)
    weights /= weights.sum()
    pad = [(0, 0)] * 3
    pad[axis] = (radius, radius)
    padded = np.pad(premul, pad, mode='edge' if border == 'clamp' else 'constant')
    out = np.zeros_like(premul, dtype=np.float32)
    for j, weight in enumerate(weights):
        if axis == 1:
            out += padded[:, j:j + premul.shape[1]] * float(weight)
        else:
            out += padded[j:j + premul.shape[0], :] * float(weight)
    return _quantize(out)


def _encoded(premul):
    import numpy as np

    a = np.clip(premul[..., 3:4], 0, 1)
    rgb = np.divide(premul[..., :3], a, out=np.zeros_like(premul[..., :3]), where=a > 1e-9)
    rgba = np.uint8(np.rint(np.clip(np.concatenate([rgb, a], -1), 0, 1) * 255))
    rgba[rgba[..., 3] == 0, :3] = 0
    return Image.fromarray(rgba, 'RGBA')


def render_filter(image, config):
    """Reference for GPU parity; each Gaussian pass has quantized RGBA8 storage."""
    import numpy as np

    validate_filter(config, 1)
    if not isinstance(image, Image.Image) or image.mode != 'RGBA' or not all(image.size):
        raise ValueError('filter requires nonempty RGBA')
    if image.width * image.height > MAX_FILTER_PIXELS or max(image.size) > MAX_FILTER_DIMENSION:
        raise ValueError('filter canvas exceeds pixel budget')
    kind = config.get('filter_type', 'blur')
    mix = config.get('filter_mix', 1)
    radius = config.get('filter_radius', 1.5)
    if mix == 0 or kind == 'blur' and radius == 0 or (kind == 'bloom' and config.get('filter_strength', 1) == 0):
        return image.copy()
    raw = np.asarray(image, dtype=np.float32) / 255
    premul = np.concatenate((raw[..., :3] * raw[..., 3:4], raw[..., 3:4]), axis=-1)
    if kind == 'bloom':
        brightness = raw[..., :3].max(axis=-1, keepdims=True)
        threshold = config.get('filter_threshold', .7)
        weight = np.clip((brightness - threshold) / (1 - threshold), 0, 1)
        working = _quantize(premul * weight)
    else:
        working = premul
    border = config.get('filter_border', 'clamp')
    h = _gaussian(working, radius, 1, border)
    v = _gaussian(h, radius, 0, border)
    if kind == 'blur':
        output = premul * (1 - mix) + v * mix
    else:
        strength = config.get('filter_strength', 1)
        glow = v * strength
        effective_a = np.clip(premul[..., 3:4] + glow[..., 3:4] * (1 - premul[..., 3:4]), 0, 1)
        effective_rgb = np.minimum(effective_a, premul[..., :3] + glow[..., :3])
        bright = np.concatenate((effective_rgb, effective_a), axis=-1)
        output = premul * (1 - mix) + bright * mix
    return _encoded(output)
