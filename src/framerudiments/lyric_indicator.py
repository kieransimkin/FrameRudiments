"""Deterministic word-following sprite with continuous and timed particle treatments.

Native DanceRudiments contributes only an explicitly selected, scalar bounce axis.
Word and cue events stay on their exact source-bound encoded-frame ledger; no
mutable emitter state, synthetic audio beat or forward-time sampling is used.
"""
from __future__ import annotations

from bisect import bisect_right
from functools import lru_cache
from fnmatch import fnmatchcase
import math
import random

from PIL import Image, ImageDraw, ImageFilter

from .io import fields, integer, number
from .motion import Motion
from .modulation import RudimentModulations
from .particle_engine import _shape_mask, _read_cues
from .rich_subtitles import rgba, word_centres
from .timeline import half_up
from fractions import Fraction


ALLOWED_MODULATIONS = {"indicator_size", "indicator_glow", "indicator_opacity", "offset_x",
                       "offset_y", "rotation", "bounce_amplitude", "particle_rate",
                       "particle_size", "particle_glow", "particle_opacity", "particle_burst"}
MAX_ACTIVE = 384


def _sig(seed, frame, ordinal):
    return random.Random((int(seed) * 1103515245 + frame * 1664525 + ordinal * 1013904223) & 0xffffffff)


def _profile(row, label, time_factor=1):
    fields(row, {"burst_count", "rate_multiplier", "size_multiplier", "colour", "glow",
                 "duration_frames", "opacity", "indicator_scale", "indicator_colour"}, label)
    result = dict(burst_count=integer(row.get("burst_count", 0), f"{label} burst", maximum=128),
                  rate_multiplier=number(row.get("rate_multiplier", 1), f"{label} rate", minimum=0, maximum=12),
                  size_multiplier=number(row.get("size_multiplier", 1), f"{label} size", minimum=.1, maximum=6),
                  glow=number(row.get("glow", 0), f"{label} glow", minimum=0, maximum=24),
                  opacity=number(row.get("opacity", 1), f"{label} opacity", minimum=0, maximum=1),
                  indicator_scale=number(row.get("indicator_scale", 1), f"{label} indicator scale", minimum=.2, maximum=4),
                  duration_frames=integer(row.get("duration_frames", 12), f"{label} duration", minimum=1, maximum=240 * time_factor))
    result["colour"] = rgba(row["colour"], f"{label} particle colour") if "colour" in row else None
    result["indicator_colour"] = (rgba(row["indicator_colour"], f"{label} indicator colour")
                                  if "indicator_colour" in row else None)
    result["specified"] = set(row)
    return result


def _colour(colour):
    return rgba(colour, "sprite colour")


def _unit(value):
    return min(1.0, max(0.0, value))


class WordIndicator:
    """Independent overlay drawn after line captions and before final-frame FX."""

    def __init__(self, project, config, context_at, text_block):
        fields(config, {"sprite", "glyph", "size", "colour", "glow", "opacity", "position", "offset_x",
                        "offset_y", "travel_frames", "bounce", "rotation", "particles", "modulations"},
               "word indicator")
        if not project.lyrics or not project.words or text_block is None:
            raise ValueError("Word indicator requires both lyrics and word_lyrics and a full-line captions block")
        if text_block.mode != "current" or text_block.word_mode == "current":
            raise ValueError("Word indicator requires full-line current captions, not isolated word captions")
        self.project, self.context_at, self.block = project, context_at, text_block
        self.font_ref = text_block.font_ref
        self.glyph = config.get("glyph", "spark")
        if self.glyph not in {"spark", "orb", "star", "diamond", "ring"}:
            raise ValueError("Unknown indicator glyph")
        self.sprite_ref = project.ref(config["sprite"]) if "sprite" in config else None
        if self.sprite_ref:
            with Image.open(self.sprite_ref["path"]) as source:
                if source.width > 1024 or source.height > 1024 or source.width < 2 or source.height < 2:
                    raise ValueError("Indicator sprite dimensions must be between 2 and 1024")
                self.sprite = source.convert("RGBA")
            if self.sprite.getchannel("A").getbbox() is None:
                raise ValueError("Word indicator sprite must have visible pixels")
        else:
            self.sprite = None
        self.position = config.get("position", "above")
        if self.position not in {"above", "below", "on"}:
            raise ValueError("Indicator position must be above, below or on")
        time_factor = getattr(project, "_time_sampling_factor", 1)
        self.travel = integer(config.get("travel_frames", 8), "indicator transition frames",
                              maximum=120 * time_factor)
        self.style = dict(indicator_size=number(config.get("size", 18), "indicator size", minimum=2, maximum=128),
                          indicator_glow=number(config.get("glow", 4), "indicator glow", minimum=0, maximum=24),
                          indicator_opacity=number(config.get("opacity", 1), "indicator opacity", minimum=0, maximum=1),
                          offset_x=number(config.get("offset_x", 0), "indicator offset x", minimum=-300, maximum=300),
                          offset_y=number(config.get("offset_y", 12), "indicator offset y", minimum=-300, maximum=300),
                          rotation=number(config.get("rotation", 0), "indicator rotation", minimum=-360, maximum=360),
                          bounce_amplitude=0.0, particle_rate=0.0, particle_size=0.0,
                          particle_glow=0.0, particle_opacity=0.0, particle_burst=0.0)
        self.colour = _colour(config.get("colour", "#fff6ba"))
        self.bounce = None
        self.bounce_axis = 0
        if "bounce" in config:
            bounce = config["bounce"]
            fields(bounce, {"motion", "axis", "amplitude"}, "indicator bounce")
            self.bounce = Motion(bounce["motion"], project.base, project.clock)
            dims = self.bounce.value_dimensions
            axis = bounce.get("axis", 0)
            if isinstance(axis, str):
                if axis not in dims:
                    raise ValueError("Indicator bounce axis not found in native movement")
                axis = dims.index(axis)
            self.bounce_axis = integer(axis, "indicator bounce axis", maximum=len(dims) - 1)
            self.style["bounce_amplitude"] = number(bounce.get("amplitude", 20), "bounce amplitude", minimum=0, maximum=250)
            project.inputs.extend(self.bounce.files)
        self.modulations = RudimentModulations(project, config.get("modulations", []), context_at)
        for driver in self.modulations.drivers:
            if driver.target not in {f"style.{key}" for key in ALLOWED_MODULATIONS}:
                raise ValueError(f"Unsupported word indicator modulation: {driver.target}")
        project.inputs.extend(self.modulations.files)
        self._events = {"word": {}, "cue": {}}
        for row in project.words:
            if row["text"] and row.get("word_index") is not None:
                self._events["word"].setdefault(row["frame"], []).append(row["text"])
        particle = config.get("particles", {})
        fields(particle, {"mode", "kind", "rate", "burst_count", "size", "lifetime_frames",
                          "speed", "spread", "gravity", "wind", "colour", "opacity", "glow", "seed",
                          "on_word", "on_cue", "cue_labels", "cue_file", "trail"}, "word indicator particles")
        self.particle_mode = particle.get("mode", "off")
        if self.particle_mode not in {"off", "continuous", "word", "cue", "hybrid"}:
            raise ValueError("Particles mode must be off/continuous/word/cue/hybrid")
        self.particle_kind = particle.get("kind", "sparks")
        if self.particle_kind not in {"sparks", "smoke", "embers", "confetti", "fireflies", "glitter"}:
            raise ValueError("Unsupported indicator particle kind")
        self.rate = number(particle.get("rate", 12), "indicator particle rate", minimum=0, maximum=120)
        self.burst = integer(particle.get("burst_count", 14), "indicator particle burst", maximum=128)
        self.particle_size = number(particle.get("size", 5), "indicator particle size", minimum=1, maximum=30)
        self.particle_lifetime = integer(particle.get("lifetime_frames", 35), "indicator particle lifetime",
                                         minimum=1, maximum=240 * time_factor)
        self.particle_speed = number(particle.get("speed", 1.7), "indicator particle speed", minimum=0, maximum=12)
        self.particle_spread = number(particle.get("spread", 360), "indicator particle spread", minimum=0, maximum=360)
        self.particle_gravity = number(particle.get("gravity", 0), "indicator particle gravity", minimum=-2, maximum=2)
        self.particle_wind = number(particle.get("wind", 0), "indicator particle wind", minimum=-2, maximum=2)
        self.particle_colour = _colour(particle.get("colour", "#f9c67d"))
        self.particle_opacity = number(particle.get("opacity", .9), "indicator particle opacity", minimum=0, maximum=1)
        self.particle_glow = number(particle.get("glow", 1), "indicator particle glow", minimum=0, maximum=24)
        self.particle_trail = number(particle.get("trail", 0), "particle trail", minimum=0, maximum=30)
        self.seed = integer(particle.get("seed", 23), "indicator particle seed", maximum=2147483647)
        self.on_word = _profile(particle.get("on_word", {}), "word trigger", time_factor)
        self.on_cue = _profile(particle.get("on_cue", {}), "cue trigger", time_factor)
        labels = particle.get("cue_labels", [])
        if not isinstance(labels, list) or len(labels) > 64 or any(not isinstance(x, str) for x in labels):
            raise ValueError("Cue label patterns must be a list of up to 64 strings")
        self.cue_patterns = tuple(x.upper() for x in labels)
        self.cue_ref = project.ref(particle["cue_file"]) if "cue_file" in particle else None
        if self.cue_ref:
            from_file = _read_cues(self.cue_ref["path"], source_sha=project.audio_ref["sha256"])
            first = Fraction(project.start_sample, project.sample_rate)
            span = Fraction(project.end_sample - project.start_sample, project.sample_rate)
            cue_rows = []
            for cycle in range(project.repeats):
                for event in from_file:
                    if first <= event.second < first + span:
                        idx = half_up((event.second - first + cycle * span) * project.fps)
                        if idx < project.frame_count:
                            cue_rows.append(dict(frame=idx, text=event.raw))
        else:
            cue_rows = project.cues
        for event in cue_rows:
            label = event.get("text", "")
            if label and (not self.cue_patterns or any(fnmatchcase(label.upper(), pattern)
                                                       for pattern in self.cue_patterns)):
                self._events["cue"].setdefault(event["frame"], []).append(label)
        self.style.update(particle_rate=self.rate, particle_size=self.particle_size,
                          particle_glow=self.particle_glow, particle_opacity=self.particle_opacity,
                          particle_burst=self.burst)
        self._event_frames = {key: sorted(value) for key, value in self._events.items()}
        self.frame_report = None
        self.evidence = dict(sprite=self.sprite_ref, font=self.font_ref, glyph=self.glyph,
                             mode=self.particle_mode, source_clock="encoded words/cues + native bounce axis",
                             bounce=self.bounce.evidence if self.bounce else None,
                             modulations=self.modulations.evidence, cue_file=self.cue_ref,
                             cue_events=sum(map(len, self._events["cue"].values())),
                             word_events=sum(map(len, self._events["word"].values())), seed=self.seed)

    def _style_at(self, frame):
        ctx = self.context_at(frame)
        numeric, _, report = self.modulations.apply(ctx, self.style, {})
        bounds = {"indicator_size": (2, 128), "indicator_glow": (0, 24), "indicator_opacity": (0, 1),
                  "offset_x": (-300, 300), "offset_y": (-300, 300), "rotation": (-360, 360),
                  "bounce_amplitude": (0, 250), "particle_rate": (0, 120),
                  "particle_size": (0, 30), "particle_glow": (0, 24),
                  "particle_opacity": (0, 1), "particle_burst": (0, 128)}
        for key, (lo, hi) in bounds.items():
            numeric[key] = number(numeric[key], f"modulated {key}", minimum=lo, maximum=hi)
        return numeric, report

    @lru_cache(maxsize=4096)
    def _centre(self, frame):
        ctx = self.context_at(frame)
        word, line = ctx.word, ctx.lyric
        if not word or not line or not line.get("text") or word.get("word_index") is None:
            return None
        positions = word_centres(self.block, line["text"])
        i = word["word_index"]
        if not 0 <= i < len(positions):
            return None
        x, y = positions[i]
        if self.travel and i:
            u = _unit((frame - word["frame"]) / self.travel)
            smooth = u * u * (3 - 2 * u)
            before = positions[i - 1]
            x = before[0] + (x - before[0]) * smooth
            y = before[1] + (y - before[1]) * smooth
        style, _ = self._style_at(frame)
        h = style["indicator_size"]
        if self.position == "below":
            y += self.block.maximum * 1.1 + h / 2
        elif self.position == "above":
            y -= h / 2 + style["offset_y"]
        else:
            y += self.block.maximum * .5
        if self.bounce:
            values = self.bounce.sample(ctx.source_seconds, continuous_beat=ctx.beat)
            y -= values[self.bounce_axis] * style["bounce_amplitude"]
        return x + style["offset_x"], y, style

    def _recent(self, frame, kind):
        duration = (self.on_word if kind == "word" else self.on_cue)["duration_frames"]
        frames = self._event_frames[kind]
        index = bisect_right(frames, frame) - 1
        if index < 0 or frame - frames[index] >= duration:
            return None
        return frames[index]

    def _profile_at(self, frame):
        state = dict(indicator_colour=None, indicator_scale=1., size_multiplier=1.,
                     rate_multiplier=1., colour=None, glow=0., opacity=1.)
        for kind, profile in (("word", self.on_word), ("cue", self.on_cue)):
            if self._recent(frame, kind) is not None:
                for key in profile["specified"]:
                    if key in state and profile.get(key) is not None:
                        state[key] = profile[key]
        return state

    def _symbol(self, size, colour, rotation):
        size = max(2, min(256, round(size)))
        if self.sprite is not None:
            tile = self.sprite.resize((size, size), Image.Resampling.LANCZOS)
            tint = Image.new("RGBA", tile.size, colour)
            mixed = Image.blend(tile, tint, .35)
            mixed.putalpha(tile.getchannel("A"))
            tile = mixed
        else:
            tile = Image.new("RGBA", (size, size))
            draw = ImageDraw.Draw(tile)
            pad = max(1, size // 8)
            if self.glyph == "ring":
                draw.ellipse((pad, pad, size - pad, size - pad), outline=colour, width=max(1, size // 6))
            elif self.glyph == "diamond":
                draw.polygon(((size / 2, pad), (size - pad, size / 2), (size / 2, size - pad),
                              (pad, size / 2)), fill=colour)
            elif self.glyph == "star":
                xy = []
                for n in range(10):
                    theta = -math.pi / 2 + n * math.pi / 5
                    radius = size * (.44 if n % 2 == 0 else .19)
                    xy.append((size / 2 + math.cos(theta) * radius, size / 2 + math.sin(theta) * radius))
                draw.polygon(xy, fill=colour)
            elif self.glyph == "orb":
                draw.ellipse((pad, pad, size - pad, size - pad), fill=colour)
            else:
                draw.line((size / 2, pad, size / 2, size - pad), fill=colour, width=max(2, size // 5))
                draw.line((pad, size / 2, size - pad, size / 2), fill=colour, width=max(2, size // 5))
                draw.ellipse((size * .36, size * .36, size * .64, size * .64), fill="#ffffff")
        return tile.rotate(rotation, Image.Resampling.BICUBIC, expand=True) if rotation else tile

    def _particles(self, canvas, frame):
        if self.particle_mode == "off":
            return 0, 0
        born, count = 0, 0
        first = max(0, frame - self.particle_lifetime + 1)
        for n in range(first, frame + 1):
            centre = self._centre(n)
            if centre is None:
                continue
            x, y, parameters = centre
            effects = self._profile_at(n)
            births = []
            if self.particle_mode in {"continuous", "hybrid"}:
                rate = parameters["particle_rate"] * effects["rate_multiplier"]
                # Counter-based fractional births; no mutable emitter accumulators.
                expected = rate / self.project.fps
                whole = math.floor(expected)
                fractional = expected - whole
                rng = _sig(self.seed, n, 999)
                births.extend((j, "continuous") for j in range(whole + int(rng.random() < fractional)))
            if self.particle_mode in {"word", "hybrid"} and n in self._events["word"]:
                amount = round(self.on_word["burst_count"] if "burst_count" in self.on_word["specified"]
                               else parameters["particle_burst"])
                births.extend((1000 + j, "word") for j in range(min(128, amount)))
            if self.particle_mode in {"cue", "hybrid"} and n in self._events["cue"]:
                amount = round(self.on_cue["burst_count"] if "burst_count" in self.on_cue["specified"]
                               else parameters["particle_burst"])
                births.extend((2000 + j, "cue") for j in range(min(128, amount)))
            if n == frame:
                born += len(births)
            for ident, trigger in births:
                if count >= MAX_ACTIVE:
                    return born, count
                rng = _sig(self.seed, n, ident)
                age = frame - n
                fade = (1 - age / self.particle_lifetime) ** 1.5
                theta = math.radians(-90 + (rng.random() - .5) * self.particle_spread)
                velocity = self.particle_speed * (.5 + rng.random())
                px = x + math.cos(theta) * velocity * age + self.particle_wind * age * age * .5
                py = y + math.sin(theta) * velocity * age + self.particle_gravity * age * age * .5
                size = max(1, min(128, round(parameters["particle_size"]
                                             * effects["size_multiplier"] * (.7 + rng.random() * .6))))
                a = _unit(parameters["particle_opacity"]
                          * effects["opacity"] * fade)
                colour = effects["colour"] or self.particle_colour
                if trigger == "word" and self.on_word["colour"]:
                    colour = self.on_word["colour"]
                if trigger == "cue" and self.on_cue["colour"]:
                    colour = self.on_cue["colour"]
                if self.particle_kind in {"smoke", "fireflies"}:
                    shape = "soft"
                elif self.particle_kind == "confetti":
                    shape = "square"
                elif self.particle_kind == "sparks":
                    shape = "streak"
                else:
                    shape = "star"
                shape_mask = _shape_mask(shape, max(3, 2 * size + 1), 70 if shape == "soft" else 10)
                tile = Image.new("RGBA", shape_mask.size, colour)
                tile.putalpha(shape_mask.point(lambda value: round(value * a)))
                if self.particle_trail:
                    trail = ImageDraw.Draw(canvas)
                    trail.line((px, py, px - math.cos(theta) * self.particle_trail,
                                py - math.sin(theta) * self.particle_trail), fill=(*colour[:3], round(a * 90)),
                               width=max(1, size // 3))
                x0, y0 = round(px - tile.width / 2), round(py - tile.height / 2)
                glow = parameters["particle_glow"] + effects["glow"]
                if glow:
                    margin = min(72, math.ceil(glow * 2))
                    padded = Image.new("RGBA", (tile.width + margin * 2, tile.height + margin * 2))
                    padded.alpha_composite(tile, (margin, margin))
                    halo = padded.filter(ImageFilter.GaussianBlur(glow))
                    self._clip_tile(canvas, halo, x0 - margin, y0 - margin)
                self._clip_tile(canvas, tile, x0, y0)
                count += 1
        return born, count

    @staticmethod
    def _clip_tile(canvas, tile, x, y):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(canvas.width, x + tile.width), min(canvas.height, y + tile.height)
        if x1 > x0 and y1 > y0:
            canvas.alpha_composite(tile.crop((x0-x, y0-y, x1-x, y1-y)), (x0, y0))

    def draw(self, image, ctx):
        self.frame_report = None
        if not 0 <= ctx.frame < self.project.frame_count:
            raise ValueError("Indicator frame exceeds video duration")
        centre = self._centre(ctx.frame)
        if centre is None:
            return None
        x, y, parameters = centre
        effects = self._profile_at(ctx.frame)
        layer = Image.new("RGBA", image.size)
        born, active = self._particles(layer, ctx.frame)
        colour = effects["indicator_colour"] or self.colour
        size = parameters["indicator_size"] * effects["indicator_scale"]
        tile = self._symbol(size, colour, parameters["rotation"])
        opacity = parameters["indicator_opacity"] * effects["opacity"]
        tile.putalpha(tile.getchannel("A").point(lambda a: round(a * opacity)))
        glow = parameters["indicator_glow"] + effects["glow"]
        if glow:
            margin = min(72, math.ceil(glow * 2))
            padded = Image.new("RGBA", (tile.width + margin * 2, tile.height + margin * 2))
            padded.alpha_composite(tile, (margin, margin))
            blurred = padded.filter(ImageFilter.GaussianBlur(glow))
            self._clip_tile(layer, blurred, round(x - tile.width / 2 - margin),
                            round(y - tile.height / 2 - margin))
        self._clip_tile(layer, tile, round(x - tile.width / 2), round(y - tile.height / 2))
        image.alpha_composite(layer)
        self.frame_report = dict(kind="word-indicator", frame=ctx.frame, word=ctx.word["text"],
                                 word_index=ctx.word["word_index"], center=[round(x, 3), round(y, 3)],
                                 particles_births=born, particles_active=active,
                                 cue_trigger=ctx.frame in self._events["cue"],
                                 word_trigger=ctx.frame in self._events["word"])
        return layer.getchannel("A").getbbox()
