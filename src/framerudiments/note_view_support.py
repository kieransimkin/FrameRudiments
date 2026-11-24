"""Shared bounded labels and time axes for note consumers; no playback clock."""

from functools import lru_cache
import math
from PIL import ImageColor, ImageFont
from .io import integer, number


def text(value, name, maximum=80):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or not value.isprintable():
        raise ValueError(f"{name} needs a nonempty printable single line up to {maximum} characters")
    return value.strip()


def ids(value, name, *, low=0, high=127):
    if not isinstance(value, list) or not value or len(value) > high - low + 1:
        raise ValueError(f"{name} needs a nonempty bounded list")
    result = tuple(integer(v, name, minimum=low, maximum=high) for v in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} needs unique values")
    return frozenset(result)


def colour(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} needs a CSS colour")
    try:
        return ImageColor.getcolor(value, "RGBA")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} needs a CSS colour") from exc


@lru_cache(maxsize=128)
def _font(path, size):
    return ImageFont.truetype(str(path), max(1, size))


def font(layer, size):
    if not layer.project.font_ref:
        raise ValueError("Named note views need an explicit pinned project font")
    return _font(layer.project.font_ref["path"], max(1, round(size)))


def label(draw, xy, value, layer, size, *, fill="#d7e5ef", max_width=None):
    if not layer.labels:
        return
    face = font(layer, size)
    value = str(value)
    if max_width is not None:
        while value and draw.textlength(value, font=face) > max_width:
            value = value[:-1]
    if value:
        draw.text(xy, value, font=face, fill=fill)


def window(layer, now, mode="scroll"):
    if mode == "overview":
        return 0.0, float(layer.project.duration)
    return now - layer.history, now + layer.ahead


def axis(draw, layer, now, size, settings, *, mode="scroll"):
    """Draw continuous output seconds. Source beat/tempo authority is unchanged."""
    w, h = size
    left = w * settings.get("label_fraction", 0.16)
    top = h * 0.13
    bottom = h * 0.95
    low, high = window(layer, now, mode)
    span = high - low
    step = settings.get("grid_seconds")
    if step is None:
        target = span / 8
        magnitude = 10 ** math.floor(math.log10(max(target, 1e-9)))
        step = next(v * magnitude for v in (1, 2, 5, 10) if v * magnitude >= target)

    def x(seconds):
        return left + (seconds - low) / span * (w - left - w * 0.02)

    for tick in range(math.ceil(max(0, low) / step), math.floor(high / step) + 1):
        stamp = tick * step
        if stamp > float(layer.project.duration):
            continue
        px = x(stamp)
        draw.line((px, top, px, bottom), fill="#294256", width=max(1, round(w / 960)))
        label(draw, (px + 2, h * 0.035), f"{stamp:g}s", layer, min(h * 0.045, w * 0.027))
    return left, top, w - w * 0.02, bottom, x


def validate_axis(settings):
    if "label_fraction" in settings:
        number(settings["label_fraction"], "Note lane label fraction", minimum=0.08, maximum=0.4)
    if "grid_seconds" in settings:
        number(settings["grid_seconds"], "Note time grid", minimum=0.025, maximum=3600)
