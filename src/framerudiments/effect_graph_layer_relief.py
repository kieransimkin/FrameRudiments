"""M6.3 bounded CPU alpha relief, satin and swept-silhouette effects.

No renderer, motion or GPU dependency. Geometry comes only from selected alpha.
Lighting uses image-space degrees: zero points right, 90 points down. These are
stylised 2.5-D effects, not mesh extrusion or a physically based material model.
"""
from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageColor

from .effect_graph_alpha_geometry import (
    MAX_DISTANCE_PIXELS, blur_alpha, padded_alpha, place_mask, signed_distance,
)

RELIEF_TYPES = frozenset({'bevel', 'emboss', 'edge_light', 'satin', 'extrusion', 'long_shadow'})
LIGHT_TYPES = frozenset({'bevel', 'emboss', 'edge_light'})
SWEEP_TYPES = frozenset({'extrusion', 'long_shadow'})
RELIEF_ENUMS = {
    'bevel_profile': frozenset({'linear', 'round'}),
    'bevel_direction': frozenset({'up', 'down'}),
    'satin_mode': frozenset({'normal', 'inverted'}),
}
RELIEF_COLOURS = frozenset({'highlight_color', 'shadow_color', 'extrude_color'})
RELIEF_RANGES = {
    'bevel_depth': (0, 64), 'bevel_softness': (0, 16),
    'relief_threshold': (1 / 255, 1),
    'light_angle': (-3600, 3600), 'light_elevation': (0, 90),
    'highlight_strength': (0, 1), 'shadow_strength': (0, 1),
    'extrude_length': (0, 256), 'extrude_angle': (-3600, 3600),
    'extrude_fade': (0, 1),
    **{f'{name}.{component}': (0, 255) for name in RELIEF_COLOURS for component in 'rgb'},
}
RELIEF_DEFAULTS = {
    'bevel_profile': 'round', 'bevel_direction': 'up', 'satin_mode': 'normal',
    'bevel_depth': 4, 'bevel_softness': 1, 'relief_threshold': 0.5,
    'light_angle': 225, 'light_elevation': 45,
    'highlight_strength': 0.8, 'shadow_strength': 0.8,
    'highlight_color': '#ffffff', 'shadow_color': '#000000',
    'extrude_color': '#000000', 'extrude_length': 16, 'extrude_angle': 45, 'extrude_fade': 0,
}
# Crop-sized masks/fields and finite sweep visits prevent runaway per-frame work.
MAX_SWEEP_PIXELS = 4_194_304
MAX_SWEEP_VISITS = 134_217_728
MAX_SWEEP_SAMPLES = 257


def _rgb(config, key):
    return ImageColor.getrgb(config.get(key, RELIEF_DEFAULTS.get(key, '#000000')))


def _gaussian_field(field, sigma):
    """Separable, zero-border Gaussian for floating-point heights (no SciPy)."""
    import numpy as np

    if sigma == 0:
        return field.copy()
    radius = max(1, math.ceil(3 * sigma))
    positions = np.arange(-radius, radius + 1, dtype=np.float32)
    weights = np.exp(-0.5 * (positions / sigma) ** 2)
    weights /= weights.sum()
    result = np.asarray(field, dtype=np.float32)
    for axis in (1, 0):
        padding = [(0, 0), (0, 0)]
        padding[axis] = (radius, radius)
        extended = np.pad(result, padding)
        next_pass = np.zeros_like(result)
        for i, weight in enumerate(weights):
            slices = [slice(None), slice(None)]
            slices[axis] = slice(i, i + result.shape[axis])
            next_pass += extended[tuple(slices)] * weight
        result = next_pass
    return result


def _as_mask(values):
    import numpy as np

    return Image.fromarray(np.uint8(np.rint(np.clip(values, 0, 1) * 255)))


def lighting_masks(alpha, config):
    """Return crop-local highlight/shadow/outer-rim masks and canvas-space bounds.

    The distance field's threshold is relative to the selected alpha maximum:
    uniformly fading a silhouette does not erase its geometry. Actual soft alpha
    still gates emitted coverage. An emboss is an interior border ridge; a bevel
    is a ramp to an interior plateau. Flat plateaus receive no added lighting.
    """
    import numpy as np

    width = config.get('style_width', 3)
    softness = config.get('bevel_softness', 1)
    halo = math.ceil(width + 3 * softness) + 3
    # Include the EDT's own one-pixel padding in its allocation budget.
    mask, bounds = padded_alpha(alpha, halo, max_pixels=MAX_DISTANCE_PIXELS)
    if mask is None:
        return None, None, None, None
    coverage = np.asarray(mask, dtype=np.float32) / 255
    maximum = float(coverage.max())
    normalized = _as_mask(coverage / maximum)
    distance = signed_distance(normalized, threshold=config.get('relief_threshold', 0.5),
                               limit=max(2, width + 2))
    kind = config.get('style_type')
    angle = math.radians(config.get('light_angle', 225) % 360)
    altitude = math.radians(config.get('light_elevation', 45))
    light_x, light_y = math.cos(angle) * math.cos(altitude), math.sin(angle) * math.cos(altitude)
    outer_highlight = None
    if kind == 'edge_light':
        # Positive SDF increases outward. Thus its gradient is the outward normal.
        gy, gx = np.gradient(_gaussian_field(distance, softness))
        norm = np.sqrt(gx * gx + gy * gy)
        facing = np.divide(gx * light_x + gy * light_y, norm,
                           out=np.zeros_like(norm), where=norm > 1e-7)
        side = config.get('outline_position', 'outer')
        outer_width = width if side == 'outer' else width / 2 if side == 'center' else 0
        inner_width = width if side == 'inner' else width / 2 if side == 'center' else 0
        outside = np.clip(outer_width + 0.5 - distance, 0, 1) * (1 - coverage) * maximum
        inside = np.clip(inner_width + 0.5 + distance, 0, 1) * coverage
        if not outer_width:
            outside[:] = 0
        if not inner_width:
            inside[:] = 0
        highlight = inside * np.maximum(facing, 0)
        outer_highlight = outside * np.maximum(facing, 0)
        shadow = np.zeros_like(highlight)
    else:
        ramp = np.clip(-distance / width, 0, 1)
        profile = config.get('bevel_profile', 'round')
        if kind == 'emboss':
            height = (np.sin(np.pi * ramp) if profile == 'round'
                      else 1 - np.abs(2 * ramp - 1))
        else:
            height = ramp * ramp * (3 - 2 * ramp) if profile == 'round' else ramp
        height *= config.get('bevel_depth', 4)
        if config.get('bevel_direction', 'up') == 'down':
            height = -height
        height = _gaussian_field(height, softness)
        gy, gx = np.gradient(height)
        norm = np.sqrt(gx * gx + gy * gy + 1)
        # Compare to a flat surface. No brightness change on zero-slope plateaus.
        change = (-gx * light_x - gy * light_y + math.sin(altitude)) / norm - math.sin(altitude)
        highlight = coverage * np.clip(change, 0, 1)
        shadow = coverage * np.clip(-change, 0, 1)
    strength = config.get('style_strength', 1)
    highlight *= strength * config.get('highlight_strength', 0.8)
    shadow *= strength * config.get('shadow_strength', 0.8)
    if outer_highlight is not None:
        outer_highlight = _as_mask(outer_highlight * strength * config.get('highlight_strength', 0.8))
    return _as_mask(highlight), _as_mask(shadow), outer_highlight, bounds


def _paint_pair(image, reference, highlight, shadow, config, *, effect_only):
    """Conditional-coverage shading; full fill preserves source alpha exactly."""
    import numpy as np

    result = Image.new('RGBA', image.size)
    fill = config.get('fill_opacity', 1)
    hi_rgb = np.asarray(_rgb(config, 'highlight_color'), dtype=np.float32) / 255
    lo_rgb = np.asarray(_rgb(config, 'shadow_color'), dtype=np.float32) / 255
    for y in range(0, image.height, 128):
        box = (0, y, image.width, min(image.height, y + 128))
        src = np.asarray(image.crop(box), dtype=np.float32) / 255
        ref = np.asarray(reference.crop(box), dtype=np.float32) / 255
        hi = np.asarray(highlight.crop(box), dtype=np.float32) / 255
        lo = np.asarray(shadow.crop(box), dtype=np.float32) / 255
        if effect_only:
            alpha = np.clip(hi + lo, 0, 1)[..., None]
            premul = hi[..., None] * hi_rgb + lo[..., None] * lo_rgb
        else:
            hi = np.divide(hi, ref, out=np.zeros_like(hi), where=ref > 0)
            lo = np.divide(lo, ref, out=np.zeros_like(lo), where=ref > 0)
            total = np.maximum(1, hi + lo)
            hi, lo = hi / total, lo / total
            base = np.clip(1 - hi - lo, 0, 1) * fill
            weight = (hi + lo + base)[..., None]
            alpha = src[..., 3:4] * weight
            premul = src[..., 3:4] * (src[..., :3] * base[..., None]
                                     + hi[..., None] * hi_rgb + lo[..., None] * lo_rgb)
        rgb = np.divide(premul, alpha, out=np.zeros_like(premul), where=alpha > 0)
        out = np.concatenate([rgb, alpha], axis=-1)
        quantized = np.uint8(np.rint(np.clip(out, 0, 1) * 255))
        quantized[quantized[..., 3] == 0, :3] = 0
        result.paste(Image.fromarray(quantized), box[:2])
    return result


def render_lighting(image, reference, config):
    """Render alpha relief; outer rim light is explicitly composited behind fill."""
    hi_crop, lo_crop, outer_crop, bounds = lighting_masks(reference, config)
    if bounds is None:
        return Image.new('RGBA', image.size)
    hi = place_mask(hi_crop, image.size, *bounds[:2])
    lo = place_mask(lo_crop, image.size, *bounds[:2])
    outer = (place_mask(outer_crop, image.size, *bounds[:2])
             if outer_crop is not None else None)
    effect_only = config.get('style_output', 'combined') == 'effect'
    if effect_only:
        all_highlights = ImageChops.add(hi, outer) if outer is not None else hi
        return _paint_pair(image, reference, all_highlights, lo, config, effect_only=True)
    filled = _paint_pair(image, reference, hi, lo, config, effect_only=False)
    if outer is None or outer.getbbox() is None:
        return filled
    rim = _paint_pair(image, reference, outer, Image.new('L', image.size), config, effect_only=True)
    return Image.alpha_composite(rim, filled)


def satin_mask(alpha, config):
    """Symmetric displaced-alpha interference clipped to the original silhouette."""
    import numpy as np

    dx, dy = config.get('style_offset', [4, 4])
    blur = config.get('style_blur', 0)
    mask, bounds = padded_alpha(alpha, math.ceil(4 * blur) + 2)
    if mask is None:
        return Image.new('L', alpha.size)
    smooth = blur_alpha(mask, blur)
    first = place_mask(smooth, alpha.size, bounds[0] + dx, bounds[1] + dy)
    second = place_mask(smooth, alpha.size, bounds[0] - dx, bounds[1] - dy)
    # Relative interference, not a second multiplication by translucent alpha.
    difference = np.asarray(ImageChops.difference(first, second), dtype=np.float32)
    maximum = max(alpha.getextrema()[1], 1)
    wave = np.clip(difference / maximum, 0, 1)
    if config.get('satin_mode', 'normal') == 'inverted':
        wave = 1 - wave
    return _as_mask(wave * np.asarray(alpha, dtype=np.float32) / 255)


def sweep_surface(alpha, config):
    """Union of subpixel silhouette samples, never repeated alpha-over stamping.

    Nearest coverage wins. Newly exposed coverage can interpolate towards the
    far colour. Work is local to the visible sweep and is explicitly bounded.
    """
    import numpy as np

    result = Image.new('RGBA', alpha.size)
    box = alpha.getbbox()
    length = config.get('extrude_length', 16)
    if box is None or length == 0:
        return result
    angle = math.radians(config.get('extrude_angle', 45) % 360)
    dx, dy = length * math.cos(angle), length * math.sin(angle)
    # Remove trigonometric residuals at cardinal directions (byte-exact pixels).
    dx = 0.0 if abs(dx) < 1e-10 else dx
    dy = 0.0 if abs(dy) < 1e-10 else dy
    left = max(0, math.floor(box[0] + min(0, dx)) - 1)
    top = max(0, math.floor(box[1] + min(0, dy)) - 1)
    right = min(alpha.width, math.ceil(box[2] + max(0, dx)) + 1)
    bottom = min(alpha.height, math.ceil(box[3] + max(0, dy)) + 1)
    size = right - left, bottom - top
    samples = math.ceil(length) + 1
    pixels = size[0] * size[1]
    if (pixels > MAX_SWEEP_PIXELS or samples > MAX_SWEEP_SAMPLES
            or pixels * samples > MAX_SWEEP_VISITS):
        raise ValueError('layer-style swept alpha exceeds work budget')
    crop = alpha.crop(box)
    near_colour = np.asarray(_rgb(config, 'style_color'), dtype=np.float32) / 255
    far_colour = (np.asarray(_rgb(config, 'extrude_color'), dtype=np.float32) / 255
                  if config['style_type'] == 'extrusion' else near_colour)
    coverage = np.zeros((size[1], size[0]), dtype=np.float32)
    premul = np.zeros((size[1], size[0], 3), dtype=np.float32)
    for i in range(samples):
        t = i / (samples - 1)
        stamp = place_mask(crop, size, box[0] - left + dx * t, box[1] - top + dy * t)
        value = np.asarray(stamp, dtype=np.float32) / 255
        value *= 1 - config.get('extrude_fade', 0) * t
        addition = np.maximum(value - coverage, 0)
        colour = near_colour * (1 - t) + far_colour * t
        premul += addition[..., None] * colour
        coverage = np.maximum(coverage, value)
    rgb = np.divide(premul, coverage[..., None], out=np.zeros_like(premul), where=coverage[..., None] > 0)
    coverage *= config.get('style_strength', 1)
    quantized_alpha = np.uint8(np.rint(np.clip(coverage, 0, 1) * 255))
    rgb[quantized_alpha == 0] = 0
    out = np.concatenate([np.uint8(np.rint(np.clip(rgb, 0, 1) * 255)), quantized_alpha[..., None]], axis=-1)
    result.paste(Image.fromarray(out), (left, top))
    return result


def relief_bounds(alpha, config):
    """Conservative un-clipped bounds for the new styles (not surface resizing)."""
    box = alpha.getbbox()
    if box is None:
        return None
    kind = config['style_type']
    if kind in {'bevel', 'emboss', 'satin'}:
        return box
    if kind == 'edge_light':
        width = config.get('style_width', 3)
        position = config.get('outline_position', 'outer')
        radius = width if position == 'outer' else width / 2 if position == 'center' else 0
        halo = math.ceil(radius) + 1 if radius else 0
        return box[0] - halo, box[1] - halo, box[2] + halo, box[3] + halo
    angle = math.radians(config.get('extrude_angle', 45) % 360)
    length = config.get('extrude_length', 16)
    dx, dy = length * math.cos(angle), length * math.sin(angle)
    dx = 0 if abs(dx) < 1e-10 else dx
    dy = 0 if abs(dy) < 1e-10 else dy
    return (math.floor(box[0] + min(0, dx)), math.floor(box[1] + min(0, dy)),
            math.ceil(box[2] + max(0, dx)), math.ceil(box[3] + max(0, dy)))
