"""Opt-in edge-aware AA, premultiplied directional blur and linear-light shutter blur.

This module implements presentation effects, never an audio/STFT analysis.
Shutter blur uses *observed encoded frames* from the current source cycle;
no optical flow, speculative subframe motion or future pixels are invented.
"""
from copy import deepcopy
from functools import lru_cache
import math

from PIL import Image, ImageChops, ImageFilter, ImageOps

from .io import fields, integer, number


QUALITY_RANGES = {
    "antialias": (0, 1, 0),
    "antialias_radius": (.25, 3, .75),
    "antialias_threshold": (0, .95, .08),
    "antialias_passes": (1, 3, 1),
    "motion_blur": (0, 1, 0),
    "motion_blur_distance": (0, 96, 0),
    "motion_blur_angle": (-360, 360, 0),
    "motion_blur_samples": (2, 24, 8),
    "motion_blur_trail": (0, 1, 1),
    "motion_blur_falloff": (0, 8, 1),
}


def edge_aware_antialias(image, *, strength, radius=.75, threshold=.08, passes=1):
    """Smooth only high-contrast image/alpha boundaries in premultiplied RGBA.

    It is a final-raster AA filter; it cannot reconstruct vector subpixels that
    were never rendered. Pixel art should normally keep ``antialias: 0``.
    """
    if not strength or image.width < 2 or image.height < 2:
        return image
    premult = image.convert("RGBa")
    for _ in range(round(passes)):
        # Alpha boundaries are as important as RGB edges for isolated sprites.
        rgba = premult.convert("RGBA")
        luminance = ImageOps.grayscale(rgba.convert("RGB"))
        alpha = rgba.getchannel("A")
        edge = ImageChops.lighter(luminance.filter(ImageFilter.FIND_EDGES),
                                 alpha.filter(ImageFilter.FIND_EDGES))
        edge = edge.filter(ImageFilter.GaussianBlur(.45))
        cutoff = round(threshold * 255)
        mask = edge.point(lambda value: round(max(0, value - cutoff) * strength * 255 / max(1, 255 - cutoff)))
        softened = premult.filter(ImageFilter.GaussianBlur(radius))
        premult = Image.composite(softened, premult, mask)
    return premult.convert("RGBA")


def directional_motion_blur(image, *, strength, distance, angle=0, samples=8, trail=1, falloff=1):
    """Subpixel resampled directional shutter approximation, alpha-safe.

    This deliberately does not claim to know where an object moved in time;
    the separate temporal shutter effect samples real encoded frames.
    """
    if not strength or not distance:
        return image
    source = image.convert("RGBa")
    count = round(samples)
    theta = math.radians(angle)
    dx, dy = math.cos(theta) * distance, math.sin(theta) * distance
    average = None
    total = 0.0
    for i in range(count):
        # trail=0 centres the shutter; trail=1 puts it behind the sharp image.
        fraction = i / max(1, count - 1) - (1 - trail) / 2
        shift_x, shift_y = -dx * fraction, -dy * fraction
        sample = source.transform(image.size, Image.Transform.AFFINE,
                                  (1, 0, -shift_x, 0, 1, -shift_y),
                                  resample=Image.Resampling.BICUBIC,
                                  fillcolor=(0, 0, 0, 0))
        weight = math.exp(-falloff * abs(fraction))
        total += weight
        average = sample if average is None else Image.blend(average, sample, weight / total)
    return Image.blend(source, average, strength).convert("RGBA")


def apply_quality_effects(image, settings):
    """Shared VisualStyle tail, available at Rudiment, element and frame scopes."""
    if settings.antialias:
        image = edge_aware_antialias(image, strength=settings.antialias,
            radius=settings.antialias_radius, threshold=settings.antialias_threshold,
            passes=settings.antialias_passes)
    if settings.motion_blur and settings.motion_blur_distance:
        image = directional_motion_blur(image, strength=settings.motion_blur,
            distance=settings.motion_blur_distance, angle=settings.motion_blur_angle,
            samples=settings.motion_blur_samples, trail=settings.motion_blur_trail,
            falloff=settings.motion_blur_falloff)
    return image


@lru_cache(maxsize=1)
def _colour_tables():
    linear = tuple(round(65535 * (((v / 255 + .055) / 1.055) ** 2.4 if v / 255 > .04045
                                    else v / 255 / 12.92)) for v in range(256))
    srgb = tuple(round(255 * (12.92 * (v / 65535) if v / 65535 <= .0031308
                              else 1.055 * (v / 65535) ** (1 / 2.4) - .055)) for v in range(65536))
    return linear, srgb


def linear_shutter_composite(images, weights, strength):
    """Weighted linear-light RGB integration with deterministic 16-bit LUTs.

    An optional NumPy path accelerates the *same integer computation*; the
    pure-Python fallback produces identical pixels without new dependencies.
    """
    if not images:
        raise ValueError("Temporal blur requires an observed frame")
    first = images[-1]
    if strength <= 0 or len(images) == 1:
        return first.copy()
    if any(im.mode != "RGB" or im.size != first.size for im in images):
        raise ValueError("Temporal motion blur frames must have equal RGB dimensions")
    if len(weights) != len(images) or any(not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError("Invalid temporal shutter weights")
    if not sum(weights):
        return first.copy()
    # Blend the unfiltered current sample with the weighted shutter exposure.
    total = sum(weights)
    effective = [strength * weight / total for weight in weights]
    effective[-1] += 1 - strength
    scale = 1 << 20
    fixed = [round(w * scale) for w in effective]
    fixed[-1] += scale - sum(fixed)
    to_linear, to_srgb = _colour_tables()
    sources = [im.tobytes() for im in images]
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        lookup = np.asarray(to_linear, dtype=np.int64)
        output = np.zeros(len(sources[0]), dtype=np.int64)
        for data, weight in zip(sources, fixed):
            if weight:
                output += lookup[np.frombuffer(data, dtype=np.uint8)] * weight
        indices = ((output + scale // 2) // scale).clip(0, 65535)
        encoded = np.asarray(to_srgb, dtype=np.uint8)[indices].tobytes()
    else:
        output = bytearray(len(sources[0]))
        for j in range(len(output)):
            value = (sum(to_linear[data[j]] * weight
                         for data, weight in zip(sources, fixed)) + scale // 2) // scale
            output[j] = to_srgb[max(0, min(65535, value))]
        encoded = bytes(output)
    return Image.frombytes("RGB", first.size, encoded)


class TemporalShutter:
    """Pure, frame-addressed lookback: modulated settings never store state."""

    _DEFAULTS = {"strength": .8, "shutter_frames": 3, "samples": 4, "falloff": 1.0}
    _RANGES = {"strength": (0, 1), "shutter_frames": (0, 8),
               "samples": (2, 9), "falloff": (0, 8)}
    _PROXY = {"strength": "motion_blur", "shutter_frames": "motion_blur_distance",
              "samples": "motion_blur_samples", "falloff": "motion_blur_falloff"}

    def __init__(self, project, config, context_at):
        if not isinstance(config, dict):
            raise ValueError("post_effects.temporal must be an object")
        fields(config, {"strength", "shutter_frames", "samples", "falloff", "modulations"},
               "temporal motion blur")
        self.base = {}
        for key, (lo, hi) in self._RANGES.items():
            value = config.get(key, self._DEFAULTS[key])
            value = integer(value, key, minimum=lo, maximum=hi) if key == "samples" else (
                number(value, key, minimum=lo, maximum=hi))
            self.base[key] = value
        rows = config.get("modulations", [])
        if not isinstance(rows, list) or len(rows) > 16:
            raise ValueError("Temporal blur modulations must be a list of <=16 entries")
        from .modulation import _Modulator
        self.drivers = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("target"), str):
                raise ValueError("Temporal modulation must be an object with a target")
            if not row["target"].startswith("temporal."):
                raise ValueError("Temporal blur modulation target must begin temporal.")
            name = row["target"].removeprefix("temporal.")
            if name not in self._RANGES or any(key == name for key, _ in self.drivers):
                raise ValueError("Unknown or duplicated temporal blur modulation target")
            proxy = dict(row, target="visual." + self._PROXY[name])
            proxy.setdefault("range", list(self._RANGES[name]))
            self.drivers.append((name, _Modulator(proxy, project, context_at)))
        self.files = [file for _, driver in self.drivers for file in driver.motion.files]
        self.evidence = dict(config=dict(self.base),
                             modulations=[dict(driver.evidence, target="temporal." + key)
                                          for key, driver in self.drivers],
                             sampling="observed prior encoded frames; linear-light shutter blend; no interpolation")
        self.context_at = context_at

    def parameters(self, context):
        values = dict(self.base)
        reports = {}
        for key, driver in self.drivers:
            result = driver.output(context)
            if result is None:
                continue
            value, native = result
            lo, hi = self._RANGES[key]
            value = max(lo, min(hi, value))
            if key == "samples":
                value = round(value)
            values[key] = value
            reports["temporal." + key] = dict(value=value, native_value=native)
        return values, reports

    def plan(self, context):
        params, reports = self.parameters(context)
        count = round(params["samples"])
        span = params["shutter_frames"]
        candidates = {context.frame}
        if params["strength"] and span >= 1:
            for j in range(1, count):
                lag = round(j * span / (count - 1))
                n = context.frame - lag
                if lag <= 0 or n < 0:
                    continue
                # Crossfades at repeated source excerpts are deliberately forbidden.
                sample = self.context_at(n)
                if sample.source_seconds > context.source_seconds + 1e-9:
                    continue
                candidates.add(n)
        indices = sorted(candidates)
        weights = [math.exp(-params["falloff"] * (context.frame - idx) / max(1, span))
                   for idx in indices]
        return indices, weights, params, reports

    def combine(self, images, weights, strength):
        return linear_shutter_composite(images, weights, strength)


def render_temporal_frame(compositor, frame):
    """Recompose past frames without feedback or time-dependent cache state."""
    temporal = compositor.post_pipeline.temporal
    context = compositor.context(frame)
    indices, weights, settings, reports = temporal.plan(context)
    old_extents, old_texts = deepcopy(compositor.extents), deepcopy(compositor.text_states)
    sampled = []
    compositor._temporal_sampling = True
    try:
        for idx in indices:
            compositor.extents = deepcopy(old_extents)
            compositor.text_states = deepcopy(old_texts)
            image, _ = compositor.frame(idx)
            sampled.append(image)
        current_extents, current_texts = compositor.extents, compositor.text_states
    except BaseException:
        compositor.extents, compositor.text_states = old_extents, old_texts
        raise
    finally:
        compositor._temporal_sampling = False
    compositor.extents, compositor.text_states = current_extents, current_texts
    compositor.post_pipeline.last_report["temporal"] = dict(
        parameters=settings, modulations=reports, source_frames=indices,
        weights=weights, source_seconds=context.source_seconds)
    return temporal.combine(sampled, weights, settings["strength"]), context
