"""Deterministic, opt-in colour and image transforms for *all* rudiment renderers.

This is a compositing/presentation operation, not a change to calibrated data.
Absent `visual` the renderer's exact original pixels are retained.
"""
import colorsys
import math

from PIL import Image, ImageColor, ImageFilter

from .io import fields, number, integer
from .post_effects import POST_RANGES, apply_post_effects
from .quality_effects import QUALITY_RANGES, apply_quality_effects


PALETTES = {
    "aurora": ("#062b45", "#15c4b0", "#f1dc84", "#ec73e8"),
    "neon": ("#160b39", "#00e5ff", "#ff27bd", "#fff2aa"),
    "fire": ("#180520", "#a32740", "#ff6827", "#ffe9a2"),
    "ice": ("#09213c", "#326bb6", "#7adce9", "#f4ffff"),
    "sunset": ("#261348", "#c0427a", "#f6a263", "#fff4c5"),
    "ocean": ("#08192f", "#145b88", "#1cbeb0", "#caf9de"),
    "synthwave": ("#101038", "#993ce6", "#fc468e", "#f9ba6b"),
    "copper": ("#241919", "#855039", "#cb895f", "#f0cc9a"),
    "forest": ("#101f2a", "#187456", "#70b16c", "#e3f5b9"),
    "mono": ("#14202e", "#556b80", "#b1c9d1", "#ffffff"),
    "rainbow": ("#ff4466", "#ffbe45", "#5be07d", "#50aefb", "#bc68f8", "#ff4466"),
    "heat": ("#050414", "#622090", "#ec3b50", "#ffa844", "#fff7bc"),
    "viridis-inspired": ("#440154", "#31688e", "#35b779", "#fde725"),
    "magma-inspired": ("#000004", "#51127c", "#b73779", "#fc8961", "#fcfdbf"),
    "diverging": ("#2171b5", "#eeeeee", "#d73027"),
    "pastel": ("#a8c8ff", "#bfc0ef", "#f6c7dc", "#f9deb1"),
}

VISUAL_RANGES = {
    "hue_shift": (-3600, 3600, 0),
    "saturation": (0, 4, 1),
    "brightness": (0, 4, 1),
    "contrast": (0, 4, 1),
    "opacity": (0, 1, 1),
    "gamma": (.1, 5, 1),
    "threshold": (0, 1, 0),
    "posterize": (0, 16, 0),
    "glow": (0, 24, 0),
    "blur": (0, 12, 0),
    "rotation": (-36000, 36000, 0),
    "scale": (.1, 3, 1),
    "scale_x": (.1, 3, 1),
    "scale_y": (.1, 3, 1),
    "offset_x": (-1, 1, 0),
    "offset_y": (-1, 1, 0),
    "gradient_phase": (-100, 100, 0),
    "gradient_mix": (0, 1, 1),
    "gradient_cycles": (.01, 32, 1),
}
VISUAL_RANGES.update(POST_RANGES)
VISUAL_RANGES.update(QUALITY_RANGES)
_GRADIENT_MODES = {"horizontal", "vertical", "diagonal", "anti-diagonal", "radial",
                   "angular", "diamond", "wave", "checker", "level", "frequency", "time"}


def _clamp(value, lo=0, hi=1):
    return max(lo, min(hi, value))


def palette(value):
    if isinstance(value, str):
        if value not in PALETTES:
            raise ValueError(f"Unknown visual palette: {value}")
        value = PALETTES[value]
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 32:
        raise ValueError("Palette must have 1..32 CSS colours")
    if any(not isinstance(s, str) for s in value):
        raise ValueError("Palette entries must be CSS colour strings")
    return tuple(ImageColor.getcolor(s, "RGBA") for s in value)


def _stops(value):
    if isinstance(value, str):
        colours = palette(value)
        return tuple((j / max(1, len(colours) - 1), c) for j, c in enumerate(colours))
    if not isinstance(value, (list, tuple)) or not 2 <= len(value) <= 32:
        raise ValueError("Gradient stops need 2..32 colours/positioned stops")
    if all(isinstance(item, str) for item in value):
        colours = palette(value)
        return tuple((j / (len(colours) - 1), c) for j, c in enumerate(colours))
    positions = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Use CSS colours or {at, colour} objects for gradient stops")
        fields(item, {"at", "colour"}, "gradient stop")
        pos = number(item["at"], "gradient stop position", minimum=0, maximum=1)
        if not isinstance(item["colour"], str):
            raise ValueError("Gradient stop colour must be CSS string")
        positions.append((pos, ImageColor.getcolor(item["colour"], "RGBA")))
    if positions[0][0] != 0 or positions[-1][0] != 1 or any(a[0] >= b[0] for a, b in zip(positions, positions[1:])):
        raise ValueError("Gradient stops must strictly increase from 0 to 1")
    return tuple(positions)


class VisualStyle:
    """Stateless, bounded spatial gradients + hue/saturation/level/image transforms."""

    def __init__(self, config):
        fields(config, {"palette", "gradient", *VISUAL_RANGES}, "visual style")
        self.palette = palette(config["palette"]) if "palette" in config else None
        for key, (lo, hi, default) in VISUAL_RANGES.items():
            setattr(self, key, number(config.get(key, default), f"visual {key}", minimum=lo, maximum=hi))
        value = config.get("gradient")
        if value is not None:
            if not isinstance(value, dict):
                raise ValueError("visual.gradient must be an object")
            fields(value, {"mode", "stops", "space", "wrap", "reverse", "blend", "bands", "phase", "mix", "cycles"}, "gradient")
            self.gradient_mode = value.get("mode", "vertical")
            if self.gradient_mode not in _GRADIENT_MODES:
                raise ValueError("Unknown gradient coordinate mode")
            self.gradient_stops = _stops(value.get("stops", config.get("palette", "aurora")))
            self.gradient_space = value.get("space", "rgb")
            if self.gradient_space not in {"rgb", "hsv"}:
                raise ValueError("Gradient space must be rgb or hsv")
            self.gradient_wrap = value.get("wrap", "clamp")
            if self.gradient_wrap not in {"clamp", "repeat", "mirror"}:
                raise ValueError("Gradient wrap must be clamp, repeat or mirror")
            self.gradient_blend = value.get("blend", "tint")
            if self.gradient_blend not in {"replace", "tint", "multiply", "screen", "mix"}:
                raise ValueError("Gradient blend must be replace/tint/multiply/screen/mix")
            self.gradient_bands = integer(value.get("bands", 0), "gradient bands", minimum=0, maximum=256)
            self.gradient_reverse = value.get("reverse", False)
            if not isinstance(self.gradient_reverse, bool):
                raise ValueError("Gradient reverse must be boolean")
            self.gradient_phase += number(value.get("phase", 0), "gradient phase", minimum=-100, maximum=100)
            self.gradient_mix *= number(value.get("mix", 1), "gradient mix", minimum=0, maximum=1)
            self.gradient_cycles *= number(value.get("cycles", 1), "gradient cycles", minimum=.01, maximum=32)
        else:
            self.gradient_mode = None

    def _gradient_at(self, u):
        raw = 1 - u if self.gradient_reverse else u
        raw = raw * self.gradient_cycles + self.gradient_phase
        if self.gradient_wrap == "repeat":
            u = raw % 1
        elif self.gradient_wrap == "mirror":
            u = 1 - abs(raw % 2 - 1)
        else:
            u = _clamp(raw)
        if self.gradient_bands > 1:
            u = round(u * (self.gradient_bands - 1)) / (self.gradient_bands - 1)
        stops = self.gradient_stops
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if u <= b:
                t = _clamp((u - a) / (b - a))
                if self.gradient_space == "hsv":
                    h1, s1, v1 = colorsys.rgb_to_hsv(*(x / 255 for x in ca[:3]))
                    h2, s2, v2 = colorsys.rgb_to_hsv(*(x / 255 for x in cb[:3]))
                    dh = ((h2 - h1 + .5) % 1) - .5
                    rgb = colorsys.hsv_to_rgb((h1 + t * dh) % 1, s1 + t * (s2 - s1), v1 + t * (v2 - v1))
                    return tuple(round(x * 255) for x in rgb)
                return tuple(round(ca[j] * (1 - t) + cb[j] * t) for j in range(3))
        return stops[-1][1][:3]

    def _position(self, x, y, w, h, rgb):
        xx, yy = x / max(1, w - 1), y / max(1, h - 1)
        dx, dy = xx - .5, yy - .5
        mode = self.gradient_mode
        if mode in {"horizontal", "time"}:
            return xx
        if mode in {"vertical", "frequency"}:
            return 1 - yy
        if mode == "diagonal":
            return (xx + 1 - yy) / 2
        if mode == "anti-diagonal":
            return ((1 - xx) + (1 - yy)) / 2
        if mode == "radial":
            return _clamp(math.hypot(dx, dy) * 2)
        if mode == "angular":
            return ((math.atan2(dx, -dy) / (2 * math.pi)) % 1)
        if mode == "diamond":
            return _clamp((abs(dx) + abs(dy)))
        if mode == "wave":
            return .5 + .5 * math.sin(6 * math.pi * xx + 4 * math.pi * yy)
        if mode == "checker":
            return (int(xx * 10) + int(yy * 10)) % 2
        return max(rgb) / 255  # 'level' measures rendered luminance proxy, not audio RMS.

    def _colour(self, colour, gradient):
        r, g, b = colour
        if gradient is not None:
            if self.gradient_blend == "replace":
                mixed = gradient
            elif self.gradient_blend == "multiply":
                mixed = tuple(round(a * z / 255) for a, z in zip(colour, gradient))
            elif self.gradient_blend == "screen":
                mixed = tuple(round(255 - (255 - a) * (255 - z) / 255) for a, z in zip(colour, gradient))
            elif self.gradient_blend == "tint":
                mixed = tuple(round(z * max(colour) / 255) for z in gradient)
            else:
                mixed = gradient
            r, g, b = (round(a * (1 - self.gradient_mix) + z * self.gradient_mix)
                       for a, z in zip(colour, mixed))
        if self.hue_shift or self.saturation != 1:
            hue, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            r, g, b = (round(x * 255) for x in colorsys.hsv_to_rgb(
                (hue + self.hue_shift / 360) % 1, _clamp(sat * self.saturation), val))
        channels = []
        for c in (r, g, b):
            value = _clamp((c / 255 - .5) * self.contrast + .5)
            value = _clamp(value * self.brightness)
            value = value ** (1 / self.gamma)
            if self.posterize >= 2:
                value = round(value * (round(self.posterize) - 1)) / (round(self.posterize) - 1)
            channels.append(round(_clamp(value) * 255))
        return tuple(channels)

    def _transform(self, image):
        w, h = image.size
        if self.rotation:
            image = image.rotate(self.rotation, resample=Image.Resampling.BICUBIC, expand=False)
        sx, sy = self.scale * self.scale_x, self.scale * self.scale_y
        if sx != 1 or sy != 1 or self.offset_x or self.offset_y:
            sw = max(1, round(w * sx))
            sh = max(1, round(h * sy))
            image = image.resize((sw, sh), resample=Image.Resampling.BICUBIC)
            base = Image.new("RGBA", (w, h))
            x = round((w - sw) / 2 + self.offset_x * w)
            y = round((h - sh) / 2 + self.offset_y * h)
            base.paste(image, (x, y))
            image = base
        return image

    def apply(self, image, background=(0, 0, 0, 0)):
        """Transform RGBA ink, preserving transparent/solid renderer background."""
        if image.mode != "RGBA":
            raise ValueError("Visual pipeline expects RGBA viewport")
        image = self._transform(image)
        if self.blur:
            image = image.filter(ImageFilter.GaussianBlur(self.blur))
        if self.glow:
            blurred = image.filter(ImageFilter.GaussianBlur(self.glow))
            # Draw a diffuse coloured halo underneath the sharp ink.
            blurred.alpha_composite(image)
            image = blurred
        grade = (self.gradient_mode is not None or self.hue_shift != 0 or self.saturation != 1
                 or self.brightness != 1 or self.contrast != 1 or self.gamma != 1
                 or self.threshold or self.posterize >= 2 or self.opacity != 1)
        if not grade:
            return apply_quality_effects(apply_post_effects(image, self), self)
        pixels = image.load()
        w, h = image.size
        # RGB results are memoised for nonspatial grading. Gradients preserve
        # existing alpha/level; spatial samples are quantised to limit cost.
        cache = {}
        for y in range(h):
            for x in range(w):
                r, g, b, a = pixels[x, y]
                if not a or (r, g, b, a) == background:
                    continue
                if self.threshold and max(r, g, b) / 255 < self.threshold:
                    pixels[x, y] = (r, g, b, 0)
                    continue
                u = self._position(x, y, w, h, (r, g, b)) if self.gradient_mode else 0
                u = round(u * 512) / 512
                key = (r, g, b, u)
                if key not in cache:
                    cache[key] = self._colour((r, g, b), self._gradient_at(u) if self.gradient_mode else None)
                pixels[x, y] = (*cache[key], round(a * self.opacity))
        return apply_quality_effects(apply_post_effects(image, self), self)
