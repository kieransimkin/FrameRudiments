"""M7.4 deterministic CPU pixel, scanline, CRT and glitch effects.

Inputs and outputs are straight RGBA. Spatial sampling, cell averages and mix
are premultiplied. No wall clock, global RNG, frame history or file I/O is used.
All animation is supplied by resolved native DanceRudiment control values.
"""
from __future__ import annotations

from copy import deepcopy
import math

from PIL import Image

from .effect_graph_transition_geometry import _sample
from .effect_graph_layer_overlays import _texture_paint
from .effect_graph_spatial import _sample_alpha

RASTER_TYPES = frozenset({'pixelate', 'pixel_sort', 'dither', 'scanlines', 'crt', 'glitch'})
RASTER_ENUMS = {
    'raster_type': RASTER_TYPES,
    'raster_border': frozenset({'transparent', 'clamp', 'repeat', 'mirror'}),
    'pixel_alpha': frozenset({'preserve', 'average'}),
    'sort_axis': frozenset({'x', 'y'}),
    'sort_key': frozenset({'luminance', 'brightness', 'red', 'green', 'blue', 'alpha'}),
    'sort_reverse': frozenset({True, False}),
    'dither_pattern': frozenset({'none', 'bayer2', 'bayer4', 'bayer8', 'noise'}),
    'dither_space': frozenset({'srgb', 'linear_srgb'}),
    'dither_monochrome': frozenset({True, False}),
    'scan_axis': frozenset({'x', 'y'}),
    'crt_mask': frozenset({'none', 'rgb', 'bgr'}),
}
RASTER_DEFAULTS = {
    'raster_type': 'pixelate', 'raster_mix': 1.0, 'raster_border': 'transparent',
    'raster_seed': 0, 'raster_phase': 0.0,
    'pixel_size': [8, 8], 'pixel_origin': [0, 0], 'pixel_alpha': 'preserve',
    'sort_axis': 'x', 'sort_key': 'luminance', 'sort_reverse': False,
    'sort_low': 0.0, 'sort_high': 1.0, 'sort_span': 256, 'sort_min_run': 2,
    'sort_alpha_floor': 1,
    'dither_pattern': 'bayer4', 'dither_levels': 4, 'dither_strength': 1.0,
    'dither_space': 'srgb', 'dither_monochrome': False, 'dither_origin': [0, 0],
    'scan_axis': 'y', 'scan_spacing': 4.0, 'scan_width': .5,
    'scan_strength': .5, 'scan_phase': 0.0,
    'crt_mask': 'rgb', 'crt_pitch': 1.0, 'crt_mask_strength': .35,
    'crt_curvature': .08, 'crt_vignette': .3, 'crt_gain': 1.0, 'crt_noise': 0.0,
    'glitch_band_height': 16, 'glitch_density': .3,
    'glitch_offset': [32.0, 0.0], 'glitch_chromatic': 0.0,
}
RASTER_RANGES = {
    'raster_mix': (0, 1), 'raster_seed': (0, 10000), 'raster_phase': (-10000, 10000),
    'pixel_size.x': (1, 1024), 'pixel_size.y': (1, 1024),
    'pixel_origin.x': (-4096, 4096), 'pixel_origin.y': (-4096, 4096),
    'sort_low': (0, 1), 'sort_high': (0, 1), 'sort_span': (2, 4096),
    'sort_min_run': (2, 4096), 'sort_alpha_floor': (1, 255),
    'dither_levels': (2, 256), 'dither_strength': (0, 1),
    'dither_origin.x': (-4096, 4096), 'dither_origin.y': (-4096, 4096),
    'scan_spacing': (1, 1024), 'scan_width': (0, 1),
    'scan_strength': (0, 1), 'scan_phase': (-10000, 10000),
    'crt_pitch': (1, 32), 'crt_mask_strength': (0, 1), 'crt_curvature': (0, 1),
    'crt_vignette': (0, 1), 'crt_gain': (0, 4), 'crt_noise': (0, 1),
    'glitch_band_height': (1, 1024), 'glitch_density': (0, 1),
    'glitch_offset.x': (-4096, 4096), 'glitch_offset.y': (-4096, 4096),
    'glitch_chromatic': (0, 64),
}
RASTER_FIELDS = frozenset(RASTER_DEFAULTS)
RASTER_CONTROLS = frozenset(RASTER_ENUMS) | frozenset(RASTER_RANGES)
_INTEGER = frozenset({
    'raster_seed', 'pixel_size.x', 'pixel_size.y', 'pixel_origin.x', 'pixel_origin.y',
    'sort_span', 'sort_min_run', 'sort_alpha_floor', 'dither_levels',
    'dither_origin.x', 'dither_origin.y', 'glitch_band_height',
})
_VECTORS = frozenset({'pixel_size', 'pixel_origin', 'dither_origin', 'glitch_offset'})
_GRAPH_FIELDS = frozenset({'id', 'type', 'inputs', 'opacity'})
MAX_PIXELS = 33_177_600
MAX_DIMENSION = 16384
MAX_TILE_PIXELS = 262144
TILE_ROWS = 48


def _finite(value, name, limits, *, integral=False):
    if (type(value) not in (float, int) or not limits[0] <= value <= limits[1]
            or not math.isfinite(value)
            or integral and type(value) is not int):
        raise ValueError(f'invalid raster_effect {name}: expected {limits}, integer={integral}')


def _enum(name, value):
    choices = RASTER_ENUMS[name]
    if not ((type(value) is bool or type(value) is str) and value in choices):
        raise ValueError(f'invalid raster_effect {name}')


def raster_control_base(config, name):
    if not isinstance(name, str) or name not in RASTER_CONTROLS:
        raise ValueError('unknown raster_effect control')
    if '.' in name:
        field, axis = name.split('.')
        return config.get(field, RASTER_DEFAULTS[field])['xy'.index(axis)]
    return config.get(name, RASTER_DEFAULTS[name])


def validate_raster(config, input_count=None):
    """Fail closed on schema, topology and allocation-independent constraints."""
    if not isinstance(config, dict) or set(config) - RASTER_FIELDS - _GRAPH_FIELDS:
        raise ValueError('unknown raster_effect property')
    if input_count is not None and (type(input_count) is not int or input_count != 1):
        raise ValueError('raster_effect requires exactly one input')
    for field in _VECTORS:
        value = config.get(field, RASTER_DEFAULTS[field])
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f'{field} requires a two-component vector')
    for name in RASTER_ENUMS:
        _enum(name, raster_control_base(config, name))
    for name, limits in RASTER_RANGES.items():
        _finite(raster_control_base(config, name), name, limits, integral=name in _INTEGER)
    _finite(config.get('opacity', 1), 'opacity', (0, 1))
    if config.get('sort_low', 0) > config.get('sort_high', 1):
        raise ValueError('sort_low must not exceed sort_high')
    if config.get('sort_min_run', 2) > config.get('sort_span', 256):
        raise ValueError('sort_min_run must not exceed sort_span')


def raster_control_apply(config, name, value):
    """Copy-on-write controls. Joint constraints are checked after all bindings."""
    if not isinstance(name, str) or name not in RASTER_CONTROLS:
        raise ValueError('unknown raster_effect control')
    if name in RASTER_ENUMS:
        _enum(name, value)
    else:
        _finite(value, name, RASTER_RANGES[name])
        if name in _INTEGER:
            value = math.floor(value + .5)
    result = dict(config)
    if '.' in name:
        field, axis = name.split('.')
        result[field] = list(config.get(field, RASTER_DEFAULTS[field]))
        result[field]['xy'.index(axis)] = value
    else:
        result[name] = value
    return result


def raster_parameter_descriptors(config=None, *, input_count=None):
    config = {} if config is None else config
    validate_raster(config, input_count)
    kind = config.get('raster_type', 'pixelate')
    prefixes = {
        'pixelate': ('pixel_',), 'pixel_sort': ('sort_',), 'dither': ('dither_',),
        'scanlines': ('scan_',), 'crt': ('crt_', 'scan_'), 'glitch': ('glitch_',),
    }[kind]
    active = {'raster_type', 'raster_mix', 'opacity'}
    if kind in {'crt', 'glitch'}:
        active.add('raster_border')
    if kind == 'glitch' or kind == 'dither' and config.get('dither_pattern') == 'noise' or kind == 'crt' and config.get('crt_noise', 0) > 0:
        active.update({'raster_seed', 'raster_phase'})
    out = {}
    for name in sorted(RASTER_CONTROLS | {'opacity'}):
        item = dict(animatable=True, active=name in active or name.startswith(prefixes),
                    default=1 if name == 'opacity' else raster_control_base({}, name),
                    value=config.get('opacity', 1) if name == 'opacity' else raster_control_base(config, name))
        if name in RASTER_ENUMS:
            item.update(type='enum', choices=sorted(RASTER_ENUMS[name]), interpolation='discrete')
        else:
            units = ('pixels' if name.startswith(('pixel_size.', 'pixel_origin.', 'dither_origin.', 'glitch_offset.'))
                     or name in {'sort_span', 'sort_min_run', 'glitch_band_height', 'glitch_chromatic', 'scan_spacing', 'crt_pitch'}
                     else 'channel_255' if name == 'sort_alpha_floor' else 'cycles' if name == 'scan_phase'
                     else 'state_index' if name == 'raster_phase' else 'integer' if name in _INTEGER else 'ratio')
            item.update(type='integer' if name in _INTEGER else 'number', units=units,
                        range=list((0, 1) if name == 'opacity' else RASTER_RANGES[name]),
                        interpolation='nearest-half-up' if name in _INTEGER else 'linear')
        if kind == 'dither' and config.get('dither_pattern', 'bayer4') == 'none':
            if name == 'dither_strength' or name.startswith('dither_origin.'):
                item['active'] = False
        if kind == 'crt' and config.get('crt_mask', 'rgb') == 'none':
            if name in {'crt_pitch', 'crt_mask_strength'}:
                item['active'] = False
        out[name] = item
    return deepcopy(out)


def raster_capabilities(config=None, *, input_count=None):
    config = {} if config is None else config
    validate_raster(config, input_count)
    return dict(schema='framerudiments.raster-effect.v1', backend='cpu', gpu_native=False,
                input_count=1, deterministic=True, temporal_history=False, canvas_expansion=False,
                colour_space='display-referred; optional linear-sRGB quantization',
                max_pixels=MAX_PIXELS, max_dimension=MAX_DIMENSION,
                max_tile_pixels=MAX_TILE_PIXELS, max_sort_span=4096,
                controls=raster_parameter_descriptors(config, input_count=input_count))


def raster_is_identity(config):
    if config.get('raster_mix', 1) == 0:
        return True
    kind = config.get('raster_type', 'pixelate')
    if kind == 'pixelate':
        return config.get('pixel_size', [8, 8]) in ([1, 1], (1, 1))
    if kind == 'scanlines':
        return not config.get('scan_strength', .5) or not config.get('scan_width', .5)
    if kind == 'glitch':
        return not config.get('glitch_density', .3) or not (any(config.get('glitch_offset', [32, 0])) or config.get('glitch_chromatic', 0))
    return False


def _hash01(x, y, seed, phase, salt=0):
    """Defined uint32 hash; absolute coordinates make tiling/seek irrelevant."""
    import numpy as np
    x, y = np.broadcast_arrays(np.asarray(x, dtype=np.int64), np.asarray(y, dtype=np.int64))
    n = (x & 0xffffffff).astype(np.uint32) * np.uint32(0x1f123bb5)
    n ^= (y & 0xffffffff).astype(np.uint32) * np.uint32(0x5f356495)
    n ^= np.uint32((seed * 7919 + math.floor(phase) * 104729 + salt * 15485863) & 0xffffffff)
    n ^= n >> np.uint32(16)
    n *= np.uint32(0x7feb352d)
    n ^= n >> np.uint32(15)
    n *= np.uint32(0x846ca68b)
    n ^= n >> np.uint32(16)
    return n.astype(np.float64) / 4294967296.0


def _box_coverage(coordinate, period, duty, phase=0):
    """Exact unit-pixel coverage of a periodic dark interval; phase in cycles."""
    import numpy as np
    def integral(t):
        whole = np.floor(t)
        return whole * duty + np.minimum(t - whole, duty)
    return np.clip(period * (integral((coordinate + .5) / period + phase)
                             - integral((coordinate - .5) / period + phase)), 0, 1)


def _encode(premul, alpha):
    import numpy as np
    rgb = np.divide(premul, alpha[..., None], out=np.zeros_like(premul), where=alpha[..., None] > 0)
    value = np.uint8(np.rint(np.clip(np.concatenate([rgb, alpha[..., None]], axis=-1), 0, 1) * 255))
    value[value[..., 3] == 0, :3] = 0
    return value


def _premul(source):
    import numpy as np
    value = source.astype(np.float64) / 255
    return value[..., :3] * value[..., 3:4], value[..., 3]


def _sample_border(source, x, y, border):
    if border == 'transparent':
        return _sample(source, x, y)
    rgb, alpha = _texture_paint(source, (x + .5) / source.shape[1], (y + .5) / source.shape[0], border)
    return rgb * alpha[..., None], alpha


def _pixelate(source, lineage, config, rows):
    """Area-weighted cell means, accumulated in bounded strips, not RGB resizes."""
    import numpy as np
    height, width = source.shape[:2]
    bx, by = config.get('pixel_size', [8, 8])
    ox, oy = config.get('pixel_origin', [0, 0])
    xs = np.r_[0, np.arange(ox % bx or bx, width, bx)].astype(np.int64)
    widths = np.diff(np.r_[xs, width])
    ys = np.r_[0, np.arange(oy % by or by, height, by), height].astype(np.int64)
    result = Image.new('RGBA', (width, height))
    original = Image.new('L', (width, height)) if lineage is not None else None
    preserve = config.get('pixel_alpha', 'preserve') == 'preserve'
    mix = config.get('raster_mix', 1)
    for top, bottom in zip(ys, ys[1:]):
        sums = np.zeros((len(xs), 4), np.float64)
        lsums = np.zeros(len(xs), np.float64) if lineage is not None and not preserve else None
        for y in range(int(top), int(bottom), rows):
            end = min(int(bottom), y + rows)
            p, a = _premul(source[y:end])
            sums += np.add.reduceat(np.concatenate([p, a[..., None]], axis=-1), xs, axis=1).sum(axis=0)
            if lsums is not None:
                lsums += np.add.reduceat(lineage[y:end].astype(np.float64) / 255, xs, axis=1).sum(axis=0)
        mean = sums / (widths * (bottom - top))[:, None]
        colours = np.divide(mean[:, :3], mean[:, 3:4], out=np.zeros_like(mean[:, :3]), where=mean[:, 3:4] > 0)
        rgbrow, arow = np.repeat(colours, widths, axis=0), np.repeat(mean[:, 3], widths)
        lrow = np.repeat(lsums / (widths * (bottom - top)), widths) if lsums is not None else None
        for y in range(int(top), int(bottom), rows):
            end = min(int(bottom), y + rows)
            bp, ba = _premul(source[y:end])
            a = ba if preserve else np.broadcast_to(arow, ba.shape)
            p = rgbrow[None, ...] * a[..., None]
            result.paste(Image.fromarray(_encode(mix * p + (1 - mix) * bp, mix * a + (1 - mix) * ba)), (0, y))
            if original is not None:
                base = lineage[y:end].astype(np.float64) / 255
                transformed = base if preserve else np.broadcast_to(lrow, base.shape)
                original.paste(Image.fromarray(np.uint8(np.rint((mix * transformed + (1 - mix) * base) * 255))), (0, y))
    return result, original


def _sort_metric(line, name):
    import numpy as np
    if name in {'red', 'green', 'blue', 'alpha'}:
        return line[:, {'red': 0, 'green': 1, 'blue': 2, 'alpha': 3}[name]].astype(np.float64) / 255
    rgb = line[:, :3].astype(np.float64) / 255
    return rgb.max(axis=1) if name == 'brightness' else rgb @ np.array([.2126, .7152, .0722])


def _pixel_sort(source, lineage, config):
    """Stable per-run permutation; never cross a transparent/ineligible gap."""
    import numpy as np
    vertical = config.get('sort_axis', 'x') == 'y'
    src = source.swapaxes(0, 1) if vertical else source
    lin = lineage.T if vertical and lineage is not None else lineage
    output = np.empty_like(src)
    alpha_output = np.empty_like(lin) if lin is not None else None
    low, high = config.get('sort_low', 0), config.get('sort_high', 1)
    span, minimum = config.get('sort_span', 256), config.get('sort_min_run', 2)
    mix = config.get('raster_mix', 1)
    for i, line in enumerate(src):
        metric = _sort_metric(line, config.get('sort_key', 'luminance'))
        eligible = (line[:, 3] >= config.get('sort_alpha_floor', 1)) & (metric >= low) & (metric <= high)
        edges = np.flatnonzero(np.diff(np.r_[False, eligible, False].astype(np.int8)))
        order = np.arange(len(line))
        for start, end in zip(edges[::2], edges[1::2]):
            for at in range(int(start), int(end), span):
                stop = min(int(end), at + span)
                if stop - at < minimum:
                    continue
                keys = metric[at:stop]
                rank = np.argsort(-keys if config.get('sort_reverse', False) else keys, kind='stable')
                order[at:stop] = at + rank
        if mix == 1:
            output[i] = line[order]
        else:
            bp, ba = _premul(line)
            p, a = _premul(line[order])
            output[i] = _encode(mix * p + (1 - mix) * bp, mix * a + (1 - mix) * ba)
        output[i, output[i, :, 3] == 0, :3] = 0
        if lin is not None:
            alpha_output[i] = np.uint8(np.rint(mix * lin[i, order] + (1 - mix) * lin[i]))
    if vertical:
        output = output.swapaxes(0, 1)
        if alpha_output is not None:
            alpha_output = alpha_output.T
    return Image.fromarray(output), Image.fromarray(alpha_output) if alpha_output is not None else None


def _dither(rgb, x, y, config):
    import numpy as np
    linear = config.get('dither_space', 'srgb') == 'linear_srgb'
    if linear:
        rgb = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055)**2.4)
    if config.get('dither_monochrome', False):
        rgb = np.repeat((rgb @ np.array([.2126, .7152, .0722]))[..., None], 3, axis=-1)
    pattern = config.get('dither_pattern', 'bayer4')
    ox, oy = config.get('dither_origin', [0, 0])
    if pattern.startswith('bayer'):
        size = int(pattern[-1])
        matrix = np.array([[0]], dtype=np.int64)
        while len(matrix) < size:
            matrix = np.block([[4 * matrix, 4 * matrix + 2], [4 * matrix + 3, 4 * matrix + 1]])
        noise = (matrix[(y.astype(np.int64) - oy) % size, (x.astype(np.int64) - ox) % size] + .5) / size**2 - .5
    elif pattern == 'noise':
        noise = _hash01(x - ox, y - oy, config.get('raster_seed', 0), config.get('raster_phase', 0)) - .5
    else:
        noise = np.zeros(x.shape)
    scale = config.get('dither_levels', 4) - 1
    value = np.clip(rgb * scale + noise[..., None] * config.get('dither_strength', 1), 0, scale)
    rgb = np.floor(value + .5) / scale
    if linear:
        rgb = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0)**(1 / 2.4) - .055)
    return rgb


def _scan(rgb, x, y, config):
    coord = x if config.get('scan_axis', 'y') == 'x' else y
    coverage = _box_coverage(coord, config.get('scan_spacing', 4), config.get('scan_width', .5), config.get('scan_phase', 0))
    return rgb * (1 - config.get('scan_strength', .5) * coverage[..., None])


def _crt_shading(rgb, x, y, width, height, config):
    import numpy as np
    rgb = _scan(rgb, x, y, config)
    mask = config.get('crt_mask', 'rgb')
    if mask != 'none':
        period = 3 * config.get('crt_pitch', 1)
        for channel in range(3):
            stripe = channel if mask == 'rgb' else 2 - channel
            coverage = _box_coverage(x, period, 1 / 3, -stripe / 3)
            rgb[..., channel] *= 1 - config.get('crt_mask_strength', .35) * (1 - coverage)
    u, v = 2 * (x + .5) / width - 1, 2 * (y + .5) / height - 1
    rgb *= (1 - config.get('crt_vignette', .3) * np.clip((u * u + v * v) / 2, 0, 1))[..., None]
    noise = config.get('crt_noise', 0)
    if noise:
        rgb += noise * (_hash01(x, y, config.get('raster_seed', 0), config.get('raster_phase', 0)) - .5)[..., None]
    return np.clip(rgb * config.get('crt_gain', 1), 0, 1)


def render_raster_with_alpha(image, config, *, original_alpha=None, tile_rows=TILE_ROWS):
    """Return independent RGBA and original-alpha metadata; opacity belongs to DAG.

    Pixel sorting permutes lineage with the exact colour-derived permutation.
    Cell alpha is averaged only when explicitly requested. Glitch and CRT
    geometry transport lineage through exactly the visible sampling footprint.
    """
    import numpy as np
    validate_raster(config, 1)
    if (not isinstance(image, Image.Image) or image.mode != 'RGBA' or min(image.size) < 1
            or max(image.size) > MAX_DIMENSION or image.width * image.height > MAX_PIXELS):
        raise ValueError('raster_effect needs nonempty RGBA within its pixel budget')
    if original_alpha is not None and (not isinstance(original_alpha, Image.Image)
                                      or original_alpha.mode != 'L' or original_alpha.size != image.size):
        raise ValueError('original alpha must be a same-sized L image')
    if type(tile_rows) is not int or not 1 <= tile_rows <= 256:
        raise ValueError('tile_rows must be 1..256')
    if raster_is_identity(config):
        return image.copy(), original_alpha.copy() if original_alpha is not None else None
    source = np.asarray(image, dtype=np.uint8)
    lineage = np.asarray(original_alpha, dtype=np.uint8) if original_alpha is not None else None
    rows = min(tile_rows, max(1, MAX_TILE_PIXELS // image.width))
    kind = config.get('raster_type', 'pixelate')
    if kind == 'pixelate':
        return _pixelate(source, lineage, config, rows)
    if kind == 'pixel_sort':
        return _pixel_sort(source, lineage, config)
    out = Image.new('RGBA', image.size)
    out_alpha = Image.new('L', image.size) if lineage is not None else None
    border, mix = config.get('raster_border', 'transparent'), config.get('raster_mix', 1)
    for top in range(0, image.height, rows):
        bottom = min(image.height, top + rows)
        y, x = np.indices((bottom - top, image.width), dtype=np.float64)
        y += top
        bp, ba = _premul(source[top:bottom])
        rgb = source[top:bottom, :, :3].astype(np.float64) / 255
        alpha = ba
        la = lineage[top:bottom].astype(np.float64) / 255 if lineage is not None else None
        if kind in {'crt', 'glitch'}:
            sx, sy = x, y
            selected = np.zeros(x.shape, dtype=bool)
            if kind == 'crt':
                amount = config.get('crt_curvature', .08)
                u, v = 2 * (x + .5) / image.width - 1, 2 * (y + .5) / image.height - 1
                sx = (u * (1 + amount * v * v) + 1) * image.width / 2 - .5
                sy = (v * (1 + amount * u * u) + 1) * image.height / 2 - .5
            else:
                band = np.floor(y / config.get('glitch_band_height', 16))
                seed, phase = config.get('raster_seed', 0), config.get('raster_phase', 0)
                selected = _hash01(band, 0, seed, phase) < config.get('glitch_density', .3)
                dx, dy = config.get('glitch_offset', [32, 0])
                sx = x - selected * dx * (2 * _hash01(band, 0, seed, phase, 1) - 1)
                sy = y - selected * dy * (2 * _hash01(band, 0, seed, phase, 2) - 1)
            p, alpha = _sample_border(source, sx, sy, border)
            if lineage is not None:
                la = _sample_alpha(lineage, sx, sy, border)
            split = config.get('glitch_chromatic', 0) if kind == 'glitch' else 0
            if split:
                red, ra = _sample_border(source, sx - split * selected, sy, border)
                blue, blua = _sample_border(source, sx + split * selected, sy, border)
                p[..., 0], p[..., 2] = red[..., 0], blue[..., 2]
                alpha = np.maximum(np.maximum(alpha, ra), blua)
                if lineage is not None:
                    la = np.maximum(np.maximum(la, _sample_alpha(lineage, sx - split * selected, sy, border)),
                                    _sample_alpha(lineage, sx + split * selected, sy, border))
            rgb = np.divide(p, alpha[..., None], out=np.zeros_like(p), where=alpha[..., None] > 0)
        if kind == 'dither':
            rgb = _dither(rgb, x, y, config)
        elif kind == 'scanlines':
            rgb = _scan(rgb, x, y, config)
        elif kind == 'crt':
            rgb = _crt_shading(rgb, x, y, image.width, image.height, config)
        p = rgb * alpha[..., None]
        encoded = _encode(mix * p + (1 - mix) * bp, mix * alpha + (1 - mix) * ba)
        out.paste(Image.fromarray(encoded), (0, top))
        if lineage is not None:
            original = lineage[top:bottom].astype(np.float64) / 255
            out_alpha.paste(Image.fromarray(np.uint8(np.rint(np.clip(mix * la + (1 - mix) * original, 0, 1) * 255))), (0, top))
    return out, out_alpha


def render_raster(image, config, *, tile_rows=TILE_ROWS):
    """Public single-surface reference implementation."""
    return render_raster_with_alpha(image, config, tile_rows=tile_rows)[0]
