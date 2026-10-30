"""M5.3 deterministic geometric transitions with bounded CPU sampling.

Two incoming straight-RGBA images are sampled as premultiplied colours into
small output-row tiles. The implementation requires no history or extra clock;
all controllable parameters are resolved by the existing effect graph.
"""
from __future__ import annotations

import math

from PIL import Image

GEOMETRIC_STYLES = frozenset({'zoom', 'rotate', 'polar', 'kaleidoscope', 'liquid'})
GEOMETRY_DEFAULTS = {
    'strength': 0.75,
    'zoom': 1.0,
    'rotation': 180.0,         # degrees
    'twist': 0.75,             # turns across radius
    'segments': 6,            # reflected kaleidoscope sectors
    'frequency': 4.0,         # oscillations across the image
    'amplitude': 24.0,        # displacement in physical pixels
    'phase': 0.0,             # radians; DanceRudiment-modulatable
}
GEOMETRY_RANGES = {
    'strength': (0.0, 1.0), 'zoom': (0.0, 4.0),
    'rotation': (-720.0, 720.0), 'twist': (-4.0, 4.0),
    'segments': (2, 24), 'frequency': (0.0, 32.0),
    'amplitude': (0.0, 256.0), 'phase': (-8 * math.pi, 8 * math.pi),
}
TILE_ROWS = 48


def _number(value, name, low, high, *, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not low <= value <= high or (integer and type(value) is not int)):
        raise ValueError(f'geometric transition {name} must be finite in [{low}, {high}]')


def validate_transition_geometry(config):
    """Validate before rendering; geometric-only parameters never leak to other styles."""
    style = config.get('transition_style', 'dissolve')
    params = GEOMETRY_RANGES.keys() & config.keys()
    if style not in GEOMETRIC_STYLES:
        if params:
            raise ValueError('geometric transition parameters require geometric style')
        return
    for name, (minimum, maximum) in GEOMETRY_RANGES.items():
        _number(config.get(name, GEOMETRY_DEFAULTS[name]), name, minimum, maximum,
                integer=name == 'segments')


def _sample(source, sx, sy):
    """Return interpolated premultiplied RGB and alpha, with transparent borders."""
    import numpy as np

    rows, width = sx.shape
    height, source_width = source.shape[:2]
    x0, y0 = np.floor(sx).astype(np.int32), np.floor(sy).astype(np.int32)
    tx, ty = sx - x0, sy - y0
    alpha = np.zeros((rows, width), np.float32)
    colour = np.zeros((rows, width, 3), np.float32)
    for ox, oy, weight in (
        (0, 0, (1 - tx) * (1 - ty)),
        (1, 0, tx * (1 - ty)),
        (0, 1, (1 - tx) * ty),
        (1, 1, tx * ty),
    ):
        xi, yi = x0 + ox, y0 + oy
        valid = (xi >= 0) & (xi < source_width) & (yi >= 0) & (yi < height)
        sample = source[np.clip(yi, 0, height - 1), np.clip(xi, 0, source_width - 1)].astype(np.float32) / 255
        contribution = sample[..., 3] * weight * valid
        alpha += contribution
        colour += sample[..., :3] * contribution[..., None]
    return colour, alpha


def _weight(coordinate, progress, feather, size):
    import numpy as np

    # At least one output pixel of smoothing for antialiasing, even if the
    # author selects a hard edge. Existing non-geometric wipes are unchanged.
    transition_width = max(feather, 1.0 / max(size))
    t = np.clip((progress - coordinate) / transition_width + 0.5, 0, 1)
    return t * t * (3 - 2 * t)


def _rotate(dx, dy, angle):
    import numpy as np

    cs, sn = np.cos(angle), np.sin(angle)
    return dx * cs - dy * sn, dx * sn + dy * cs


def geometric_transition(from_image: Image.Image, to_image: Image.Image, config: dict,
                         *, tile_rows: int = TILE_ROWS) -> Image.Image:
    """Morph and reveal two images with exact endpoints and stateless progress.

    Scene colour is currently display-referred, matching existing CPU graph
    transitions. Intermediate images use straight RGBA at the API boundary.
    """
    import numpy as np

    validate_transition_geometry(config)
    if config.get('transition_style') not in GEOMETRIC_STYLES:
        raise ValueError('geometric transition requires geometric style')
    if (not isinstance(from_image, Image.Image) or not isinstance(to_image, Image.Image)
            or from_image.mode != 'RGBA' or to_image.mode != 'RGBA'
            or from_image.size != to_image.size):
        raise ValueError('geometric transition requires equal-sized RGBA sources')
    if type(tile_rows) is not int or not 1 <= tile_rows <= 256:
        raise ValueError('geometric transition tile_rows must be 1..256')
    progress = config.get('progress', 0)
    _number(progress, 'progress', 0, 1)
    if progress == 0:
        return from_image.copy()
    if progress == 1:
        return to_image.copy()

    height, width = from_image.height, from_image.width
    style = config['transition_style']
    centre_x, centre_y = config.get('center', [0.5, 0.5])
    cx, cy = width * centre_x - 0.5, height * centre_y - 0.5
    corners = [(x - cx, y - cy) for x in (-0.5, width - 0.5)
               for y in (-0.5, height - 0.5)]
    max_radius = max(math.hypot(x, y) for x, y in corners) or 1.0
    feather = config.get('feather', 0)
    strength = config.get('strength', GEOMETRY_DEFAULTS['strength'])
    zoom = config.get('zoom', GEOMETRY_DEFAULTS['zoom'])
    rotation = math.radians(config.get('rotation', GEOMETRY_DEFAULTS['rotation']))
    twist = config.get('twist', GEOMETRY_DEFAULTS['twist']) * (2 * math.pi)
    count = config.get('segments', GEOMETRY_DEFAULTS['segments'])
    frequency = config.get('frequency', GEOMETRY_DEFAULTS['frequency'])
    amplitude = config.get('amplitude', GEOMETRY_DEFAULTS['amplitude']) * strength
    phase = config.get('phase', GEOMETRY_DEFAULTS['phase'])
    direction = config.get('direction', 'right')
    # Keep whole-frame inputs as uint8; only convert the current sample
    # tile to float32, avoiding multi-gigabyte 8K working copies.
    a = np.asarray(from_image, dtype=np.uint8)
    b = np.asarray(to_image, dtype=np.uint8)
    output = Image.new('RGBA', (width, height))
    xx = np.arange(width, dtype=np.float32)[None, :]
    for top in range(0, height, tile_rows):
        end = min(height, top + tile_rows)
        yy = np.arange(top, end, dtype=np.float32)[:, None]
        x = np.broadcast_to(xx, (end - top, width))
        y = np.broadcast_to(yy, (end - top, width))
        dx, dy = x - cx, y - cy
        radial = np.hypot(dx, dy) / max_radius
        angle = np.arctan2(dy, dx)
        norm_x, norm_y = (x + .5) / width, (y + .5) / height
        wave = np.sin(norm_y * (2 * math.pi * frequency) + phase)

        if style == 'zoom':
            coordinate = radial
            scale_a = 1 + strength * zoom * progress
            scale_b = 1 + strength * zoom * (1 - progress)
            sx_a, sy_a = cx + dx / scale_a, cy + dy / scale_a
            sx_b, sy_b = cx + dx / scale_b, cy + dy / scale_b
        elif style == 'rotate':
            coordinate = ((angle + math.pi + rotation * progress) % (2 * math.pi)) / (2 * math.pi)
            ax, ay = _rotate(dx, dy, -rotation * strength * progress)
            bx, by = _rotate(dx, dy, rotation * strength * (1 - progress))
            sx_a, sy_a = cx + ax, cy + ay
            sx_b, sy_b = cx + bx, cy + by
        elif style == 'polar':
            coordinate = radial
            ax, ay = _rotate(dx, dy, twist * strength * radial * progress)
            bx, by = _rotate(dx, dy, -twist * strength * radial * (1 - progress))
            sx_a, sy_a = cx + ax, cy + ay
            sx_b, sy_b = cx + bx, cy + by
        elif style == 'kaleidoscope':
            # Fold repeated angular sectors into mirrored wedges.
            sector = 2 * math.pi / count
            unfolded = (angle + math.pi + rotation * progress) % sector
            coordinate = unfolded / sector
            fold = np.where(unfolded <= sector / 2, unfolded, sector - unfolded)
            sector_angle = fold - math.pi / count
            image_angle = sector_angle + twist * strength * radial
            px, py = np.cos(image_angle) * radial * max_radius, np.sin(image_angle) * radial * max_radius
            sx_a = cx + (dx * (1 - strength * progress) + px * strength * progress)
            sy_a = cy + (dy * (1 - strength * progress) + py * strength * progress)
            sx_b = cx + (dx * (1 - strength * (1 - progress)) + px * strength * (1 - progress))
            sy_b = cy + (dy * (1 - strength * (1 - progress)) + py * strength * (1 - progress))
        else:  # liquid
            drift = amplitude * wave
            drift_y = amplitude * 0.35 * np.cos(norm_x * (2 * math.pi * frequency) + phase)
            sx_a, sy_a = x - drift * progress, y - drift_y * progress
            sx_b, sy_b = x + drift * (1 - progress), y + drift_y * (1 - progress)
            if direction in {'down', 'up'}:
                coordinate = norm_y + drift_y / max(height, 1)
            else:
                coordinate = norm_x + drift / max(width, 1)
        if direction in {'left', 'up'}:
            coordinate = 1 - coordinate
        weight = _weight(coordinate, progress, feather, (height, width))
        a_colour, a_alpha = _sample(a, sx_a, sy_a)
        b_colour, b_alpha = _sample(b, sx_b, sy_b)
        alpha = a_alpha * (1 - weight) + b_alpha * weight
        premul = a_colour * (1 - weight)[..., None] + b_colour * weight[..., None]
        colour = np.divide(premul, alpha[..., None], out=np.zeros_like(premul),
                           where=alpha[..., None] > 1e-7)
        data = np.concatenate((colour, alpha[..., None]), axis=-1)
        tile = Image.fromarray(np.uint8(np.rint(np.clip(data, 0, 1) * 255)), 'RGBA')
        output.paste(tile, (0, top))
    return output
