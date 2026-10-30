"""M5.2 deterministic, bounded two-source colour-band transitions.

Selected colours can reveal at different times, displace independently in
both sources, recolour incoming pixels, and create stateless spatial echoes.
This CPU reference deliberately shares M4's RGB/HSV/OKLab distance functions.
There is no unrecorded frame history, source clock, shader or global state.
"""
from __future__ import annotations

import math

from PIL import Image

from .effect_graph_spectral import (
    MAX_OFFSET, MAX_SPECTRAL_BANDS, SPECTRAL_ALPHA_MODES, SPECTRAL_SPACES,
    _coverage, _numpy, _rgb,
)

MAX_ECHOES = 4
TILE_ROWS = 64
MASK_SOURCES = frozenset({'from', 'to', 'union', 'intersection'})
COLOUR_FIELDS = frozenset({'source_color', 'to_color', 'target_color'})
SCALAR_FIELDS = frozenset({'start', 'end', 'tolerance', 'softness', 'opacity', 'recolor', 'echo_decay'})
VECTOR_FIELDS = frozenset({'offset_from', 'offset_to'})
BAND_FIELDS = frozenset({'id', 'space', 'mask_source', 'echoes', *COLOUR_FIELDS, *SCALAR_FIELDS, *VECTOR_FIELDS})
STYLE_FIELDS = frozenset({'bands', 'mix', 'alpha_mode'})


def _finite(value, name, lo, hi):
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f'spectral transition {name} must be finite in [{lo}, {hi}]')


def validate_transition_spectral(config):
    """Reject unsupported structural combinations before image processing."""
    if config.get('transition_style', 'dissolve') != 'spectral':
        extra = STYLE_FIELDS.intersection(config)
        if extra:
            raise ValueError(f'spectral-only transition settings require spectral style: {sorted(extra)}')
        return
    bands = config.get('bands')
    if not isinstance(bands, list) or not 1 <= len(bands) <= MAX_SPECTRAL_BANDS:
        raise ValueError('spectral transition requires 1..8 bands')
    _finite(config.get('mix', 1), 'mix', 0, 1)
    if (not isinstance(config.get('alpha_mode', 'expanded'), str) or
            config.get('alpha_mode', 'expanded') not in SPECTRAL_ALPHA_MODES):
        raise ValueError('invalid spectral transition alpha_mode')
    seen = set()
    for index, band in enumerate(bands):
        if not isinstance(band, dict) or set(band) - BAND_FIELDS:
            raise ValueError(f'invalid spectral transition band {index}')
        name = band.get('id')
        if (not isinstance(name, str) or not 1 <= len(name) <= 64 or
                not name[0].isalpha() or not all(c.isalnum() or c in '_-' for c in name) or
                name in seen):
            raise ValueError('spectral transition band ids must be unique and alphanumeric')
        seen.add(name)
        if 'source_color' not in band:
            raise ValueError('spectral transition band needs source_color')
        for field in COLOUR_FIELDS & band.keys():
            _rgb(band[field], field)
        if (not isinstance(band.get('space', 'oklab'), str) or
                band.get('space', 'oklab') not in SPECTRAL_SPACES):
            raise ValueError('invalid spectral transition selection space')
        if (not isinstance(band.get('mask_source', 'union'), str) or
                band.get('mask_source', 'union') not in MASK_SOURCES):
            raise ValueError('invalid spectral transition mask_source')
        for field in SCALAR_FIELDS:
            _finite(band.get(field, {'start': 0, 'end': 1, 'tolerance': .08,
                                    'softness': .08, 'opacity': 1, 'recolor': 0,
                                    'echo_decay': 0}[field]), field, 0, 1)
        if band.get('start', 0) >= band.get('end', 1):
            raise ValueError('spectral transition band end must exceed start')
        for field in VECTOR_FIELDS:
            vec = band.get(field, [0, 0])
            if not isinstance(vec, (list, tuple)) or len(vec) != 2:
                raise ValueError(f'invalid spectral transition {field} vector')
            for component in vec:
                _finite(component, field, -MAX_OFFSET, MAX_OFFSET)
        echoes = band.get('echoes', 0)
        if type(echoes) is not int or not 0 <= echoes <= MAX_ECHOES:
            raise ValueError('spectral transition echoes must be integer 0..4')


def control_parts(config, path):
    """Resolve band IDs, not list positions, so reordering never breaks controls."""
    if not isinstance(path, str):
        return None
    parts = path.split('.')
    if len(parts) not in (3, 4) or parts[0] != 'bands':
        return None
    index = next((i for i, b in enumerate(config.get('bands', []))
                  if b['id'] == parts[1]), None)
    if index is None:
        return None
    field = parts[2]
    component = parts[3] if len(parts) == 4 else None
    if field in VECTOR_FIELDS and component in {'x', 'y'}:
        return index, field, component
    if field in COLOUR_FIELDS and component in {'r', 'g', 'b'}:
        return index, field, component
    if component is None and (field in SCALAR_FIELDS or field in {'space', 'mask_source', 'echoes'}):
        return index, field, None
    return None


def control_base(config, path):
    part = control_parts(config, path)
    if part is None:
        raise ValueError('unknown spectral transition control target')
    index, field, component = part
    band = config['bands'][index]
    if field in VECTOR_FIELDS:
        return band.get(field, [0, 0])[0 if component == 'x' else 1]
    if field in COLOUR_FIELDS:
        default = band['source_color'] if field != 'target_color' else band.get('to_color', band['source_color'])
        return _rgb(band.get(field, default), field)[{'r': 0, 'g': 1, 'b': 2}[component]]
    return band.get(field, {'start': 0, 'end': 1, 'tolerance': .08, 'softness': .08,
                            'opacity': 1, 'recolor': 0, 'echo_decay': 0, 'echoes': 0,
                            'space': 'oklab', 'mask_source': 'union'}[field])


def control_choices(config, path):
    part = control_parts(config, path)
    if part is None:
        return None
    field = part[1]
    if field == 'space':
        return SPECTRAL_SPACES
    if field == 'mask_source':
        return MASK_SOURCES
    return None


def control_apply(config, path, value):
    part = control_parts(config, path)
    if part is None:
        raise ValueError('unknown spectral transition control target')
    index, field, component = part
    updated = dict(config)
    updated['bands'] = list(config['bands'])
    band = dict(updated['bands'][index])
    updated['bands'][index] = band
    accepted = control_choices(config, path)
    if accepted is not None:
        if not isinstance(value, str) or value not in accepted:
            raise ValueError('invalid categorical spectral transition control')
        band[field] = value
    elif field in VECTOR_FIELDS:
        _finite(value, path, -MAX_OFFSET, MAX_OFFSET)
        vec = list(band.get(field, [0, 0]))
        vec[0 if component == 'x' else 1] = value
        band[field] = vec
    elif field in COLOUR_FIELDS:
        _finite(value, path, 0, 255)
        default = band['source_color'] if field != 'target_color' else band.get('to_color', band['source_color'])
        rgb = list(_rgb(band.get(field, default), field))
        rgb[{'r': 0, 'g': 1, 'b': 2}[component]] = round(value)
        band[field] = '#%02x%02x%02x' % tuple(rgb)
    elif field == 'echoes':
        _finite(value, path, 0, MAX_ECHOES)
        band[field] = round(value)
    else:
        _finite(value, path, 0, 1)
        band[field] = value
    return updated


def _shift(source, top, bottom, width, dx, dy):
    """Bilinear sample into premultiplied floating RGBA with transparent borders."""
    np = _numpy()
    height = source.shape[0]
    rows = bottom - top
    sx = np.arange(width, dtype=np.float32)[None, :] - np.float32(dx)
    sy = np.arange(top, bottom, dtype=np.float32)[:, None] - np.float32(dy)
    x0, y0 = np.floor(sx).astype(np.int32), np.floor(sy).astype(np.int32)
    tx, ty = sx - x0, sy - y0
    alpha = np.zeros((rows, width), dtype=np.float32)
    rgb = np.zeros((rows, width, 3), dtype=np.float32)
    for ox, oy, weight in ((0, 0, (1-tx)*(1-ty)), (1, 0, tx*(1-ty)),
                           (0, 1, (1-tx)*ty), (1, 1, tx*ty)):
        xi, yi = np.broadcast_to(x0 + ox, (rows, width)), np.broadcast_to(y0 + oy, (rows, width))
        valid = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
        texel = source[np.clip(yi, 0, height-1), np.clip(xi, 0, width-1)].astype(np.float32) / 255
        a = texel[..., 3] * weight * valid
        alpha += a
        rgb += texel[..., :3] * a[..., None]
    return rgb, alpha


def _mask(a, b, band, incoming_color):
    """Return alpha-weighted selection; transparent hidden RGB is ignored."""
    np = _numpy()
    pick_a = _coverage(np.divide(a[0], a[1][..., None],
                                 out=np.zeros_like(a[0]), where=a[1][..., None] > 1e-7), band) * a[1]
    other_band = {**band, 'source_color': incoming_color}
    pick_b = _coverage(np.divide(b[0], b[1][..., None],
                                 out=np.zeros_like(b[0]), where=b[1][..., None] > 1e-7), other_band) * b[1]
    mode = band.get('mask_source', 'union')
    if mode == 'from':
        return pick_a
    if mode == 'to':
        return pick_b
    if mode == 'intersection':
        return np.minimum(pick_a, pick_b)
    return np.maximum(pick_a, pick_b)


def spectral_transition(from_image, to_image, config, *, tile_rows=TILE_ROWS):
    """Transition two layers by spatially selected, independently timed bands.

    At progress 0/1 the outputs are exact source copies. Between endpoints,
    nonselected pixels dissolve normally while each band has its own reveal
    window, displacement and optional stateless chromatic spatial echoes.
    """
    np = _numpy()
    validate_transition_spectral(config)
    if (not isinstance(from_image, Image.Image) or not isinstance(to_image, Image.Image)
            or from_image.mode != 'RGBA' or to_image.mode != 'RGBA'
            or from_image.size != to_image.size):
        raise ValueError('spectral transition requires equal-size RGBA sources')
    if type(tile_rows) is not int or not 1 <= tile_rows <= 256:
        raise ValueError('spectral transition tile_rows must be 1..256')
    progress = config.get('progress', 0)
    _finite(progress, 'progress', 0, 1)
    if progress == 0:
        return from_image.copy()
    if progress == 1:
        return to_image.copy()
    height, width = from_image.height, from_image.width
    aa = np.asarray(from_image, dtype=np.uint8)
    bb = np.asarray(to_image, dtype=np.uint8)
    result = Image.new('RGBA', from_image.size)
    mix = config.get('mix', 1)
    easing = config.get('feather', 0)
    for top in range(0, height, tile_rows):
        bottom = min(height, top + tile_rows)
        a = aa[top:bottom].astype(np.float32) / 255
        b = bb[top:bottom].astype(np.float32) / 255
        alpha_a, alpha_b = a[..., 3], b[..., 3]
        current_alpha = alpha_a * (1-progress) + alpha_b * progress
        current_rgb = (a[..., :3] * alpha_a[..., None] * (1-progress) +
                       b[..., :3] * alpha_b[..., None] * progress)
        for band in config['bands']:
            local = float(np.clip((progress - band.get('start', 0)) /
                                  (band.get('end', 1) - band.get('start', 0)), 0, 1))
            eased = local * local * (3 - 2 * local)
            local = local * (1-easing) + eased * easing
            from_shift = band.get('offset_from', [0, 0])
            to_shift = band.get('offset_to', [0, 0])
            moved_a = _shift(aa, top, bottom, width, from_shift[0] * local, from_shift[1] * local)
            moved_b = _shift(bb, top, bottom, width, to_shift[0] * (1-local), to_shift[1] * (1-local))
            original_a = (a[..., :3] * alpha_a[..., None], alpha_a)
            original_b = (b[..., :3] * alpha_b[..., None], alpha_b)
            incoming_color = band.get('to_color', band['source_color'])
            mask = _mask(original_a, original_b, band, incoming_color)
            mask = np.maximum(mask, _mask(moved_a, moved_b, band, incoming_color))
            strength = np.clip(mask * band.get('opacity', 1) * mix, 0, 1)
            premul_b = moved_b[0]
            recolor = band.get('recolor', 0)
            if recolor:
                target = np.asarray(_rgb(band.get('target_color', incoming_color), 'target_color'),
                                    dtype=np.float32) / 255
                premul_b = premul_b * (1-recolor) + target * moved_b[1][..., None] * recolor
            branch_rgb = moved_a[0] * (1-local) + premul_b * local
            branch_alpha = moved_a[1] * (1-local) + moved_b[1] * local
            # Echoes sample displaced subpositions; these are spatial trails,
            # not accesses to previous video frames.
            for index in range(1, band.get('echoes', 0) + 1):
                frac = 1 + index / (band['echoes'] + 1)
                ea = _shift(aa, top, bottom, width,
                            from_shift[0] * local * frac, from_shift[1] * local * frac)
                eb = _shift(bb, top, bottom, width,
                            to_shift[0] * (1-local) * frac, to_shift[1] * (1-local) * frac)
                ep = ea[0] * (1-local) + eb[0] * local
                el = ea[1] * (1-local) + eb[1] * local
                weight = band.get('echo_decay', 0) ** index / (band['echoes'] + 1)
                branch_rgb = branch_rgb * (1-weight) + ep * weight
                branch_alpha = branch_alpha * (1-weight) + el * weight
            current_rgb = current_rgb * (1-strength[..., None]) + branch_rgb * strength[..., None]
            current_alpha = current_alpha * (1-strength) + branch_alpha * strength
        if config.get('alpha_mode', 'expanded') == 'source':
            maximum = np.maximum(alpha_a, alpha_b)
            scale = np.divide(np.minimum(current_alpha, maximum), current_alpha,
                              out=np.zeros_like(current_alpha), where=current_alpha > 1e-7)
            current_rgb *= scale[..., None]
            current_alpha = np.minimum(current_alpha, maximum)
        rgb = np.divide(current_rgb, current_alpha[..., None],
                        out=np.zeros_like(current_rgb), where=current_alpha[..., None] > 1e-7)
        output = np.uint8(np.rint(np.clip(np.concatenate((rgb, current_alpha[..., None]), axis=-1),
                                         0, 1) * 255))
        result.paste(Image.fromarray(output, 'RGBA'), (0, top))
    return result
