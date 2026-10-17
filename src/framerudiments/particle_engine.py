"""Seek-safe, frame-addressed particles and stylised smoke for FrameRudiments.

This module owns no audio DSP, independent rhythm engine or mutable particle
simulation. Every visible particle is reconstructed from its bounded birth
window and native DanceRudiment control values on the exact encoded frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from fractions import Fraction
from functools import lru_cache
import math
from pathlib import Path
import random
import re

from PIL import Image, ImageColor, ImageDraw

from .effects import alpha_scaled, transformed_bounds
from .io import fields, integer, number, rectangle
from .modulation import RudimentModulations
from .timeline import half_up, parse_cue
from .visual_style import VisualStyle

# Every setting is numerical and can be driven by a native DanceRudiment.
# Bounds also constrain dynamic modulation results, before any pixels are drawn.
PARAMETERS = {
    "rate": (0, 1200, 28),  # new particles per second, continuous mode
    "burst_count": (0, 512, 80),
    "lifetime": (0.05, 12, 2.5),
    "size": (0.5, 96, 8),
    "size_growth": (-3, 8, 0.6),  # size change in initial radii / second
    "speed": (0, 4, 0.18),  # viewport heights/second
    "speed_variance": (0, 1, 0.35),
    "direction": (-360, 360, -90),  # degrees, 0 right, -90 up
    "spread": (0, 360, 80),
    "gravity": (-3, 3, 0),  # viewport heights/second squared
    "wind": (-3, 3, 0),
    "drag": (0, 12, 0.25),
    "turbulence": (0, 2, 0.08),
    "turbulence_frequency": (0.1, 16, 2),
    "emitter_x": (0, 1, 0.5),
    "emitter_y": (0, 1, 0.7),
    "emitter_width": (0, 1, 0.06),
    "emitter_height": (0, 1, 0.025),
    "opacity": (0, 1, 0.85),
    "fade_in": (0, 1, 0.05),  # share of particle lifetime
    "fade_out": (0, 1, 0.4),
    "hue": (-360, 360, 0),
    "saturation": (0, 3, 1),
    "brightness": (0, 3, 1),
    "rotation": (-360, 360, 0),
    "spin": (-1440, 1440, 0),  # degrees per second
    "stretch": (0.2, 8, 1),
    "softness": (0, 1, 0.65),
    "trail": (0, 5, 0.15),
    "pulse_frequency": (0, 24, 0),
    "pulse_amount": (0, 1, 0),
}
INTEGER_PARAMETERS = {"burst_count"}
KINDS = {"particles", "smoke", "sparks", "embers", "confetti", "snow", "rain", "dust", "fireflies"}
EMISSION_MODES = {"continuous", "cue", "hybrid"}
MAX_CUES = 20000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BIRTHS_PER_FRAME = 128
MAX_ACTIVE = 2000


def _number(value, name):
    return number(value, name)


def _validated_style(style):
    fields(style, {*PARAMETERS, "colour", "secondary_colour", "shape", "blend", "seed"}, "particle style")
    config = {k: _number(style.get(k, default), f"particle {k}") for k, (_, _, default) in PARAMETERS.items()}
    for k, (lo, hi, _) in PARAMETERS.items():
        if not lo <= config[k] <= hi:
            raise ValueError(f"Particle {k} must be between {lo} and {hi}")
    for k in INTEGER_PARAMETERS:
        config[k] = integer(style.get(k, round(config[k])), f"particle {k}", maximum=int(PARAMETERS[k][1]))
    config["seed"] = integer(style.get("seed", 1), "particle seed", maximum=2**31 - 1)
    config["shape"] = style.get("shape", "disc")
    if config["shape"] not in {"disc", "soft", "square", "streak", "star"}:
        raise ValueError("Particle shape must be disc, soft, square, streak or star")
    config["blend"] = style.get("blend", "alpha")
    if config["blend"] not in {"alpha", "add"}:
        raise ValueError("Particle blend must be alpha or add")
    for name, default in (("colour", "#79dbff"), ("secondary_colour", "#edac7c")):
        value = style.get(name, default)
        if not isinstance(value, str):
            raise ValueError(f"Particle {name} must be a CSS colour")
        config[name] = ImageColor.getcolor(value, "RGBA")
    return config


def _cue_name(text):
    if not isinstance(text, str):
        raise ValueError("Cue labels must be strings")
    text = text.strip()
    found = re.fullmatch(r"\[([A-Za-z][\w -]*):\s*([^\]]+)\]", text)
    if found:
        return found.group(2).strip(), found.group(1).strip().upper(), text
    return text, "", text


@dataclass(frozen=True)
class CueEvent:
    second: Fraction
    label: str
    kind: str
    raw: str
    order: int


_STAMP = re.compile(r"\[(\d{1,9}):([0-5]\d)(?:\.(\d{1,30}))?\]")
_OFFSET = re.compile(r"\[offset:([+-]?\d{1,9})\]", re.IGNORECASE)


def _parse_lrc_cues(path):
    """Preserve StemLab's exact decimal LRC timestamp into video-frame quantisation.

    Offset policy matches FrameRudiments' existing project LRC parser.
    Only the source-time tags are interpreted; cue wording stays opaque.
    """
    rows = []
    document = Path(path).read_text(encoding="utf-8-sig").splitlines()
    offsets = [m for line in document if (m := _OFFSET.fullmatch(line.strip()))]
    if len(offsets) > 1:
        raise ValueError("More than one particle LRC offset is ambiguous")
    offset = Fraction(int(offsets[0][1]), 1000) if offsets else Fraction(0)
    for raw in document:
        line = raw.strip()
        if not line:
            continue
        found = _OFFSET.fullmatch(line)
        if found:
            continue
        position = 0
        stamps = []
        while found := _STAMP.match(line, position):
            value = Fraction(int(found[1]) * 60 + int(found[2]))
            if found[3]:
                value += Fraction(int(found[3]), 10 ** len(found[3]))
            stamps.append(value + offset)
            position = found.end()
        if not stamps:
            if re.fullmatch(r"\[[A-Za-z][^\]]*\]", line):
                continue  # LRC document metadata
            raise ValueError(f"Malformed particle LRC cue: {line[:100]!r}")
        label = line[position:].strip()
        for stamp in stamps:
            name, kind, unparsed = _cue_name(label)
            rows.append(CueEvent(stamp, name, kind, unparsed, len(rows)))
            if len(rows) > MAX_CUES:
                raise ValueError("Particle cue file exceeds 20,000 cue timestamps")
    if not rows:
        raise ValueError("Particle LRC contains no timed cues")
    return rows


def _read_cues(path, *, source_sha=None):
    """Read DanceMoves LRC/JSON, CD CUE, or StemLab snap/generate reports."""
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Particle cue file exceeds 2 MiB")
    suffix = path.suffix.lower()
    if suffix == ".lrc":
        cues = _parse_lrc_cues(path)
    elif suffix == ".cue":
        rows = parse_cue(path)
        cues = [
            CueEvent(Fraction(str(row.source_seconds)), *_cue_name(row.text), i) for i, row in enumerate(rows)
        ]
    elif suffix == ".json":
        from .io import read_json

        data = read_json(path)
        if isinstance(data, list):
            schema = "dancemoves.cues"
            rows = data
        elif isinstance(data, dict):
            schema = data.get("schema", "dancemoves.cues")
            if schema not in {
                "dancemoves.cues",
                "dancemoves.cue-timeline.v1",
                "stemlab.cue-snap.v1",
                "stemlab.cue-generate.v1",
            }:
                raise ValueError(f"Unsupported particle cue schema: {schema}")
            rows = data.get("cues", data.get("events"))
            # A StemLab snap report may carry source identity in its grid.
            if schema.startswith("stemlab."):
                grid = data.get("grid", {})
                if not isinstance(grid, dict):
                    raise ValueError("Invalid StemLab cue grid")
                digest = grid.get("source_sha256") or data.get("source_audio_sha256")
                if digest and source_sha and digest.lower() != source_sha.lower():
                    raise ValueError("StemLab cue source hash differs from project master")
        else:
            raise ValueError("Cue JSON must be an object or event array")
        if not isinstance(rows, list):
            raise ValueError("Cue JSON requires a cues/events list")
        cues = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError("Cue JSON event must be an object")
            is_stemlab = str(schema).startswith("stemlab.")
            if is_stemlab:
                time = row.get("output_seconds_exact", row.get("output_seconds"))
                label = row.get("label")
            else:
                time = row.get("time", row.get("source_seconds", row.get("time_seconds")))
                label = row.get("label", row.get("name", row.get("id", "")))
            if not isinstance(time, (int, float, str)) or isinstance(time, bool):
                raise ValueError("Cue times must be numeric seconds")
            try:
                stamp = Fraction(str(time))
            except (ValueError, ZeroDivisionError, OverflowError) as exc:
                raise ValueError("Malformed cue timestamp") from exc
            # Stringified nan/inf aren't valid Fraction; bounds prevent pathological timestamps.
            if not 0 <= stamp <= 24 * 3600:
                raise ValueError("Cue time outside supported 24-hour source period")
            name, kind, raw = _cue_name(label)
            if not is_stemlab and isinstance(row.get("type"), str):
                kind = row["type"].strip().upper()
            cues.append(CueEvent(stamp, name, kind, raw, i))
    else:
        raise ValueError("Particle cues must be .lrc, .cue or .json")
    if len(cues) > MAX_CUES:
        raise ValueError("Particle cue file exceeds 20,000 cue events")
    if any(c.second < 0 for c in cues):
        raise ValueError("Cue time cannot be negative")
    return tuple(sorted(cues, key=lambda c: (c.second, c.order)))


def _selected(cue, patterns):
    if not patterns:
        return True
    options = (
        cue.label.upper(),
        cue.raw.upper(),
        f"{cue.kind}: {cue.label}".upper(),
        f"[{cue.kind}: {cue.label}]".upper(),
    )
    return any(fnmatchcase(value, pattern.upper()) for pattern in patterns for value in options)


def _hash_seed(seed, frame, index, extra=0):
    # 64-bit stable mixer: independent of process hash seeds, threads and seek order.
    n = (
        seed
        + 0x9E3779B97F4A7C15
        + (frame + 1) * 0xBF58476D1CE4E5B9
        + (index + 1) * 0x94D049BB133111EB
        + extra
    ) & 0xFFFFFFFFFFFFFFFF
    n ^= n >> 30
    n = (n * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    n ^= n >> 27
    n = (n * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return n ^ (n >> 31)


@lru_cache(maxsize=384)
def _soft_mask(size, softness):
    """Premultiplied-friendly radial smoke alpha, generated without randomness."""
    size = max(2, int(size))
    f = softness / 100
    mask = Image.new("L", (64, 64))
    pix = mask.load()
    for y in range(64):
        for x in range(64):
            dist = math.hypot((x + 0.5 - 32) / 31.5, (y + 0.5 - 32) / 31.5)
            value = max(0, 1 - dist * dist)
            pix[x, y] = round(255 * value ** (1.3 + 3.5 * f))
    return mask.resize((size, size), Image.Resampling.LANCZOS)


@lru_cache(maxsize=384)
def _shape_mask(shape, size, softness):
    if shape == "soft":
        return _soft_mask(size, softness)
    image = Image.new("L", (size, size))
    draw = ImageDraw.Draw(image)
    if shape == "square":
        draw.rectangle((1, 1, size - 2, size - 2), fill=255)
    elif shape == "streak":
        draw.rounded_rectangle((size * 0.36, 0, size * 0.64, size - 1), radius=size / 7, fill=255)
    elif shape == "star":
        points = []
        for j in range(10):
            a = -math.pi / 2 + math.tau * j / 10
            radius = size * (0.48 if j % 2 == 0 else 0.2)
            points.append((size / 2 + radius * math.cos(a), size / 2 + radius * math.sin(a)))
        draw.polygon(points, fill=255)
    else:
        draw.ellipse((1, 1, size - 2, size - 2), fill=255)
    return image


class ParticleEmitter:
    """One ordinary composited layer; integrates with existing scoped post FX."""

    def __init__(self, project, cfg, context_at):
        fields(
            cfg,
            {
                "id",
                "type",
                "kind",
                "bounds",
                "style",
                "visual",
                "modulations",
                "emission",
                "opacity",
                "particle_sprite",
                "start_seconds",
                "end_seconds",
            },
            "particle emitter layer",
        )
        self.project = project
        self.motion = None  # Supports the existing compositor's native-motion evidence contract.
        self.id = cfg.get("id")
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Particle emitter id must be a nonempty string")
        self.kind = cfg.get("kind", "particles")
        if self.kind not in KINDS:
            raise ValueError(f"Unknown particle emitter kind: {self.kind}")
        self.bounds = tuple(
            map(
                int,
                rectangle(
                    cfg.get("bounds", [0, 0, project.width, project.height]),
                    "particle bounds",
                    project.width,
                    project.height,
                ),
            )
        )
        if any(x != int(x) for x in cfg.get("bounds", [0, 0, project.width, project.height])):
            raise ValueError("Particle bounds must use integer pixels")
        self.size = (self.bounds[2] - self.bounds[0], self.bounds[3] - self.bounds[1])
        self.opacity = number(cfg.get("opacity", 1), "particle layer opacity", minimum=0, maximum=1)
        self.start = number(cfg.get("start_seconds", 0), "particle start", minimum=0)
        self.end = number(cfg.get("end_seconds", 24 * 3600), "particle end", minimum=self.start)
        defaults = (
            {
                "shape": "soft",
                "colour": "#dce5ef",
                "secondary_colour": "#7696b3",
                "rate": 16,
                "size": 14,
                "speed": 0.09,
                "direction": -90,
                "turbulence": 0.14,
                "lifetime": 3.5,
                "size_growth": 0.8,
            }
            if self.kind == "smoke"
            else {}
        )
        fields(
            cfg.get("style", {}),
            {*PARAMETERS, "colour", "secondary_colour", "shape", "blend", "seed"},
            "particle style",
        )
        defaults.update(cfg.get("style", {}))
        if self.kind in {"sparks", "embers"}:
            presets = {
                "shape": "streak",
                "colour": "#ffd987",
                "secondary_colour": "#f36b32",
                "speed": 0.5,
                "spread": 110,
                "lifetime": 1.2,
                "rate": 35,
            }
        elif self.kind == "snow":
            presets = {
                "shape": "soft",
                "colour": "#eaf5ff",
                "speed": 0.12,
                "direction": 90,
                "spread": 25,
                "emitter_y": 0.05,
                "emitter_width": 1,
                "lifetime": 7,
                "gravity": 0.015,
                "size": 4,
            }
        elif self.kind == "rain":
            presets = {
                "shape": "streak",
                "colour": "#99caff",
                "speed": 0.9,
                "direction": 90,
                "emitter_y": 0.04,
                "emitter_width": 1,
                "lifetime": 1.7,
                "spread": 12,
                "size": 3,
                "stretch": 3,
            }
        elif self.kind == "confetti":
            presets = {
                "shape": "square",
                "speed": 0.35,
                "spread": 160,
                "gravity": 0.15,
                "spin": 120,
                "size": 6,
            }
        elif self.kind == "dust":
            presets = {"shape": "soft", "colour": "#d8cbab", "speed": 0.06, "lifetime": 5, "size": 3}
        elif self.kind == "fireflies":
            presets = {
                "shape": "soft",
                "colour": "#b6f274",
                "speed": 0.05,
                "pulse_frequency": 1,
                "pulse_amount": 0.75,
                "lifetime": 5,
                "size": 5,
            }
        else:
            presets = {}
        self.base = _validated_style({**presets, **defaults})
        self.visual = cfg.get("visual", {})
        if not isinstance(self.visual, dict):
            raise ValueError("Particle visual must be an object")
        VisualStyle(self.visual)
        self.mods = RudimentModulations(project, cfg.get("modulations", []), context_at)
        allowed = set(PARAMETERS)
        for driver in self.mods.drivers:
            if driver.scope == "style" and driver.property not in allowed:
                raise ValueError(f"Unknown particle modulation parameter: {driver.property}")
            # Existing RudimentModulations validates every visual target.
        project.inputs.extend(self.mods.files)
        self.context_at = context_at
        self.emission = cfg.get("emission", {})
        fields(self.emission, {"mode", "cue_file", "labels", "burst_count"}, "particle emission")
        self.mode = self.emission.get("mode", "continuous")
        if self.mode not in EMISSION_MODES:
            raise ValueError("Particle emission must be continuous, cue or hybrid")
        self.cues_ref = None
        self.events = ()
        patterns = self.emission.get("labels", [])
        if (
            not isinstance(patterns, list)
            or len(patterns) > 64
            or any(not isinstance(p, str) or not p for p in patterns)
        ):
            raise ValueError("Particle cue labels must be up to 64 nonempty patterns")
        if self.mode != "continuous":
            if "cue_file" not in self.emission:
                raise ValueError("Cue-triggered particle emission needs a pinned cue_file")
            self.cues_ref = project.ref(self.emission["cue_file"])
            self.events = tuple(
                c
                for c in _read_cues(
                    self.cues_ref["path"], source_sha=getattr(project, "audio_ref", {}).get("sha256")
                )
                if _selected(c, patterns)
            )
        elif "cue_file" in self.emission:
            raise ValueError("Continuous-only particles must not configure an unused cue file")
        self.burst_default = integer(
            self.emission.get("burst_count", round(self.base["burst_count"])),
            "emission burst count",
            maximum=512,
        )
        self._rate_modulated = any(driver.target == "style.rate" for driver in self.mods.drivers)
        self._burst_modulated = any(driver.target == "style.burst_count" for driver in self.mods.drivers)
        if len(self.events) * project.repeats > 100_000:
            raise ValueError("Cue file/repeats combination schedules too many events")
        self.bursts = self._scheduled_bursts()
        if any(len(events) > 32 for events in self.bursts.values()):
            raise ValueError("More than 32 particle cues on one output frame")
        if self.mode != "cue" and self.base["rate"] / project.fps > MAX_BIRTHS_PER_FRAME:
            raise ValueError("Configured continuous particle birth rate exceeds per-frame cap")
        self.sprite_ref = project.ref(cfg["particle_sprite"]) if "particle_sprite" in cfg else None
        self.sprite_image = None
        if self.sprite_ref:
            with Image.open(self.sprite_ref["path"]) as asset:
                if asset.width > 1024 or asset.height > 1024 or asset.width < 2 or asset.height < 2:
                    raise ValueError("Particle sprite image must be between 2 and 1024 px per side")
                self.sprite_image = asset.convert("RGBA")
            if self.sprite_image.getchannel("A").getbbox() is None:
                raise ValueError("Particle sprite has no visible pixels")
        self.frame_report = None
        self.evidence = dict(
            kind=self.kind,
            emission=self.mode,
            cues=self.cues_ref,
            cue_events=len(self.events),
            scheduled_bursts=len(self.bursts),
            source_policy="exact source cue times mapped to each excerpt/repeat cycle",
            sprite=self.sprite_ref,
            seed=self.base["seed"],
            modulation=self.mods.evidence,
            bounds=self.bounds,
            deterministic="counter-seeded frame births; no integrated state",
        )

    def _scheduled_bursts(self):
        if self.mode == "continuous":
            return {}
        p = self.project
        start = Fraction(p.start_sample, p.sample_rate)
        end = Fraction(p.end_sample, p.sample_rate)
        duration = end - start
        result = {}
        for cycle in range(p.repeats):
            for event in self.events:
                if not start <= event.second < end or not self.start <= float(event.second) < self.end:
                    continue
                relative = event.second - start + cycle * duration
                frame = half_up(relative * p.fps)
                if frame >= p.frame_count:
                    continue
                result.setdefault(frame, []).append(
                    dict(
                        time=float(event.second),
                        label=event.label,
                        type=event.kind,
                        cycle=cycle,
                        order=event.order,
                    )
                )
        return result

    @lru_cache(maxsize=2048)
    def _parameters_at(self, frame):
        ctx = self.context_at(frame)
        style, visual, report = self.mods.apply(ctx, self.base, self.visual)
        for name, (lo, hi, _) in PARAMETERS.items():
            value = number(style[name], f"modulated particle {name}", minimum=lo, maximum=hi)
            style[name] = round(value) if name in INTEGER_PARAMETERS else value
        if self.mode != "cue" and style["rate"] / self.project.fps > MAX_BIRTHS_PER_FRAME:
            raise ValueError("Particle emission exceeds per-frame safety limit")
        if visual:
            VisualStyle(visual)
        return style, visual, report

    def _births(self, frame, style):
        values = []
        if (
            self.mode in {"continuous", "hybrid"}
            and self.start <= self.context_at(frame).source_seconds < self.end
        ):
            expected = style["rate"] / self.project.fps
            whole = int(expected)
            fraction = expected - whole
            if self._rate_modulated:
                count = whole + int(random.Random(_hash_seed(style["seed"], frame, 0)).random() < fraction)
            else:
                rate = Fraction(str(style["rate"])) / self.project.fps
                count = math.floor((frame + 1) * rate) - math.floor(frame * rate)
            for j in range(count):
                values.append((j, None))
        if self.mode in {"cue", "hybrid"}:
            for event_index, event in enumerate(self.bursts.get(frame, ())):
                count = style["burst_count"] if self._burst_modulated else self.burst_default
                count = integer(round(count), "particle cue burst", maximum=512)
                for j in range(count):
                    values.append((j + event_index * 1024 + 512, event))
        return values

    def _particle(self, birth_frame, ordinal, cue, style, now):
        rng = random.Random(_hash_seed(style["seed"], birth_frame, ordinal))
        when = birth_frame / self.project.fps
        age = now - when
        if age < -1e-8:
            return None
        lifetime = style["lifetime"] * (0.85 + 0.3 * rng.random())
        if age >= lifetime:
            return None
        sx = style["emitter_x"] + (rng.random() - 0.5) * style["emitter_width"]
        sy = style["emitter_y"] + (rng.random() - 0.5) * style["emitter_height"]
        theta = math.radians(style["direction"] + (rng.random() - 0.5) * style["spread"])
        speed = style["speed"] * (1 + (rng.random() * 2 - 1) * style["speed_variance"])
        drag = style["drag"]
        distance = speed * (-math.expm1(-drag * age) / drag if drag else age)
        # Viewport-height-normalised acceleration; aspect correction in the X term.
        scale_x = self.size[1] / max(1, self.size[0])
        freq = style["turbulence_frequency"]
        phase = rng.random() * math.tau
        curl = style["turbulence"] * min(age, 1.5) * 0.04
        px = (
            sx
            + (math.cos(theta) * distance + style["wind"] * age * age / 2) * scale_x
            + curl * math.sin(freq * age + phase)
        )
        py = (
            sy
            + math.sin(theta) * distance
            + style["gravity"] * age * age / 2
            + curl * math.cos(freq * age + phase * 1.3)
        )
        fraction = age / lifetime
        alpha = style["opacity"]
        fade_in = style["fade_in"]
        fade_out = style["fade_out"]
        if fade_in:
            # An event that lands exactly on a frame must already be visible.
            alpha *= min(1, (fraction + 0.5 / (self.project.fps * lifetime)) / fade_in)
        if fade_out:
            alpha *= min(1, (1 - fraction) / fade_out)
        if style["pulse_frequency"]:
            alpha *= 1 - style["pulse_amount"] * (
                0.5 - 0.5 * math.cos(math.tau * style["pulse_frequency"] * age)
            )
        grow = max(0.15, 1 + age * style["size_growth"])
        size = max(1, min(256, round(style["size"] * grow * (0.7 + rng.random() * 0.6))))
        angle = style["rotation"] + style["spin"] * age + rng.random() * 360
        rgb = style["colour"] if rng.random() >= 0.25 else style["secondary_colour"]
        from colorsys import rgb_to_hsv, hsv_to_rgb

        h, s, v = rgb_to_hsv(*(c / 255 for c in rgb[:3]))
        h = (h + style["hue"] / 360) % 1
        s = min(1, s * style["saturation"])
        v = min(1, v * style["brightness"])
        colour = tuple(round(c * 255) for c in hsv_to_rgb(h, s, v))
        return (px, py, size, angle, max(0, min(1, alpha)), colour)

    @staticmethod
    def _composite(canvas, tile, pos, *, additive=False):
        from PIL import ImageChops

        x0, y0 = max(0, pos[0]), max(0, pos[1])
        x1, y1 = min(canvas.width, pos[0] + tile.width), min(canvas.height, pos[1] + tile.height)
        if x1 <= x0 or y1 <= y0:
            return
        source = tile.crop((x0 - pos[0], y0 - pos[1], x1 - pos[0], y1 - pos[1]))
        if not additive:
            canvas.alpha_composite(source, (x0, y0))
            return
        old = canvas.crop((x0, y0, x1, y1))
        weight = source.getchannel("A")
        rgb = ImageChops.add(
            old.convert("RGB"), ImageChops.multiply(source.convert("RGB"), Image.merge("RGB", (weight,) * 3))
        )
        output = rgb.convert("RGBA")
        output.putalpha(ImageChops.lighter(old.getchannel("A"), weight))
        canvas.paste(output, (x0, y0))

    def _stamp(self, image, info, style):
        x, y, size, angle, alpha, rgb = info
        if alpha <= 0:
            return
        cx = round(x * (self.size[0] - 1))
        cy = round(y * (self.size[1] - 1))
        shape = style["shape"]
        softness = round(style["softness"] * 100)
        diameter = max(3, min(256, size * 2))
        width = diameter
        height = max(2, min(256, round(diameter * style["stretch"])))
        mask = _shape_mask(shape, diameter, softness)
        if height != diameter:
            mask = mask.resize((width, height), Image.Resampling.LANCZOS)
        if self.sprite_image is not None:
            tile = self.sprite_image.resize((width, height), Image.Resampling.LANCZOS)
            if angle and self.kind not in {"smoke", "dust"}:
                tile = tile.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)
            tile = tile.copy()
            from PIL import ImageChops

            amask = ImageChops.multiply(tile.getchannel("A"), mask)
            if self.kind in {"smoke", "dust"}:
                tint = Image.new("RGBA", tile.size, (*rgb, 255))
                tile = Image.blend(tile, tint, 0.08)
            tile.putalpha(amask.point(lambda a: round(a * alpha)))
        else:
            tile = Image.new("RGBA", (width, height), (*rgb, 0))
            tile.putalpha(mask.point(lambda a: round(a * alpha)))
            if angle and shape in {"streak", "star", "square"}:
                tile = tile.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)
        pos = (cx - tile.width // 2, cy - tile.height // 2)
        additive = style["blend"] == "add"
        if style["trail"]:
            theta = math.radians(style["direction"])
            distance = style["trail"] * size * 2
            # Multiple fading copies extend behind the main motion vector.
            for j in (3, 2, 1):
                echo = alpha_scaled(tile, (0.25 / j))
                shifted = (
                    pos[0] - round(math.cos(theta) * distance * j / 3),
                    pos[1] - round(math.sin(theta) * distance * j / 3),
                )
                self._composite(image, echo, shifted, additive=additive)
        self._composite(image, tile, pos, additive=additive)

    def draw(self, canvas, ctx):
        self.frame_report = None
        if not 0 <= ctx.frame < self.project.frame_count:
            raise ValueError("Particle frame outside encoded programme")
        viewport = Image.new("RGBA", self.size)
        born_here = {}
        born_today = 0
        active = 0
        dropped = 0
        modulated = {}
        # Always bound the reconstruction horizon; older births cannot survive.
        max_lifetime = self.base["lifetime"]
        for driver in self.mods.drivers:
            if driver.target == "style.lifetime":
                max_lifetime = max(max_lifetime, driver.upper)
        first = max(0, ctx.frame - math.ceil(max_lifetime * 1.15 * self.project.fps))
        for n in range(first, ctx.frame + 1):
            style, visual, modulated = self._parameters_at(n)
            for ordinal, cue in self._births(n, style):
                if n == ctx.frame:
                    born_today += 1
                info = self._particle(n, ordinal, cue, style, ctx.output_seconds)
                if info is None:
                    continue
                if active >= MAX_ACTIVE:
                    dropped += 1
                    continue
                self._stamp(viewport, info, style)
                active += 1
                if n == ctx.frame and cue is not None:
                    key = (cue["order"], cue["cycle"])
                    item = born_here.setdefault(
                        key, dict(cue=cue["label"], type=cue["type"], cycle=cue["cycle"], count=0)
                    )
                    item["count"] += 1
        _, visual, current_report = self._parameters_at(ctx.frame)
        modulated = current_report
        if visual:
            # Keep frame-addressed visual colour/effects compatible with existing pipeline.
            viewport = VisualStyle(visual).apply(viewport)
        if self.opacity != 1:
            viewport = alpha_scaled(viewport, self.opacity)
        canvas.alpha_composite(viewport, self.bounds[:2])
        self.frame_report = dict(
            kind="particle-emitter",
            type=self.kind,
            emission=self.mode,
            frame=ctx.frame,
            births=born_today,
            active=active,
            dropped_due_to_cap=dropped,
            cue_bursts=list(born_here.values()),
            modulations=modulated,
            viewport=list(self.bounds),
        )
        bounds = viewport.getchannel("A").getbbox()
        return transformed_bounds(viewport, *self.bounds[:2]) if bounds else None
