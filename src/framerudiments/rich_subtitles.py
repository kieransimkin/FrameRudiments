"""Opt-in, masked subtitle typography. Existing plain captions retain their pixels.

The owned masks are generated from checksum-pinned fonts using Pillow; all colour
fills and highlights are raster effects. No font files or song text are bundled.
"""
from __future__ import annotations

import colorsys
import math
import re

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter

from .io import fields, integer, number


MAX_STOPS = 16


def rgba(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a CSS colour")
    try:
        return ImageColor.getcolor(value, "RGBA")
    except ValueError as exc:
        raise ValueError(f"Invalid {name}: {value!r}") from exc


def stops(value):
    if not isinstance(value, (tuple, list)) or not 2 <= len(value) <= MAX_STOPS:
        raise ValueError("Caption gradient requires 2..16 colour stops")
    rows = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            rows.append((index / (len(value) - 1), rgba(item, "gradient stop")))
        elif isinstance(item, dict):
            fields(item, {"at", "colour"}, "gradient stop")
            rows.append((number(item["at"], "stop position", minimum=0, maximum=1),
                         rgba(item["colour"], "gradient stop colour")))
        else:
            raise ValueError("Gradient stop must be a colour or {at, colour}")
    if rows[0][0] != 0 or rows[-1][0] != 1 or any(a[0] >= b[0] for a, b in zip(rows, rows[1:])):
        raise ValueError("Gradient positions must strictly increase from 0 to 1")
    return rows


def _colour_row(rows, u, *, space="rgb"):
    u = max(0.0, min(1.0, u))
    for (p, col), (q, nxt) in zip(rows, rows[1:]):
        if u <= q:
            t = min(1, max(0, (u - p) / (q - p)))
            if space == "hsv":
                a = colorsys.rgb_to_hsv(*(c / 255 for c in col[:3]))
                b = colorsys.rgb_to_hsv(*(c / 255 for c in nxt[:3]))
                delta = ((b[0] - a[0] + .5) % 1) - .5
                return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(
                    (a[0] + t * delta) % 1, a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])))
            return tuple(round(col[k] * (1 - t) + nxt[k] * t) for k in range(3))
    return rows[-1][1][:3]


def gradient_image(size, colours, angle=90, space="rgb"):
    """Gradient projected onto a bounded text raster, not the entire frame."""
    width, height = size
    if space not in {"rgb", "hsv"}:
        raise ValueError("Gradient space must be rgb or hsv")
    rows = stops(colours)
    direction = math.radians(angle)
    dx, dy = math.cos(direction), math.sin(direction)
    corners = [x * dx + y * dy for x in (0, max(0, width - 1))
               for y in (0, max(0, height - 1))]
    lo, hi = min(corners), max(corners)
    spread = max(1e-7, hi - lo)
    image = Image.new("RGBA", size)
    pixels = image.load()
    cache = {}
    for y in range(height):
        for x in range(width):
            pos = round((x * dx + y * dy - lo) / spread * 1024) / 1024
            if pos not in cache:
                cache[pos] = (*_colour_row(rows, pos, space=space), 255)
            pixels[x, y] = cache[pos]
    return image


def _tinted(mask, colour):
    colour = rgba(colour, "effect colour") if isinstance(colour, str) else colour
    im = Image.new("RGBA", mask.size, colour)
    im.putalpha(mask.point(lambda v: round(v * colour[3] / 255)))
    return im


def _strength(mask, alpha):
    return mask.point(lambda value: round(value * alpha))


class RichTextAppearance:
    """Single opt-in effect profile shared by captions, word captions and titles."""

    def __init__(self, config):
        fields(config, {"gradient", "stroke", "bevel", "emboss", "inner_glow",
                        "outer_glow", "drop_shadow"}, "rich subtitle effects")
        self.gradient = None
        if "gradient" in config:
            val = config["gradient"]
            fields(val, {"stops", "angle", "space"}, "subtitle gradient")
            self.gradient = dict(stops=stops(val["stops"]),
                                 angle=number(val.get("angle", 90), "gradient angle", minimum=-360, maximum=360),
                                 space=val.get("space", "rgb"))
            if self.gradient["space"] not in {"rgb", "hsv"}:
                raise ValueError("Gradient space must be rgb or hsv")
        self.stroke = self._stroke(config.get("stroke", {}))
        self.bevel = self._relief(config.get("bevel", {}), "bevel")
        self.emboss = self._relief(config.get("emboss", {}), "emboss")
        self.inner = self._glow(config.get("inner_glow", {}), "inner_glow")
        self.outer = self._glow(config.get("outer_glow", {}), "outer_glow")
        self.shadow = self._shadow(config.get("drop_shadow", {}))

    @staticmethod
    def _stroke(row):
        fields(row, {"width", "colour"}, "text stroke")
        return dict(width=integer(row.get("width", 0), "stroke width", maximum=24),
                    colour=rgba(row.get("colour", "#111827"), "stroke colour"))

    @staticmethod
    def _relief(row, name):
        fields(row, {"depth", "strength", "angle", "highlight", "shadow"}, name)
        return dict(depth=integer(row.get("depth", 0), f"{name} depth", maximum=12),
                    strength=number(row.get("strength", .65), f"{name} strength", minimum=0, maximum=1),
                    angle=number(row.get("angle", 135), f"{name} angle", minimum=-360, maximum=360),
                    highlight=rgba(row.get("highlight", "#ffffff"), f"{name} highlight"),
                    shadow=rgba(row.get("shadow", "#05091b"), f"{name} shadow"))

    @staticmethod
    def _glow(row, name):
        fields(row, {"radius", "strength", "colour"}, name)
        return dict(radius=number(row.get("radius", 0), f"{name} radius", minimum=0, maximum=28),
                    strength=number(row.get("strength", .65), f"{name} strength", minimum=0, maximum=1),
                    colour=rgba(row.get("colour", "#75dfff"), f"{name} colour"))

    @staticmethod
    def _shadow(row):
        fields(row, {"dx", "dy", "blur", "opacity", "colour"}, "subtitle drop shadow")
        return dict(dx=integer(row.get("dx", 0), "shadow dx", minimum=-64, maximum=64),
                    dy=integer(row.get("dy", 0), "shadow dy", minimum=-64, maximum=64),
                    blur=number(row.get("blur", 0), "shadow blur", minimum=0, maximum=32),
                    opacity=number(row.get("opacity", 0), "shadow opacity", minimum=0, maximum=1),
                    colour=rgba(row.get("colour", "#000000"), "shadow colour"))

    def draw(self, image, block, text, *, colour=None, active=None, completed=()):
        if not text:
            return []
        font, rows, bboxes, height = block.layout(text)
        left, top, right, bottom = block.box
        if block.config.get("panel"):
            ImageDraw.Draw(image).rounded_rectangle(block.box, radius=12, fill=block.config["panel"])
        # Use an effects margin so blurred halos aren't prematurely cropped.
        padding = min(96, math.ceil(max(self.outer["radius"] * 3,
                       self.shadow["blur"] * 3 + max(abs(self.shadow["dx"]), abs(self.shadow["dy"])),
                       self.stroke["width"] + 3, self.bevel["depth"] + 3, self.emboss["depth"] + 3)))
        x0, y0 = max(0, round(left - padding)), max(0, round(top - padding))
        x1, y1 = min(image.width, round(right + padding)), min(image.height, round(bottom + padding))
        if x1 <= x0 or y1 <= y0:
            return []
        size = (x1 - x0, y1 - y0)
        mask = Image.new("L", size)
        ink = ImageDraw.Draw(mask)
        word_masks = {}
        word_index = 0
        bounds = []
        y = top + (bottom - top - height) / 2
        for row, bb in zip(rows, bboxes):
            x = left + (right - left - (bb[2] - bb[0])) / 2 - bb[0]
            rect = (x + bb[0], y, x + bb[2], y + bb[3] - bb[1])
            if not (left <= rect[0] and rect[2] <= right and top <= rect[1] and rect[3] <= bottom):
                raise ValueError("Rich subtitle text outside its configured box")
            bounds.append([round(v, 3) for v in rect])
            ink.text((x - x0, y - bb[1] - y0), row, font=font, fill=255)
            for match in re.finditer(r"\S+", row):
                if word_index == active or word_index in completed:
                    glyph = Image.new("L", size)
                    offset = ink.textlength(row[:match.start()], font=font)
                    ImageDraw.Draw(glyph).text((x - x0 + offset, y - bb[1] - y0),
                                               match.group(), font=font, fill=255)
                    word_masks[word_index] = glyph
                word_index += 1
            y += bb[3] - bb[1] + block.spacing
        layer = Image.new("RGBA", size)
        shadow = self.shadow
        if shadow["opacity"]:
            shifted = ImageChops.offset(mask, shadow["dx"], shadow["dy"])
            if shadow["dx"] > 0:
                ImageDraw.Draw(shifted).rectangle((0, 0, shadow["dx"] - 1, size[1]), fill=0)
            elif shadow["dx"] < 0:
                ImageDraw.Draw(shifted).rectangle((size[0] + shadow["dx"], 0, size[0], size[1]), fill=0)
            if shadow["dy"] > 0:
                ImageDraw.Draw(shifted).rectangle((0, 0, size[0], shadow["dy"] - 1), fill=0)
            elif shadow["dy"] < 0:
                ImageDraw.Draw(shifted).rectangle((0, size[1] + shadow["dy"], size[0], size[1]), fill=0)
            if shadow["blur"]:
                shifted = shifted.filter(ImageFilter.GaussianBlur(shadow["blur"]))
            layer.alpha_composite(_tinted(_strength(shifted, shadow["opacity"]), shadow["colour"]))
        if self.outer["radius"] and self.outer["strength"]:
            glow = mask.filter(ImageFilter.GaussianBlur(self.outer["radius"]))
            layer.alpha_composite(_tinted(_strength(glow, self.outer["strength"]), self.outer["colour"]))
        if self.stroke["width"]:
            halo = mask.filter(ImageFilter.MaxFilter(2 * self.stroke["width"] + 1))
            layer.alpha_composite(_tinted(halo, self.stroke["colour"]))
        if self.gradient:
            stops_data = self.gradient["stops"]
            direction = self.gradient["angle"]
            fill = gradient_image(size, [dict(at=p, colour=f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}")
                                         for p, c in stops_data], direction, self.gradient["space"])
            fill.putalpha(mask)
        else:
            fill = _tinted(mask, colour or block.colour)
        layer.alpha_composite(fill)
        for k, wm in word_masks.items():
            special = block.config.get("word_colour", "#ffe082") if k == active else block.config.get(
                "completed_colour", "#9ad9d5")
            layer.alpha_composite(_tinted(wm, special))
        if self.inner["radius"] and self.inner["strength"]:
            erosion_radius = 2 * min(12, max(1, math.ceil(self.inner["radius"]))) + 1
            inner = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(erosion_radius)))
            inner = inner.filter(ImageFilter.GaussianBlur(max(.2, self.inner["radius"] / 2)))
            inner = ImageChops.multiply(inner, mask)
            layer.alpha_composite(_tinted(_strength(inner, self.inner["strength"]), self.inner["colour"]))
        for setting in (self.bevel, self.emboss):
            depth = setting["depth"]
            if depth == 0 or not setting["strength"]:
                continue
            angle = math.radians(setting["angle"])
            dx, dy = round(math.cos(angle) * depth), round(math.sin(angle) * depth)
            light = ImageChops.subtract(mask, ImageChops.offset(mask, dx, dy))
            dark = ImageChops.subtract(mask, ImageChops.offset(mask, -dx, -dy))
            layer.alpha_composite(_tinted(_strength(light, setting["strength"]), setting["highlight"]))
            layer.alpha_composite(_tinted(_strength(dark, setting["strength"]), setting["shadow"]))
        image.alpha_composite(layer, (x0, y0))
        return bounds


def word_centres(block, text):
    """Find word centres using the same wrapped glyph geometry as rich and plain captions."""
    font, rows, boxes, height = block.layout(text)
    left, top, right, bottom = block.box
    pen = ImageDraw.Draw(Image.new("L", (1, 1)))
    y = top + (bottom - top - height) / 2
    result = []
    for row, bb in zip(rows, boxes):
        x = left + (right - left - (bb[2] - bb[0])) / 2 - bb[0]
        for match in re.finditer(r"\S+", row):
            start = x + pen.textlength(row[:match.start()], font=font)
            finish = start + pen.textlength(match.group(), font=font)
            result.append((0.5 * (start + finish), y))
        y += bb[3] - bb[1] + block.spacing
    return tuple(result)
