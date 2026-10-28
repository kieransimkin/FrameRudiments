"""Deterministic multi-band spectral colour isolation and displacement.

CPU reference for the effect graph's M4 spectral displacement node.  Each
band selects *unassociated* source colour, samples the selected contribution
at a fractional translated coordinate, optionally recolours it, and composites
in authored order.  Transparent source pixels never inject hidden RGB.

Processing is striped to bound transient allocation on large 4K/8K renders.
No motion clocks, global state, GPU dependency or unpinned external assets.
"""
from __future__ import annotations

from colorsys import rgb_to_hsv
import math

from functools import lru_cache
from PIL import Image, ImageChops, ImageColor

MAX_SPECTRAL_BANDS = 8
MAX_OFFSET = 4096
TILE_ROWS = 128
SPECTRAL_SPACES = frozenset({"rgb", "hsv", "oklab"})
SPECTRAL_MODES = frozenset({"overlay", "replace"})
SPECTRAL_ALPHA_MODES = frozenset({"expanded", "source"})
BAND_FLOAT_PROPERTIES = frozenset({"tolerance", "softness", "opacity", "recolor"})
BAND_COLOUR_PROPERTIES = frozenset({"source_color", "target_color"})



@lru_cache(maxsize=1)
def _numpy():
    """Import NumPy only when the optional spectral renderer is evaluated."""
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "spectral_displacement requires NumPy; install framerudiments[effect-graph]"
        ) from exc
    return np


def _finite(value, description, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{description} must be a finite number in [{low}, {high}]")


def _rgb(value, description):
    if not isinstance(value, str):
        raise ValueError(f"{description} must be a CSS colour string")
    try:
        colour = tuple(ImageColor.getrgb(value))
        if len(colour) != 3:
            raise ValueError("alpha is not permitted in a selection colour")
        return colour
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {description}") from exc


def validate_spectral(config):
    """Validate every static parameter before any graph render is scheduled."""
    if not isinstance(config, dict):
        raise ValueError("spectral displacement configuration must be an object")
    bands = config.get("bands")
    if not isinstance(bands, list) or not 1 <= len(bands) <= MAX_SPECTRAL_BANDS:
        raise ValueError("spectral displacement requires 1..8 bands")
    if config.get("spectral_mode", "overlay") not in SPECTRAL_MODES:
        raise ValueError("invalid spectral_mode")
    if config.get("alpha_mode", "expanded") not in SPECTRAL_ALPHA_MODES:
        raise ValueError("invalid spectral alpha_mode")
    _finite(config.get("mix", 1), "spectral mix", 0, 1)
    seen = set()
    for index, band in enumerate(bands):
        if not isinstance(band, dict) or set(band) - {
            "id", "source_color", "target_color", "space", "tolerance", "softness",
            "offset", "opacity", "recolor",
        }:
            raise ValueError(f"invalid spectral band at index {index}")
        band_id = band.get("id")
        if (not isinstance(band_id, str) or not 1 <= len(band_id) <= 64
                or not band_id[0].isalpha() or not all(c.isalnum() or c in "_-" for c in band_id)
                or band_id in seen):
            raise ValueError("spectral band ids must be unique, short and alphanumeric")
        seen.add(band_id)
        if "source_color" not in band:
            raise ValueError("spectral band needs source_color")
        _rgb(band["source_color"], "source_color")
        if "target_color" in band:
            _rgb(band["target_color"], "target_color")
        if band.get("space", "oklab") not in SPECTRAL_SPACES:
            raise ValueError("unsupported spectral selection space")
        for name, default in (("tolerance", .08), ("softness", .08), ("opacity", 1), ("recolor", 0)):
            _finite(band.get(name, default), f"spectral band {name}", 0, 1)
        vector = band.get("offset", [0, 0])
        if (not isinstance(vector, (tuple, list)) or len(vector) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > MAX_OFFSET
                       for v in vector)):
            raise ValueError("spectral band offset must be a finite XY vector")


def spectral_control_parts(config, path):
    """Resolve a stable band-id control path without positional index fragility.

    Paths: ``bands.<id>.offset.x``, ``bands.<id>.source_color.r``, etc.
    Returns (band_index, field, component) or None if the path is invalid.
    """
    if not isinstance(path, str):
        return None
    parts = path.split(".")
    if len(parts) not in (3, 4) or parts[0] != "bands":
        return None
    band_index = next((i for i, b in enumerate(config.get("bands", []))
                       if b["id"] == parts[1]), None)
    if band_index is None:
        return None
    field = parts[2]
    component = parts[3] if len(parts) == 4 else None
    if field == "offset" and component in {"x", "y"}:
        return band_index, field, component
    if field in BAND_COLOUR_PROPERTIES and component in {"r", "g", "b"}:
        return band_index, field, component
    if component is None and (field in BAND_FLOAT_PROPERTIES or field == "space"):
        return band_index, field, None
    return None


def spectral_control_base(config, path):
    part = spectral_control_parts(config, path)
    if part is None:
        raise ValueError("unknown spectral control target")
    index, field, component = part
    band = config["bands"][index]
    if field == "offset":
        return band.get("offset", [0, 0])[0 if component == "x" else 1]
    if field in BAND_COLOUR_PROPERTIES:
        return _rgb(band.get(field, band["source_color"]), field)[{"r": 0, "g": 1, "b": 2}[component]]
    return band.get(field, {"tolerance": .08, "softness": .08, "opacity": 1,
                            "recolor": 0, "space": "oklab"}[field])


def spectral_control_apply(config, path, value):
    """Apply one validated control to a shallow-copied band table."""
    part = spectral_control_parts(config, path)
    if part is None:
        raise ValueError("unknown spectral control target")
    index, field, component = part
    updated = dict(config)
    updated["bands"] = list(config["bands"])
    band = dict(updated["bands"][index])
    updated["bands"][index] = band
    if field == "space":
        if value not in SPECTRAL_SPACES or not isinstance(value, str):
            raise ValueError("unsupported spectral selection space")
        band[field] = value
    elif field == "offset":
        _finite(value, path, -MAX_OFFSET, MAX_OFFSET)
        vector = list(band.get("offset", [0, 0]))
        vector[0 if component == "x" else 1] = value
        band[field] = vector
    elif field in BAND_COLOUR_PROPERTIES:
        _finite(value, path, 0, 255)
        col = list(_rgb(band.get(field, band["source_color"]), field))
        col[{"r": 0, "g": 1, "b": 2}[component]] = round(value)
        band[field] = "#%02x%02x%02x" % tuple(col)
    else:
        _finite(value, path, 0, 1)
        band[field] = value
    return updated


def _srgb_to_linear(rgb):
    np = _numpy()
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def _oklab(rgb):
    np = _numpy()
    linear = _srgb_to_linear(rgb)
    l_channel = np.cbrt(.4122214708 * linear[..., 0] + .5363325363 * linear[..., 1] + .0514459929 * linear[..., 2])
    m = np.cbrt(.2119034982 * linear[..., 0] + .6806995451 * linear[..., 1] + .1073969566 * linear[..., 2])
    s = np.cbrt(.0883024619 * linear[..., 0] + .2817188376 * linear[..., 1] + .6299787005 * linear[..., 2])
    return np.stack((.2104542553 * l_channel + .7936177850 * m - .0040720468 * s,
                     1.9779984951 * l_channel - 2.4285922050 * m + .4505937099 * s,
                     .0259040371 * l_channel + .7827717662 * m - .8086757660 * s), axis=-1)


def _hsv(rgb):
    np = _numpy()
    high = np.max(rgb, axis=-1)
    low = np.min(rgb, axis=-1)
    delta = high - low
    sat = np.divide(delta, high, out=np.zeros_like(high), where=high > 1e-8)
    hue = np.zeros_like(high)
    denom = np.where(delta > 1e-8, delta, 1)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hue = np.where((high == r) & (delta > 1e-8), ((g - b) / denom) % 6, hue)
    hue = np.where((high == g) & (delta > 1e-8), (b - r) / denom + 2, hue)
    hue = np.where((high == b) & (delta > 1e-8), (r - g) / denom + 4, hue)
    return hue / 6, sat, high


def _coverage(rgb, band):
    np = _numpy()
    target = np.asarray(_rgb(band["source_color"], "source_color"), dtype=np.float32) / 255
    space = band.get("space", "oklab")
    if space == "rgb":
        d = np.sqrt(np.mean((rgb - target) ** 2, axis=-1))
    elif space == "oklab":
        delta = _oklab(rgb) - _oklab(target)
        d = np.sqrt(np.sum(delta * delta, axis=-1))
    else:
        h, s, v = _hsv(rgb)
        key_h, key_s, key_v = rgb_to_hsv(*target)
        hue_delta = np.abs(h - key_h)
        hue_delta = np.minimum(hue_delta, 1 - hue_delta) * 2 * np.minimum(s, key_s)
        d = np.sqrt((hue_delta ** 2 + (s - key_s) ** 2 + (v - key_v) ** 2) / 3)
    tolerance = band.get("tolerance", .08)
    softness = band.get("softness", .08)
    if softness == 0:
        return (d <= tolerance).astype(np.float32)
    t = np.clip((d - tolerance) / softness, 0, 1)
    return (1 - t * t * (3 - 2 * t)).astype(np.float32)


def _float_rgb(src):
    np = _numpy()
    return src[..., :3].astype(np.float32) / 255


def _replacement_base(image, source, bands, rows):
    """Attenuate original ink wherever an extracted band will be moved."""
    np = _numpy()
    width, height = image.size
    out = Image.new("RGBA", image.size)
    for top in range(0, height, rows):
        bottom = min(top + rows, height)
        src = source[top:bottom]
        rgb = _float_rgb(src)
        removal = np.zeros((bottom - top, width), np.float32)
        for band in bands:
            removal = np.maximum(removal, _coverage(rgb, band) * band.get("opacity", 1))
        tile = src.copy()
        tile[..., 3] = np.uint8(np.rint(tile[..., 3].astype(np.float32) * (1 - removal)))
        out.paste(Image.fromarray(tile, "RGBA"), (0, top))
    return out


def _displaced_tile(source, band, top, bottom, width):
    """Bilinear interpolation of selected *premultiplied* contributions."""
    np = _numpy()
    dx, dy = band.get("offset", [0, 0])
    x = np.arange(width, dtype=np.float32)[None, :] - np.float32(dx)
    y = np.arange(top, bottom, dtype=np.float32)[:, None] - np.float32(dy)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    tx, ty = x - x0, y - y0
    rows, height = bottom - top, source.shape[0]
    selected_alpha = np.zeros((rows, width), dtype=np.float32)
    selected_rgb = np.zeros((rows, width, 3), dtype=np.float32)
    for ox, oy, weight in ((0, 0, (1 - tx) * (1 - ty)),
                           (1, 0, tx * (1 - ty)),
                           (0, 1, (1 - tx) * ty),
                           (1, 1, tx * ty)):
        xi = np.broadcast_to(x0 + ox, (rows, width))
        yi = np.broadcast_to(y0 + oy, (rows, width))
        valid = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
        sample = source[np.clip(yi, 0, height - 1), np.clip(xi, 0, width - 1)]
        rgb = _float_rgb(sample)
        a = (sample[..., 3].astype(np.float32) / 255 * _coverage(rgb, band)
             * weight * valid)
        selected_alpha += a
        selected_rgb += rgb * a[..., None]
    selected_rgb = np.divide(selected_rgb, selected_alpha[..., None],
                             out=np.zeros_like(selected_rgb), where=selected_alpha[..., None] > 1e-7)
    recolor = band.get("recolor", 0)
    if recolor:
        destination = np.asarray(_rgb(band.get("target_color", band["source_color"]),
                                      "target_color"), dtype=np.float32) / 255
        selected_rgb = selected_rgb * (1 - recolor) + destination * recolor
    tile = np.empty((rows, width, 4), dtype=np.uint8)
    tile[..., :3] = np.uint8(np.rint(np.clip(selected_rgb, 0, 1) * 255))
    tile[..., 3] = np.uint8(np.rint(np.clip(selected_alpha * band.get("opacity", 1), 0, 1) * 255))
    return Image.fromarray(tile, "RGBA")


def _premultiplied_mix(original, effected, amount, rows):
    np = _numpy()
    width, height = original.size
    result = Image.new("RGBA", original.size)
    for top in range(0, height, rows):
        bottom = min(top + rows, height)
        lhs = np.asarray(original.crop((0, top, width, bottom)), dtype=np.float32) / 255
        rhs = np.asarray(effected.crop((0, top, width, bottom)), dtype=np.float32) / 255
        a, b = lhs[..., 3:4], rhs[..., 3:4]
        alpha = (1 - amount) * a + amount * b
        premul = (1 - amount) * lhs[..., :3] * a + amount * rhs[..., :3] * b
        rgb = np.divide(premul, alpha, out=np.zeros_like(premul), where=alpha > 1e-8)
        rgba = np.uint8(np.rint(np.clip(np.concatenate((rgb, alpha), axis=-1), 0, 1) * 255))
        result.paste(Image.fromarray(rgba, "RGBA"), (0, top))
    return result


def spectral_displacement(image, config, *, tile_rows=TILE_ROWS):
    """Render arbitrary selected colour bands through independent XY vectors.

    ``overlay`` keeps original ink and overlays shifted contributions;
    ``replace`` removes selected ink before applying translated bands.
    ``source`` alpha limits the result to the original silhouette;
    ``expanded`` allows separated colours to extend beyond it.
    """
    np = _numpy()
    validate_spectral(config)
    if image.mode != "RGBA":
        raise ValueError("spectral displacement requires RGBA input")
    if type(tile_rows) is not int or not 1 <= tile_rows <= 512:
        raise ValueError("tile_rows must be 1..512")
    mix = config.get("mix", 1)
    if mix == 0:
        return image.copy()
    source = np.asarray(image, dtype=np.uint8)
    width, height = image.size
    bands = config["bands"]
    if config.get("spectral_mode", "overlay") == "replace":
        out = _replacement_base(image, source, bands, tile_rows)
    else:
        out = image.copy()
    for band in bands:
        for top in range(0, height, tile_rows):
            bottom = min(top + tile_rows, height)
            tile = _displaced_tile(source, band, top, bottom, width)
            out.alpha_composite(tile, dest=(0, top))
    if config.get("alpha_mode", "expanded") == "source":
        out.putalpha(ImageChops.darker(out.getchannel("A"), image.getchannel("A")))
        # Do not leave nonzero hidden RGB outside the clipped source silhouette.
        transparent = out.getchannel("A").point(lambda a: 255 if a == 0 else 0)
        out.paste((0, 0, 0, 0), mask=transparent)
    if mix != 1:
        out = _premultiplied_mix(image, out, mix, tile_rows)
    return out
