"""Deterministic sprite-backed Rudiment views (no replacement of vector modes).

Each sprite renderer displays *observed* native frame samples. Spectrum and
spectrogram modes require calibrated Fourier-band data supplied by StemLab;
this module does not calculate FFT, STFT, PCM waveform or acoustic true peaks.
Only explicitly pinned custom image atlases can be loaded from projects.
"""

from __future__ import annotations

from functools import lru_cache
import colorsys
import json
import math
from pathlib import Path
from hashlib import sha256 as _sha256

from PIL import Image, ImageChops, ImageColor, ImageFilter

from .io import integer, number
from .rudiment_renderers import _Viewport
from .spectrum import analyse_spectrum
from .sprite_catalogue import SPRITE_SPECS

SPRITE_DIRECTORY = Path(__file__).with_name("sprite_assets")
ROWS = {"segment": 0, "dot": 1, "tube": 2, "crystal": 3, "icon": 4, "spark": 5, "block": 6, "flame": 7}
SHAPES = frozenset(ROWS)
GEOMETRY = {"rectangular", "radial", "spiral", "mirrored", "horizontal"}
COLORS = {"original", "tint", "level"}
SPRITE_BLENDS = {"normal", "add", "screen", "multiply", "lighten", "difference"}
# The renderer validates every numeric field on construction; RudimentModulations
# can target these exact style names via an independent native 64-pip source.
# Immutable asset paths themselves are deliberately *not* modulatable.
SPRITE_NUMERIC = {
    "sprite_scale": (0.05, 8, 1),
    "sprite_scale_x": (0.05, 8, 1),
    "sprite_scale_y": (0.05, 8, 1),
    "sprite_rotation": (-3600, 3600, 0),
    "sprite_spin": (-90, 90, 0),
    "sprite_offset_x": (-1024, 1024, 0),
    "sprite_offset_y": (-1024, 1024, 0),
    "sprite_layout_rotation": (-360, 360, 0),
    "sprite_spread": (0, 4, 1),
    "sprite_anchor_x": (-1, 1, 0),
    "sprite_anchor_y": (-1, 1, 0),
    "sprite_shear_x": (-0.8, 0.8, 0),
    "sprite_shear_y": (-0.8, 0.8, 0),
    "sprite_jitter": (0, 48, 0),
    "sprite_angle_jitter": (0, 180, 0),
    "sprite_visible_fraction": (0, 1, 1),
    "sprite_palette_shift": (-64, 64, 0),
    "sprite_animation_rate": (0, 16, 1),
    "sprite_animation_phase": (-10000, 10000, 0),
    "sprite_hue_shift": (-3600, 3600, 0),
    "sprite_saturation": (0, 4, 1),
    "sprite_brightness": (0, 4, 1),
    "sprite_contrast": (0, 4, 1),
    "sprite_gamma": (0.1, 5, 1),
    "sprite_temperature": (-1, 1, 0),
    "sprite_red": (0, 4, 1),
    "sprite_green": (0, 4, 1),
    "sprite_blue": (0, 4, 1),
    "sprite_posterize": (0, 16, 0),
    "sprite_pixel_size": (1, 16, 1),
    "sprite_blur": (0, 16, 0),
    "sprite_glow": (0, 20, 0),
    "sprite_outline": (0, 10, 0),
    "sprite_shadow": (0, 20, 0),
    "sprite_shadow_x": (-64, 64, 2),
    "sprite_shadow_y": (-64, 64, 3),
    "sprite_shadow_opacity": (0, 1, 0.6),
    "sprite_scanlines": (0, 1, 0),
    "sprite_grain": (0, 1, 0),
    "sprite_sharpen": (0, 4, 0),
}
# Categorical sprite choices are selected by native Rudiment sample rather
# than attempting to interpolate an enum, colour string or PNG file path.
SPRITE_CHOICES = {
    "sprite_theme",
    "shape",
    "sprite_colour",
    "sprite_blend",
    "orientation",
    "pixelated",
    "mirror",
    "sprite_tint_colour",
    "sprite_outline_colour",
}


def _clip(x, lo=0, hi=1):
    return min(hi, max(lo, x))


@lru_cache(maxsize=1)
def _manifest():
    return json.loads((SPRITE_DIRECTORY / "manifest.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=24)
def builtin_atlas(theme):
    """Read a packaged, SHA-pinned atlas; return isolated RGBA pixels."""
    themes = _manifest()["themes"]
    if theme not in themes:
        raise ValueError(f"Unknown built-in sprite theme: {theme}")
    row = themes[theme]
    path = SPRITE_DIRECTORY / row["file"]
    if _sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        raise ValueError(f"Packaged sprite sheet hash mismatch: {row['file']}")
    with Image.open(path) as handle:
        image = handle.convert("RGBA")
    return image


class SpriteBank:
    """Immutable source pixels, cached per-size tiles and predictable stamps."""

    def __init__(self, image, columns=8, rows=8, *, pixelated=False):
        self.sheet = image.convert("RGBA")
        self.columns = integer(columns, "sprite atlas columns", minimum=1, maximum=32)
        self.rows = integer(rows, "sprite atlas rows", minimum=1, maximum=32)
        if self.sheet.width % self.columns or self.sheet.height % self.rows:
            raise ValueError("Sprite atlas dimensions must divide evenly into columns/rows")
        self.width = self.sheet.width // self.columns
        self.height = self.sheet.height // self.rows
        self.pixelated = pixelated
        self._cache = {}

    def tile(self, index, width, height, *, angle=0, alpha=1, tint=None, tint_strength=1, effects=None):
        index = integer(index, "sprite tile index", minimum=0, maximum=self.columns * self.rows - 1)
        width = integer(width, "sprite tile width", minimum=1, maximum=1024)
        height = integer(height, "sprite tile height", minimum=1, maximum=1024)
        angle = round(angle, 2)  # native modulation can animate sub-five-degree rotation
        alpha = round(_clip(alpha) * 32) / 32
        tint = tuple(tint) if tint else None
        tint_strength = round(_clip(tint_strength) * 32) / 32
        # Frame-local renderers cache by complete effective sprite appearance.
        # Default values are stripped so legacy output remains byte-identical.
        effects = effects or {}
        effect_key = tuple(
            sorted(
                (name, round(value, 3) if isinstance(value, float) else value)
                for name, value in effects.items()
            )
        )
        key = (index, width, height, angle, alpha, tint, tint_strength, effect_key)
        found = self._cache.get(key)
        if found is not None:
            return found
        box = (
            index % self.columns * self.width,
            index // self.columns * self.height,
            (index % self.columns + 1) * self.width,
            (index // self.columns + 1) * self.height,
        )
        tile = self.sheet.crop(box)
        if tint:
            # Preserve source alpha and image texture/luminance while shifting hue.
            luminosity = tile.convert("RGB").convert("L")
            pigment = Image.new("RGB", tile.size, tint[:3])
            tint_image = ImageChops.multiply(pigment, luminosity.convert("RGB"))
            tint_image.putalpha(tile.getchannel("A"))
            tile = Image.blend(tile, tint_image, tint_strength)
        # Grade the actual atlas cell, not the surrounding composited frame.
        if effects:
            tile = _sprite_grade(tile, effects)
        if tile.size != (width, height):
            tile = tile.resize(
                (width, height), Image.Resampling.NEAREST if self.pixelated else Image.Resampling.LANCZOS
            )
        if effects:
            tile = _sprite_transform(tile, effects, self.pixelated)
        if angle:
            tile = tile.rotate(
                -angle,
                resample=Image.Resampling.NEAREST if self.pixelated else Image.Resampling.BICUBIC,
                expand=True,
            )
        if effects:
            tile = _sprite_halo(tile, effects)
        if alpha < 1:
            channel = tile.getchannel("A").point(lambda value: round(value * alpha))
            tile.putalpha(channel)
        if len(self._cache) >= 768:
            self._cache.clear()
        self._cache[key] = tile
        return tile


def _sprite_grade(tile, cfg):
    """Per-cell HSV/channel balance, texture noise and CRT lines; alpha preserved."""
    hue = cfg.get("sprite_hue_shift", 0) / 360
    saturation = cfg.get("sprite_saturation", 1)
    brightness = cfg.get("sprite_brightness", 1)
    contrast = cfg.get("sprite_contrast", 1)
    gamma = cfg.get("sprite_gamma", 1)
    temperature = cfg.get("sprite_temperature", 0)
    red = cfg.get("sprite_red", 1) * (1 + max(0, temperature))
    green = cfg.get("sprite_green", 1)
    blue = cfg.get("sprite_blue", 1) * (1 + max(0, -temperature))
    posterize = round(cfg.get("sprite_posterize", 0))
    scanlines = cfg.get("sprite_scanlines", 0)
    grain = cfg.get("sprite_grain", 0)
    neutral = (
        not hue
        and saturation == brightness == contrast == gamma == red == green == blue == 1
        and not posterize
        and not scanlines
        and not grain
    )
    if neutral:
        return tile
    pixels = list(tile.getdata())
    result = []
    width = tile.width
    for i, (r, g, b, a) in enumerate(pixels):
        if not a:
            result.append((r, g, b, a))
            continue
        if hue or saturation != 1:
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            r, g, b = (round(v * 255) for v in colorsys.hsv_to_rgb((h + hue) % 1, _clip(s * saturation), v))
        values = []
        factor = 1 - (scanlines if i // width % 2 else 0)
        for channel, gain in zip((r, g, b), (red, green, blue)):
            value = _clip(((channel / 255 - 0.5) * contrast + 0.5) * brightness * gain)
            value = _clip(value ** (1 / gamma) * factor)
            if grain:
                jitter = ((i * 1103515245 + (len(result) + 12345) * 2654435761) % 997) / 996 - 0.5
                value = _clip(value + jitter * grain * 0.35)
            if posterize >= 2:
                value = round(value * (posterize - 1)) / (posterize - 1)
            values.append(round(255 * value))
        result.append((*values, a))
    output = Image.new("RGBA", tile.size)
    output.putdata(result)
    return output


def _sprite_transform(tile, cfg, pixelated):
    pixel_size = round(cfg.get("sprite_pixel_size", 1))
    if pixel_size > 1:
        tiny = tile.resize(
            (max(1, tile.width // pixel_size), max(1, tile.height // pixel_size)), Image.Resampling.NEAREST
        )
        tile = tiny.resize(tile.size, Image.Resampling.NEAREST)
    sx, sy = cfg.get("sprite_shear_x", 0), cfg.get("sprite_shear_y", 0)
    if sx or sy:
        width = max(1, round(tile.width + abs(sx) * tile.height))
        height = max(1, round(tile.height + abs(sy) * tile.width))
        det = 1 - sx * sy
        a, b, d, e = 1 / det, -sx / det, -sy / det, 1 / det
        c = tile.width / 2 - a * width / 2 - b * height / 2
        f = tile.height / 2 - d * width / 2 - e * height / 2
        tile = tile.transform(
            (width, height),
            Image.Transform.AFFINE,
            (a, b, c, d, e, f),
            resample=Image.Resampling.NEAREST if pixelated else Image.Resampling.BICUBIC,
        )
    sharpen = cfg.get("sprite_sharpen", 0)
    if sharpen:
        from PIL import ImageEnhance

        tile = ImageEnhance.Sharpness(tile).enhance(1 + sharpen)
    blur = cfg.get("sprite_blur", 0)
    if blur:
        tile = tile.filter(ImageFilter.GaussianBlur(blur))
    return tile


def _sprite_halo(tile, cfg):
    glow = cfg.get("sprite_glow", 0)
    outline = round(cfg.get("sprite_outline", 0))
    shadow = cfg.get("sprite_shadow", 0)
    if not glow and not outline and not shadow:
        return tile
    dx = round(cfg.get("sprite_shadow_x", 2))
    dy = round(cfg.get("sprite_shadow_y", 3))
    pad = min(80, int(math.ceil(max(glow * 2, outline + 2, shadow * 2 + abs(dx), shadow * 2 + abs(dy)))))
    canvas = Image.new("RGBA", (tile.width + 2 * pad, tile.height + 2 * pad))
    x, y = pad, pad
    if shadow:
        shadow_layer = Image.new("RGBA", canvas.size)
        alpha = tile.getchannel("A")
        shadow_colour = Image.new("RGBA", tile.size, (0, 0, 0, 0))
        shadow_colour.putalpha(alpha.point(lambda v: round(v * cfg.get("sprite_shadow_opacity", 0.6))))
        shadow_layer.alpha_composite(shadow_colour, (x + dx, y + dy))
        canvas.alpha_composite(shadow_layer.filter(ImageFilter.GaussianBlur(shadow)))
    if glow:
        glow_layer = Image.new("RGBA", canvas.size)
        glow_layer.alpha_composite(tile, (x, y))
        canvas.alpha_composite(glow_layer.filter(ImageFilter.GaussianBlur(glow)))
    if outline:
        alpha = tile.getchannel("A")
        expanded = alpha.filter(ImageFilter.MaxFilter(2 * outline + 1))
        border = ImageChops.subtract(expanded, alpha)
        colour = ImageColor.getcolor(cfg.get("sprite_outline_colour", "#ffffff"), "RGBA")
        border_ink = Image.new("RGBA", tile.size, colour)
        border_ink.putalpha(ImageChops.multiply(border_ink.getchannel("A"), border))
        canvas.alpha_composite(border_ink, (x, y))
    canvas.alpha_composite(tile, (x, y))
    return canvas


def blit(canvas, tile, cx, cy, *, blend="normal", anchor_x=0, anchor_y=0):
    """Clip sprite halo to viewport; never allow negative-destination exceptions."""
    x = round(cx - tile.width * (0.5 + anchor_x / 2))
    y = round(cy - tile.height * (0.5 + anchor_y / 2))
    left, top = max(0, x), max(0, y)
    right, bottom = min(canvas.width, x + tile.width), min(canvas.height, y + tile.height)
    if right <= left or bottom <= top:
        return
    image = tile.crop((left - x, top - y, right - x, bottom - y))
    if blend == "normal":
        canvas.alpha_composite(image, (left, top))
        return
    previous = canvas.crop((left, top, right, bottom))
    fg, bg = image.convert("RGB"), previous.convert("RGB")
    if blend == "add":
        mixed = ImageChops.add(bg, fg)
    elif blend == "screen":
        mixed = ImageChops.screen(bg, fg)
    elif blend == "multiply":
        mixed = ImageChops.multiply(bg, fg)
    elif blend == "lighten":
        mixed = ImageChops.lighter(bg, fg)
    else:
        mixed = ImageChops.difference(bg, fg)
    # On a transparent destination there is nothing to multiply/difference
    # with; fall back to the original glyph instead of turning it black.
    mixed = Image.composite(mixed, fg, previous.getchannel("A"))
    rgb = Image.composite(mixed, bg, image.getchannel("A"))
    merged = Image.merge(
        "RGBA", (*rgb.split(), ImageChops.screen(previous.getchannel("A"), image.getchannel("A")))
    )
    canvas.paste(merged, (left, top))


def _mode_default(mode):
    if mode.startswith("sprite-spectrum-lcd"):
        return "lcd", "segment"
    suffix = mode.rsplit("-", 1)[-1]
    if suffix in {"lcd", "blocks", "mosaic", "pixels", "retro", "city", "gauge"}:
        return "lcd", "segment"
    if suffix in {"led", "dots", "particles", "orbits", "cloud", "constellation", "sparks", "halo"}:
        return "led", "dot"
    if suffix in {"crystal", "ice"}:
        return "crystal" if suffix == "crystal" else "ice", "crystal"
    if suffix == "fire":
        return "fire", "flame"
    if suffix in {"icons", "petals", "icon-grid", "path", "trails", "xy", "orbit"}:
        return "generated-neon", "icon"
    if suffix in {"vfd", "neon", "ring", "spokes", "annular"}:
        return "vfd", "tube"
    if suffix in {"pixel", "heatmap"}:
        return "pixel", "block"
    if suffix in {"ribbon", "spiral", "radial"}:
        return "generated-neon", "spark"
    return "arcade", "block"


class SpriteRenderer(_Viewport):
    """Image-atlas visualiser for spectrum, spectrogram and native Rudiment time traces."""

    def __init__(self, size, style, mode):
        allowed = {
            "sprite_theme",
            "sprite_asset",
            "sprite_columns",
            "sprite_rows",
            "shape",
            "sprite_index",
            "sprite_size",
            "sprite_opacity",
            "sprite_colour",
            "tint_strength",
            "segment_count",
            "segment_gap",
            "off_opacity",
            "bar_gap",
            "orientation",
            "start_angle",
            "sweep_angle",
            "inner_radius",
            "outer_radius",
            "history_limit",
            "bin_stride",
            "sample_stride",
            "spacing",
            "threshold",
            "floor_db",
            "ceiling_db",
            "animation_frames",
            "animation_stride",
            "pixelated",
            "axis",
            "mirror",
            "rings",
            "twists",
            "seed",
            "max_stamps",
            "sprite_blend",
            "sprite_tint_colour",
            "sprite_outline_colour",
            *SPRITE_NUMERIC,
        }
        super().__init__(size, style, allowed)
        for key, (lower, upper, default) in SPRITE_NUMERIC.items():
            setattr(self, key, number(style.get(key, default), key, minimum=lower, maximum=upper))
        self.sprite_posterize = integer(round(self.sprite_posterize), "sprite posterize", maximum=16)
        self.sprite_pixel_size = integer(
            round(self.sprite_pixel_size), "sprite pixel size", minimum=1, maximum=16
        )
        self.sprite_outline = integer(round(self.sprite_outline), "sprite outline", maximum=10)
        self.sprite_palette_shift = round(self.sprite_palette_shift)
        self.sprite_blend = style.get("sprite_blend", "normal")
        if self.sprite_blend not in SPRITE_BLENDS:
            raise ValueError("Unknown sprite blend mode")
        self.sprite_tint_colour = style.get("sprite_tint_colour")
        self.sprite_outline_colour = style.get("sprite_outline_colour", "#ffffff")
        for name, colour in (
            ("sprite_tint_colour", self.sprite_tint_colour),
            ("sprite_outline_colour", self.sprite_outline_colour),
        ):
            if colour is not None:
                if not isinstance(colour, str):
                    raise ValueError(f"{name} must be a CSS colour string")
                ImageColor.getcolor(colour, "RGBA")
        self.mode = mode
        self.family = (
            "spectrogram"
            if mode.startswith("sprite-spectrogram-")
            else ("spectrum" if mode.startswith("sprite-spectrum-") else "motion")
        )
        self.requires_spectrum = self.family != "motion"
        default_theme, default_shape = _mode_default(mode)
        self.theme = style.get("sprite_theme", default_theme)
        if not isinstance(self.theme, str):
            raise ValueError("sprite_theme must be a theme name")
        self.shape_override = "shape" in style
        self.shape = style.get("shape", default_shape)
        if self.shape not in SHAPES:
            raise ValueError("Unknown sprite shape")
        self.index_override = "sprite_index" in style
        self.sprite_index = integer(
            style.get("sprite_index", 0 if style.get("sprite_asset") else ROWS[self.shape] * 8),
            "sprite index",
            minimum=0,
            maximum=1023,
        )
        self.sprite_size = integer(style.get("sprite_size", 12), "sprite size", minimum=2, maximum=256)
        self.segment_count = integer(style.get("segment_count", 12), "LCD segments", minimum=2, maximum=64)
        self.segment_gap = number(style.get("segment_gap", 0.18), "LCD segment gap", minimum=0, maximum=0.8)
        self.off_opacity = number(style.get("off_opacity", 0.10), "LCD unlit opacity", minimum=0, maximum=1)
        self.bar_gap = number(style.get("bar_gap", 0.16), "sprite bar gap", minimum=0, maximum=0.9)
        self.sprite_opacity = number(style.get("sprite_opacity", 1), "sprite opacity", minimum=0, maximum=1)
        self.colour_mode = style.get("sprite_colour", "original")
        if self.colour_mode not in COLORS:
            raise ValueError("sprite_colour must be original, tint or level")
        self.tint_strength = number(style.get("tint_strength", 1), "tint strength", minimum=0, maximum=1)
        self.orientation = style.get("orientation", "vertical")
        if self.orientation not in {"vertical", "horizontal"}:
            raise ValueError("sprite orientation must be vertical or horizontal")
        self.start_angle = number(style.get("start_angle", -90), "start angle", minimum=-3600, maximum=3600)
        self.sweep_angle = number(style.get("sweep_angle", 360), "sweep angle", minimum=5, maximum=360)
        self.inner_radius = number(style.get("inner_radius", 0.28), "inner radius", minimum=0, maximum=0.9)
        self.outer_radius = number(style.get("outer_radius", 0.93), "outer radius", minimum=0.1, maximum=1)
        if self.inner_radius >= self.outer_radius:
            raise ValueError("Sprite inner_radius must be less than outer_radius")
        self.history_limit = integer(style.get("history_limit", 64), "sprite history", minimum=2, maximum=512)
        self.bin_stride = integer(style.get("bin_stride", 1), "sprite bin stride", minimum=1, maximum=64)
        self.sample_stride = integer(
            style.get("sample_stride", 1), "sprite sample stride", minimum=1, maximum=32
        )
        self.spacing = integer(style.get("spacing", 5), "sprite stamp spacing", minimum=1, maximum=64)
        self.threshold = number(
            style.get("threshold", 0.035), "sprite visibility threshold", minimum=0, maximum=1
        )
        self.floor_db = number(style.get("floor_db", -72), "sprite dB floor", minimum=-240, maximum=0)
        self.ceiling_db = number(
            style.get("ceiling_db", 0), "sprite dB ceiling", minimum=self.floor_db + 0.01, maximum=60
        )
        self.animation_frames = integer(
            style.get("animation_frames", 1), "sprite animation frames", minimum=1, maximum=8
        )
        self.animation_stride = integer(
            style.get("animation_stride", 6), "sprite animation stride", minimum=1, maximum=240
        )
        self.axis = integer(style.get("axis", 0), "sprite motion axis", minimum=0, maximum=255)
        self.rings = integer(style.get("rings", 3), "sprite rings", minimum=1, maximum=16)
        self.twists = number(style.get("twists", 2), "sprite spiral twists", minimum=0.1, maximum=20)
        self.seed = integer(style.get("seed", 0), "sprite seed", minimum=0, maximum=2**32 - 1)
        self.max_stamps = integer(
            style.get("max_stamps", 8000), "maximum sprite stamps", minimum=1, maximum=25000
        )
        self.mirror = style.get("mirror", False)
        if not isinstance(self.mirror, bool):
            raise ValueError("sprite mirror must be boolean")
        self.pixelated = style.get("pixelated", self.theme in {"arcade", "pixel"})
        if not isinstance(self.pixelated, bool):
            raise ValueError("pixelated must be boolean")
        self.asset = style.get("sprite_asset")
        self.asset_columns = integer(style.get("sprite_columns", 8), "sprite columns", minimum=1, maximum=32)
        self.asset_rows = integer(style.get("sprite_rows", 8), "sprite rows", minimum=1, maximum=32)
        if self.asset is not None and not isinstance(self.asset, dict):
            raise ValueError("sprite_asset must be a pinned {path, sha256} file reference")
        self.bank = None
        self.frame_report = None
        self._stamps = 0
        if self.asset is None:
            # Default assets can be used by gallery/tests without a full project.
            self.bank = SpriteBank(builtin_atlas(self.theme), pixelated=self.pixelated)
        if self.sprite_index >= (self.asset_columns * self.asset_rows if self.asset else 64):
            raise ValueError("sprite_index falls outside selected atlas")
        self.area(1)

    def prepare(self, project):
        if self.asset is not None:
            # Fully hashed, source-bound user artwork. No arbitrary path loading.
            ref = project.ref(self.asset)
            if ref not in project.inputs:
                project.inputs.append(ref)
            with Image.open(ref["path"]) as image:
                self.bank = SpriteBank(image, self.asset_columns, self.asset_rows, pixelated=self.pixelated)
        else:
            meta = _manifest()["themes"][self.theme]
            ref = {"path": str(SPRITE_DIRECTORY / meta["file"]), "sha256": meta["sha256"]}
            if ref not in project.inputs:
                project.inputs.append(ref)

    def _stamp(
        self,
        canvas,
        cx,
        cy,
        *,
        row=None,
        column=0,
        size=None,
        width=None,
        height=None,
        opacity=1,
        angle=0,
        level=1,
        frame=0,
    ):
        self._stamps += 1
        if self._stamps > self.max_stamps:
            raise ValueError("Sprite renderer stamp limit exceeded; reduce history/bins/segments")
        if self.bank is None:
            raise ValueError("Custom sprite atlas needs prepare(project) before rendering")
        if self.sprite_visible_fraction < 1:
            visible = ((self._stamps * 2246822519 + self.seed * 3266489917) % 10007) / 10007
            if visible >= self.sprite_visible_fraction:
                return
        column += self.sprite_palette_shift
        index = self.sprite_index
        position = math.floor(
            (frame * self.sprite_animation_rate + self.sprite_animation_phase) / self.animation_stride
        )
        if self.asset is None:
            select_row = ROWS[self.shape] if self.shape_override or row is None else row
            # Image-generated sheet has semantically arranged rows but still 8x8.
            if self.index_override:
                index = (self.sprite_index + position % self.animation_frames) % 64
            else:
                index = select_row * 8 + (
                    (column + position) % min(self.animation_frames, 8)
                    if self.animation_frames > 1
                    else column % 8
                )
        else:
            index = (self.sprite_index + self.sprite_palette_shift + position % self.animation_frames) % (
                self.bank.columns * self.bank.rows
            )
        side = self.sprite_size if size is None else max(1, round(size))
        tint = None
        if self.colour_mode != "original":
            colour = self.colours[column % len(self.colours)]
            if self.colour_mode == "level":
                colour = self.colours[min(len(self.colours) - 1, int(_clip(level) * len(self.colours)))]
            tint = colour
        if self.sprite_tint_colour is not None:
            tint = ImageColor.getcolor(self.sprite_tint_colour, "RGBA")
        tw = max(1, round((width if width is not None else side) * self.sprite_scale * self.sprite_scale_x))
        th = max(1, round((height if height is not None else side) * self.sprite_scale * self.sprite_scale_y))
        if tw > 1024 or th > 1024:
            raise ValueError("Effective sprite tile exceeds the 1024-pixel safety limit")
        effects = {
            key: getattr(self, key)
            for key, (_, _, default) in SPRITE_NUMERIC.items()
            if key
            not in {
                "sprite_scale",
                "sprite_scale_x",
                "sprite_scale_y",
                "sprite_rotation",
                "sprite_spin",
                "sprite_offset_x",
                "sprite_offset_y",
                "sprite_layout_rotation",
                "sprite_spread",
                "sprite_anchor_x",
                "sprite_anchor_y",
                "sprite_jitter",
                "sprite_angle_jitter",
                "sprite_visible_fraction",
                "sprite_palette_shift",
                "sprite_animation_rate",
                "sprite_animation_phase",
            }
            and getattr(self, key) != default
        }
        if self.sprite_outline:
            effects["sprite_outline_colour"] = self.sprite_outline_colour
        slot = self._stamps
        if self.sprite_jitter:
            cx += self.sprite_jitter * math.sin(slot * 16.17 + frame * 0.31 + self.seed)
            cy += self.sprite_jitter * math.cos(slot * 11.83 + frame * 0.17 + self.seed)
        if self.sprite_spread != 1 or self.sprite_layout_rotation:
            dx, dy = (
                (cx - canvas.width / 2) * self.sprite_spread,
                (cy - canvas.height / 2) * self.sprite_spread,
            )
            theta = math.radians(self.sprite_layout_rotation)
            cx = canvas.width / 2 + dx * math.cos(theta) - dy * math.sin(theta)
            cy = canvas.height / 2 + dx * math.sin(theta) + dy * math.cos(theta)
        cx += self.sprite_offset_x
        cy += self.sprite_offset_y
        glyph_angle = (
            angle
            + self.sprite_rotation
            + frame * self.sprite_spin
            + self.sprite_angle_jitter * math.sin(slot * 13.03 + self.seed)
        )
        tile = self.bank.tile(
            index,
            tw,
            th,
            angle=glyph_angle,
            alpha=opacity * self.sprite_opacity,
            tint=tint,
            tint_strength=self.tint_strength,
            effects=effects,
        )
        blit(
            canvas,
            tile,
            cx,
            cy,
            blend=self.sprite_blend,
            anchor_x=self.sprite_anchor_x,
            anchor_y=self.sprite_anchor_y,
        )

    def _bounds(self):
        return self.area(1)

    def _polar(self, angle, radius):
        x, y = self.size[0] / 2, self.size[1] / 2
        theta = math.radians(angle)
        return x + math.cos(theta) * radius, y + math.sin(theta) * radius

    def _radii(self):
        radius = min(self.size) / 2 - self.padding - self.sprite_size / 2 - 2
        return radius * self.inner_radius, radius * self.outer_radius

    def _levels(self, frame):
        report = analyse_spectrum(frame, floor=self.floor_db, ceiling=self.ceiling_db)
        self.frame_report = report
        return [
            _clip((band["display_dbfs"] - self.floor_db) / (self.ceiling_db - self.floor_db))
            for band in report["bands"]
        ]

    def _sample_levels(self, frame, sample):
        rows = []
        for band, value in zip(frame.spectrum_bands, sample.values):
            rms = band.rms(value)
            db = 20 * math.log10(rms) if rms > 0 else self.floor_db
            rows.append(_clip((db - self.floor_db) / (self.ceiling_db - self.floor_db)))
        return rows

    def _spectrum(self, canvas, frame):
        vals = self._levels(frame)
        if not vals:
            raise ValueError("Sprite spectrum needs calibrated Fourier bands")
        family = self.mode.removeprefix("sprite-spectrum-")
        left, top, right, bottom = self._bounds()
        bands = list(enumerate(vals))[:: self.bin_stride]
        n = len(bands)
        if n == 0:
            return
        if family == "gauge":
            aggregate = sum(vals) / len(vals)
            dominant = max(range(len(vals)), key=lambda j: vals[j])
            r0, r1 = self._radii()
            count = max(12, n)
            for i in range(count):
                power = i / max(1, count - 1)
                self._stamp(
                    canvas,
                    *self._polar(self.start_angle + power * self.sweep_angle, (r0 + r1) / 2),
                    row=0,
                    size=self.sprite_size * 1.1,
                    opacity=1 if power <= aggregate else self.off_opacity,
                    column=i % 8,
                    frame=frame.context.frame,
                    angle=self.start_angle + power * self.sweep_angle,
                )
            self._stamp(
                canvas,
                *self._polar(self.start_angle + self.sweep_angle * dominant / max(1, len(vals) - 1), r1),
                row=5,
                column=dominant % 8,
                size=self.sprite_size * 1.8,
                frame=frame.context.frame,
            )
            return
        radial = family in {"led-radial", "petals", "ring", "halo", "spokes", "orbits"}
        if radial:
            r0, r1 = self._radii()
            for j, (i, level) in enumerate(bands):
                angle = self.start_angle + j * self.sweep_angle / max(1, n)
                if family in {"petals", "ring", "halo", "orbits"}:
                    r = r0 + (r1 - r0) * level
                    if family == "ring":
                        r = r0 + (r1 - r0) * level / 2
                        for ring in range(self.rings):
                            band_radius = r0 + (r - r0) * (ring + 1) / self.rings
                            self._stamp(
                                canvas,
                                *self._polar(angle, band_radius),
                                row=1,
                                column=(i + ring) % 8,
                                size=self.sprite_size,
                                opacity=0.25 + 0.75 * level,
                                frame=frame.context.frame,
                            )
                        continue
                    if family == "halo":
                        self._stamp(
                            canvas,
                            *self._polar(angle, r),
                            row=5,
                            column=i % 8,
                            size=self.sprite_size * 2,
                            opacity=0.35 + 0.6 * level,
                            frame=frame.context.frame,
                        )
                    else:
                        self._stamp(
                            canvas,
                            *self._polar(angle, r),
                            row=4 if family == "petals" else 1,
                            column=i % 8,
                            size=self.sprite_size * (1.0 + 0.75 * level),
                            opacity=0.35 + 0.6 * level,
                            angle=angle,
                            frame=frame.context.frame,
                        )
                else:
                    for s in range(self.segment_count):
                        v = (s + 0.5) / self.segment_count
                        if family == "spokes" and s % 2:
                            continue
                        radius = r0 + (r1 - r0) * v
                        self._stamp(
                            canvas,
                            *self._polar(angle, radius),
                            row=0 if family == "spokes" else 1,
                            column=i % 8,
                            size=self.sprite_size,
                            angle=angle + 90,
                            opacity=1 if v <= level else self.off_opacity,
                            frame=frame.context.frame,
                            level=level,
                        )
            return
        horizontal = family == "lcd-horizontal" or self.orientation == "horizontal"
        mirror = family == "lcd-mirrored" or self.mirror
        if horizontal:
            slot = (bottom - top) / n
            for j, (i, level) in enumerate(bands):
                y = top + (j + 0.5) * slot
                for s in range(self.segment_count):
                    progress = (s + 0.5) / self.segment_count
                    x = left + progress * (right - left)
                    alpha = 1 if progress <= level else self.off_opacity
                    self._stamp(
                        canvas,
                        x,
                        y,
                        column=i % 8,
                        row=0 if "lcd" in family else None,
                        size=min(self.sprite_size, slot * 1.5),
                        width=(right - left) / self.segment_count * (1 - self.segment_gap),
                        height=slot * (1 - self.bar_gap),
                        opacity=alpha,
                        frame=frame.context.frame,
                        level=level,
                    )
            return
        slot = (right - left) / n
        for j, (i, level) in enumerate(bands):
            x = left + (j + 0.5) * slot
            for s in range(self.segment_count):
                progress = (s + 0.5) / self.segment_count
                alpha = 1 if progress <= level else self.off_opacity
                if alpha <= 0:
                    continue
                y = bottom - progress * (bottom - top)
                if mirror:
                    y = (top + bottom) / 2 - progress * (bottom - top) / 2
                row = (
                    0
                    if "lcd" in family
                    else 1
                    if "led" in family
                    else 2
                    if family in {"vfd", "neon"}
                    else 3
                    if family in {"crystal", "ice"}
                    else 7
                    if family == "fire"
                    else 6
                )
                if family == "icons":
                    row = 4
                self._stamp(
                    canvas,
                    x,
                    y,
                    row=row,
                    column=i % 8,
                    size=min(self.sprite_size, slot * (1 - self.bar_gap)),
                    width=slot * (1 - self.bar_gap),
                    height=(bottom - top) / self.segment_count * (1 - self.segment_gap),
                    opacity=alpha,
                    frame=frame.context.frame,
                    level=level,
                )
                if mirror:
                    self._stamp(
                        canvas,
                        x,
                        top + bottom - y,
                        row=row,
                        column=i % 8,
                        size=min(self.sprite_size, slot * (1 - self.bar_gap)),
                        width=slot * (1 - self.bar_gap),
                        height=(bottom - top) / self.segment_count * (1 - self.segment_gap),
                        opacity=alpha,
                        frame=frame.context.frame,
                        level=level,
                    )

    def _spectrogram(self, canvas, frame):
        self._levels(frame)  # keeps spectrum analysis sidecar intact
        history = tuple(frame.history)[-self.history_limit :: self.sample_stride]
        if not history:
            raise ValueError("Sprite spectrogram needs observed sample history")
        mode = self.mode.removeprefix("sprite-spectrogram-")
        left, top, right, bottom = self._bounds()
        rows = [self._sample_levels(frame, s)[:: self.bin_stride] for s in history]
        n, bands = len(rows), len(rows[0])
        if not bands:
            raise ValueError("Sprite spectrogram needs calibrated Fourier bands")
        size = min(self.sprite_size, (right - left) / max(1, n) * 1.5, (bottom - top) / max(1, bands) * 1.5)
        if mode in {"radial", "spiral"}:
            r0, r1 = self._radii()
            for j, values in enumerate(rows):
                angle = self.start_angle + j * self.sweep_angle / max(1, n - 1)
                for i, level in enumerate(values):
                    if level < self.threshold:
                        continue
                    radius = r0 + (r1 - r0) * i / max(1, bands - 1)
                    if mode == "spiral":
                        radius = r0 + (r1 - r0) * (0.55 * j / max(1, n - 1) + 0.45 * i / max(1, bands - 1))
                        angle += self.twists * 360 / max(1, n)
                    self._stamp(
                        canvas,
                        *self._polar(angle, radius),
                        row=1 if mode == "radial" else 3,
                        column=i % 8,
                        level=level,
                        size=max(2, self.sprite_size * level),
                        opacity=max(0.12, level),
                        frame=frame.context.frame,
                    )
            return
        for j, values in enumerate(rows):
            x = left + (j + 0.5) * (right - left) / n
            for i, level in enumerate(values):
                if level < self.threshold:
                    continue
                y = bottom - (i + 0.5) * (bottom - top) / bands
                if mode == "rain":
                    y += (1 - level) * self.sprite_size
                row = {
                    "lcd": 0,
                    "led": 1,
                    "pixels": 6,
                    "heatmap": 6,
                    "icons": 4,
                    "neon": 2,
                    "crystal": 3,
                    "fire": 7,
                    "rain": 5,
                    "blocks": 6,
                }.get(mode, 6)
                self._stamp(
                    canvas,
                    x,
                    y,
                    row=row,
                    column=i % 8,
                    level=level,
                    size=max(1, size * (0.5 + level)),
                    opacity=max(0.1, level),
                    frame=frame.context.frame,
                )

    def _history(self, frame):
        history = frame.history[-self.history_limit :: self.sample_stride]
        if self.spacing != 5 and len(history) > 1:
            step = max(1, round(self.spacing / 5))
            reduced = history[::step]
            history = (*reduced, history[-1]) if reduced[-1] is not history[-1] else reduced
        if not history:
            raise ValueError("Sprite waveform/motion needs observed history")
        if self.axis >= len(frame.values):
            raise ValueError("Selected sprite waveform axis does not exist")
        return history

    def _waveform(self, canvas, frame):
        history = self._history(frame)
        mode = self.mode.removeprefix("sprite-waveform-")
        left, top, right, bottom = self._bounds()
        n = len(history)
        r0, r1 = self._radii()
        for j, sample in enumerate(history):
            value = sample.values[self.axis]
            if mode in {"annular", "spiral"}:
                angle = self.start_angle + self.sweep_angle * j / max(1, n - 1)
                radius = r0 + (r1 - r0) * (0.5 + value * 0.5)
                if mode == "spiral":
                    radius = r0 + (r1 - r0) * (0.65 * j / max(1, n - 1) + 0.3 * (0.5 + 0.5 * value))
                    angle = self.start_angle + self.twists * 360 * j / max(1, n - 1)
                x, y = self._polar(angle, radius)
            else:
                x = left + j * (right - left) / max(1, n - 1)
                y = top + (1 - value) * (bottom - top) / 2
            if mode == "bars":
                middle = (top + bottom) / 2
                length = abs(y - middle)
                step = max(self.sprite_size * 0.75, 2)
                for k in range(int(length / step) + 1):
                    yy = middle + (1 if y > middle else -1) * k * step
                    self._stamp(
                        canvas,
                        x,
                        yy,
                        row=0,
                        column=j % 8,
                        size=self.sprite_size * 0.8,
                        opacity=0.4 + 0.5 * abs(value),
                        frame=frame.context.frame,
                    )
            else:
                row = {
                    "lcd": 0,
                    "led": 1,
                    "dots": 1,
                    "icons": 4,
                    "neon": 2,
                    "ribbon": 6,
                    "mirrored": 1,
                    "annular": 3,
                    "spiral": 5,
                    "sparks": 5,
                    "crystal": 3,
                }.get(mode, 1)
                size = self.sprite_size * (1.5 if mode in {"ribbon", "crystal"} else 1)
                self._stamp(
                    canvas,
                    x,
                    y,
                    row=row,
                    column=j % 8,
                    level=abs(value),
                    size=size,
                    opacity=0.5 + 0.5 * abs(value),
                    frame=frame.context.frame,
                )
                if mode == "mirrored" or self.mirror:
                    self._stamp(
                        canvas,
                        x,
                        top + bottom - y,
                        row=row,
                        column=j % 8,
                        level=abs(value),
                        size=size,
                        opacity=0.5 + 0.5 * abs(value),
                        frame=frame.context.frame,
                    )

    def _motion(self, canvas, frame):
        history = self._history(frame)
        mode = self.mode.removeprefix("sprite-motion-")
        left, top, right, bottom = self._bounds()
        n = len(history)
        for j, sample in enumerate(history):
            xvalue = sample.values[0]
            yvalue = sample.values[1] if len(sample.values) > 1 else 0
            x = left + (xvalue + 1) * (right - left) / 2
            y = top + (1 - yvalue) * (bottom - top) / 2
            if mode == "icon-grid":
                x = left + (j % 12 + 0.5) * (right - left) / 12
                y = top + (j // 12 + 0.5) * (bottom - top) / max(1, math.ceil(n / 12))
            elif mode == "orbit":
                angle = self.start_angle + 360 * j / max(1, n - 1)
                radius = min(right - left, bottom - top) * (0.20 + 0.25 * (1 + yvalue) / 2)
                x, y = self._polar(angle, radius)
            elif mode in {"cloud", "particles", "constellation"}:
                x += math.sin(j * 19.19 + self.seed) * self.sprite_size * 1.5
                y += math.cos(j * 10.17 + self.seed) * self.sprite_size * 1.5
            alpha = (j + 1) / n if mode == "trails" else 0.4 + 0.6 * (j + 1) / n
            if mode == "cloud" and j % 2:
                alpha *= 0.6
            row = 4 if mode in {"path", "orbit", "icon-grid"} else 5 if mode == "constellation" else 1
            self._stamp(
                canvas,
                x,
                y,
                row=row,
                column=j % 8,
                level=abs(xvalue),
                size=self.sprite_size,
                opacity=alpha,
                frame=frame.context.frame,
            )

    def render(self, frame):
        self._stamps = 0
        self.frame_report = None
        canvas = self.image()
        if self.family == "spectrum":
            self._spectrum(canvas, frame)
        elif self.family == "spectrogram":
            self._spectrogram(canvas, frame)
        elif self.mode.startswith("sprite-waveform-"):
            self._waveform(canvas, frame)
        else:
            self._motion(canvas, frame)
        return canvas


def register_sprite_renderers(registry):
    """Adds sprite alternatives; never aliases or overwrites existing vector IDs."""
    for name, _family, _description, _origin in SPRITE_SPECS:

        def factory(size, style, selected=name):
            return SpriteRenderer(size, style, selected)

        registry.register(name, factory)
    return registry


class SpriteModulations:
    """Native-sampled numeric and categorical sprite controls, no DSP duplication.

    Renderer inputs, including *every* selectable built-in theme, are pinned at
    project preparation. An encoded frame always gives the same selection,
    regardless of the order in which other frames were rendered.
    """

    def __init__(self, project, configs, context_at, *, base_style, base_visual):
        from .modulation import RudimentModulations
        from .scoped_effects import ChoiceDriver

        if not isinstance(configs, list) or len(configs) > 32:
            raise ValueError("Sprite modulations must contain at most 32 drivers")
        if any(not isinstance(row, dict) for row in configs):
            raise ValueError("Each sprite modulator must be an object")
        keys = [row.get("id") or row.get("target") for row in configs]
        targets = [row.get("target") for row in configs]
        if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("Sprite modulation ids/targets must be nonempty and unique")
        if len(targets) != len(set(targets)):
            raise ValueError("Sprite parameter targets must be unique within a layer")
        numeric = [row for row in configs if "choices" not in row]
        self.numeric = RudimentModulations(project, numeric, context_at)
        self.categorical = []
        self.files = list(self.numeric.files)
        self.evidence = list(self.numeric.evidence)
        for row in configs:
            if "choices" not in row:
                continue
            if "combine" in row or "weight" in row or "range" in row:
                raise ValueError("Categorical sprite drivers use choices, not range/combine/weight")
            target = row.get("target")
            choices = row["choices"]
            if not isinstance(choices, list) or not 2 <= len(choices) <= 32:
                raise ValueError("Sprite choice modulation needs 2..32 alternatives")
            if target in {
                "visual.palette",
                "visual.gradient",
                *(
                    f"visual.gradient.{field}"
                    for field in (
                        "mode",
                        "stops",
                        "space",
                        "wrap",
                        "reverse",
                        "blend",
                        "bands",
                        "phase",
                        "mix",
                        "cycles",
                    )
                ),
            }:
                driver = ChoiceDriver(project, row, context_at, base_visual)
                self.categorical.append((target, choices, driver, "visual", row.get("id") or target))
            elif isinstance(target, str) and target.startswith("style.") and target[6:] in SPRITE_CHOICES:
                name = target[6:]
                for value in choices:
                    if name == "sprite_theme":
                        if (
                            not isinstance(value, str)
                            or value not in _manifest()["themes"]
                            or base_style.get("sprite_asset")
                        ):
                            raise ValueError(
                                "Sprite theme choices require bundled themes and no custom sprite_asset"
                            )
                        metadata = _manifest()["themes"][value]
                        ref = {"path": str(SPRITE_DIRECTORY / metadata["file"]), "sha256": metadata["sha256"]}
                        if ref not in self.files:
                            self.files.append(ref)
                    elif name == "shape" and value not in SHAPES:
                        raise ValueError("Unknown categorical sprite shape")
                    elif name == "sprite_colour" and value not in COLORS:
                        raise ValueError("Unknown categorical sprite colour mode")
                    elif name == "sprite_blend" and value not in SPRITE_BLENDS:
                        raise ValueError("Unknown categorical sprite blend mode")
                    elif name == "orientation" and value not in {"vertical", "horizontal"}:
                        raise ValueError("Unknown categorical sprite orientation")
                    elif name in {"pixelated", "mirror"} and not isinstance(value, bool):
                        raise ValueError("Categorical sprite switches require boolean choices")
                    elif name in {"sprite_tint_colour", "sprite_outline_colour"}:
                        if not isinstance(value, str):
                            raise ValueError("Sprite colour choice must be a CSS colour string")
                        ImageColor.getcolor(value, "RGBA")
                proxy = {key: val for key, val in row.items() if key not in {"target", "choices"}}
                proxy.update(target="style.sprite_size", range=[0, len(choices) - 1], combine="set")
                driver = RudimentModulations(project, [proxy], context_at)
                self.categorical.append((target, choices, driver, "style", row.get("id") or target))
            else:
                raise ValueError("Unsupported categorical sprite target; select an approved style or palette")
            self.files.extend(ref for ref in driver.files if ref not in self.files)
            self.evidence.append(
                dict(
                    id=row.get("id"),
                    target=target,
                    choices=choices,
                    native=driver.evidence,
                    policy="native sampled nearest choice",
                )
            )

    def apply(self, context, base_style, base_visual):
        from .scoped_effects import _set_choice
        from .visual_style import palette

        style, visual, reports = self.numeric.apply(context, base_style, base_visual)
        for target, choices, driver, scope, driver_id in self.categorical:
            if scope == "style":
                selection, _, result = driver.apply(context, {}, {})
                if not result:
                    continue
                index = max(0, min(len(choices) - 1, round(selection["sprite_size"])))
                value = choices[index]
                style[target[6:]] = value
                evidence = next(iter(result.values()))
                record = dict(
                    target=target, selected_index=index, value=value, source_value=evidence["source_value"]
                )
            else:
                picked = driver.choose(context)
                if picked is None:
                    continue
                value, evidence = picked
                visual = _set_choice(visual, target, value)
                record = dict(evidence, value=value)
                if target == "visual.palette":
                    style["colours"] = [
                        "#{:02x}{:02x}{:02x}{:02x}".format(*colour) for colour in palette(value)
                    ]
                    if "sprite_colour" not in base_style and "sprite_colour" not in style:
                        style["sprite_colour"] = "tint"
            reports[driver_id] = record
        return style, visual, reports
