"""Bounded, deterministic CPU matte and perceptual chroma-key processing.

All inputs and outputs use straight RGBA. The standalone helpers are kept free
of project/Motion imports so pure image operations have a small test surface.
Legacy two-input alpha/luma mattes and RGB keys retain their original defaults.
"""
from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageColor, ImageFilter

MATTE_COMBINES = frozenset({"multiply", "min", "max", "add", "subtract", "difference", "screen"})
KEY_SPACES = frozenset({"rgb", "oklab"})
MAX_MATTE_INPUTS = 8
MAX_KEY_COLOURS = 8


def _finite(value, name, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite and in [{low}, {high}]")


def validate_matte(config, input_count):
    if not 2 <= input_count <= MAX_MATTE_INPUTS + 1:
        raise ValueError("matte requires one image and 1..8 mask inputs")
    channel = config.get("matte_channel", "alpha")
    if not isinstance(channel, str) or channel not in {"alpha", "luminance"}:
        raise ValueError("matte_channel must be alpha or luminance")
    method = config.get("mask_combine", "multiply")
    if not isinstance(method, str) or method not in MATTE_COMBINES:
        raise ValueError("invalid matte mask_combine")
    if type(config.get("invert", False)) is not bool:
        raise ValueError("matte invert must be boolean")
    _finite(config.get("feather", 0), "matte feather", 0, 128)
    _finite(config.get("expand", 0), "matte expand", -64, 64)
    _finite(config.get("mask_opacity", 1), "matte mask_opacity", 0, 1)


def validate_key(config):
    space = config.get("key_space", "rgb")
    if not isinstance(space, str) or space not in KEY_SPACES:
        raise ValueError("invalid key_space")
    colours = config.get("key_colors")
    if colours is not None and (not isinstance(colours, list) or not 1 <= len(colours) <= MAX_KEY_COLOURS):
        raise ValueError("key_colors must be a list of 1..8 colours")
    if colours is None:
        colours = [config.get("key_color", "#00ff00")]
    if "key_color" in config:
        colours = [config["key_color"], *(config.get("key_colors") or [])]
        if len(colours) > MAX_KEY_COLOURS:
            raise ValueError("at most 8 key colours")
    for colour in colours:
        if not isinstance(colour, str):
            raise ValueError("invalid chroma key colour")
        try:
            ImageColor.getrgb(colour)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid chroma key colour") from exc
    for name, default in (("tolerance", 0.1), ("softness", 0.1), ("despill", 0),
                          ("edge_decontaminate", 0)):
        _finite(config.get(name, default), f"chroma key {name}", 0, 1)
    _finite(config.get("edge_expand", 0), "chroma key edge_expand", -64, 64)
    _finite(config.get("edge_feather", 0), "chroma key edge_feather", 0, 128)
    _finite(config.get("edge_radius", 3), "chroma key edge_radius", 0.5, 16)


def _morph(mask: Image.Image, amount: float) -> Image.Image:
    """Grow/shrink a mask; fractional radii interpolate adjacent integer radii."""
    radius = abs(float(amount))
    if radius < 1e-9:
        return mask

    def sample(r):
        if r == 0:
            return mask
        kernel = 2 * r + 1
        return mask.filter(ImageFilter.MaxFilter(kernel) if amount > 0 else ImageFilter.MinFilter(kernel))

    a = int(math.floor(radius))
    b = int(math.ceil(radius))
    first = sample(a)
    if a == b:
        return first
    return Image.blend(first, sample(b), radius - a)


def _mask(image, channel):
    alpha = image.getchannel("A")
    if channel == "alpha":
        return alpha
    # Preserve the established Pillow luminance coefficient and alpha weighting.
    return ImageChops.multiply(image.convert("RGB").convert("L"), alpha)


def _combine(a, b, method):
    if method == "multiply":
        return ImageChops.multiply(a, b)
    if method == "min":
        return ImageChops.darker(a, b)
    if method == "max":
        return ImageChops.lighter(a, b)
    if method == "add":
        return ImageChops.add(a, b)
    if method == "subtract":
        return ImageChops.subtract(a, b)
    if method == "difference":
        return ImageChops.difference(a, b)
    # Screen is the union probability 1-(1-a)*(1-b), with 8-bit rounding.
    return ImageChops.invert(ImageChops.multiply(ImageChops.invert(a), ImageChops.invert(b)))


def apply_matte(images: list[Image.Image], config: dict) -> Image.Image:
    """Apply 1..8 track masks to one foreground, preserving its straight RGB."""
    result = images[0].copy()
    mask = _mask(images[1], config.get("matte_channel", "alpha"))
    for layer in images[2:]:
        mask = _combine(mask, _mask(layer, config.get("matte_channel", "alpha")),
                        config.get("mask_combine", "multiply"))
    mask = _morph(mask, config.get("expand", 0))
    if config.get("feather", 0):
        mask = mask.filter(ImageFilter.GaussianBlur(config["feather"]))
    if config.get("invert", False):
        mask = ImageChops.invert(mask)
    weight = config.get("mask_opacity", 1)
    if weight != 1:
        mask = Image.blend(Image.new("L", mask.size, 255), mask, weight)
    result.putalpha(ImageChops.multiply(result.getchannel("A"), mask))
    return result


def _linear_srgb(rgb):
    import numpy as np
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _oklab(rgb):
    """Linear-sRGB to OKLab, D65, using the published OKLab matrices."""
    import numpy as np
    c = _linear_srgb(rgb)
    lms = c @ np.array([
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ], dtype=np.float32).T
    return np.cbrt(lms) @ np.array([
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ], dtype=np.float32).T


def chroma_key(image: Image.Image, config: dict) -> Image.Image:
    """Legacy RGB-distance or perceptual OKLab multi-key, with optional edge work.

    Key colours select pixels to remove. Masks are applied to source alpha;
    straight RGB is preserved unless despill/decontamination is requested.
    """
    import numpy as np
    pixels = np.asarray(image, dtype=np.float32) / 255.0
    rgb = pixels[..., :3]
    keys = config.get("key_colors") or [config.get("key_color", "#00ff00")]
    if "key_colors" in config and "key_color" in config:
        keys = [config["key_color"], *config["key_colors"]]
    colours = np.asarray([ImageColor.getrgb(k) for k in keys], dtype=np.float32) / 255
    # Process key colours one at a time: avoid allocating an H×W×N×3
    # difference tensor, which becomes enormous on 4K/8K frames.
    values = _oklab(rgb) if config.get("key_space", "rgb") == "oklab" else rgb
    key_values = _oklab(colours) if config.get("key_space", "rgb") == "oklab" else colours
    dist = np.full(rgb.shape[:2], np.inf, dtype=np.float32)
    closest = np.zeros(rgb.shape[:2], dtype=np.uint8)
    for index, key in enumerate(key_values):
        delta = values - key
        metric = (np.sum(delta * delta, axis=-1) if config.get("key_space", "rgb") == "oklab"
                  else np.mean(delta * delta, axis=-1))
        selected = metric < dist
        dist = np.where(selected, metric, dist)
        closest[selected] = index
    dist = np.sqrt(dist)
    tolerance = config.get("tolerance", 0.1)
    softness = config.get("softness", 0.1)
    if softness == 0:
        keep = (dist > tolerance).astype(np.float32)
    else:
        keep = np.clip((dist - tolerance) / softness, 0, 1)
        keep = keep * keep * (3 - 2 * keep)
    if config.get("edge_expand", 0):
        matte = Image.fromarray(np.uint8(np.rint(keep * 255)), "L")
        # Positive edge expansion should reveal more foreground, same as matte.
        keep = np.asarray(_morph(matte, config["edge_expand"]), dtype=np.float32) / 255
    if config.get("edge_feather", 0):
        matte = Image.fromarray(np.uint8(np.rint(keep * 255)), "L")
        keep = np.asarray(matte.filter(ImageFilter.GaussianBlur(config["edge_feather"])), dtype=np.float32) / 255
    result = pixels.copy()
    result[..., 3] *= keep
    despill = config.get("despill", 0)
    if despill:
        key = colours[closest]
        dominant = np.argmax(key, axis=-1)
        for i in range(3):
            others = [j for j in range(3) if j != i]
            excess = np.maximum(0, result[..., i] - np.maximum(result[..., others[0]], result[..., others[1]]))
            result[..., i] -= (dominant == i) * excess * despill * (1 - keep)
    decontaminate = config.get("edge_decontaminate", 0)
    if decontaminate:
        radius = config.get("edge_radius", 3)
        confidence = pixels[..., 3] * np.clip((keep - 0.65) / 0.35, 0, 1)
        # Neighbouring confidently retained pixels supply replacement colour.
        weights = Image.fromarray(np.uint8(np.rint(confidence * 255)), "L")
        blurred_weights = np.asarray(weights.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255
        replacement = np.zeros_like(rgb)
        for i in range(3):
            weighted = Image.fromarray(np.uint8(np.rint(rgb[..., i] * confidence * 255)), "L")
            blurred = np.asarray(weighted.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255
            replacement[..., i] = np.divide(blurred, blurred_weights,
                                            out=rgb[..., i].copy(), where=blurred_weights > 1e-5)
        weight = ((1 - keep) * decontaminate * (blurred_weights > 1e-5))[..., None]
        result[..., :3] = result[..., :3] * (1 - weight) + replacement * weight
    return Image.fromarray(np.uint8(np.rint(np.clip(result, 0, 1) * 255)), "RGBA")
