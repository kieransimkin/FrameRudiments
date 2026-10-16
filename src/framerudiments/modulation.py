"""Exact-frame, source-bound DanceRudiment modulation of visualisation parameters.

No independent oscillator, future-peeking, random walk or mutable smoothing
accumulator: any encoded frame can be rendered alone with identical pixels.
"""
import math

from .io import fields, number, integer
from .motion import Motion
from .visual_style import VISUAL_RANGES


CURVES = {"linear", "smoothstep", "ease-in", "ease-out", "sine", "square", "cubic",
          "absolute", "steps", "threshold", "triangle", "bell"}
BLENDS = {"set", "add", "multiply", "min", "max"}
STYLE_INTEGERS = {"line_width", "count", "trail", "lag", "seed", "font_size", "peak_count",
                  "min_peak_distance", "history_limit", "led_segments", "rings", "axis", "fill_alpha", "spokes", "sides"}
GRADIENT_TARGET_ALIASES = {"gradient.phase": "visual.gradient_phase",
                           "gradient.mix": "visual.gradient_mix",
                           "gradient.cycles": "visual.gradient_cycles"}
MAX_MODULATORS = 32


def _clamp(v):
    return max(0.0, min(1.0, v))


def _curve(value, curve, steps, deadzone, power):
    value = max(-1.0, min(1.0, value))
    if abs(value) < deadzone:
        value = 0.0
    u = (value + 1) / 2
    if curve == "absolute":
        u = abs(value)
    elif curve == "triangle":
        u = 1 - abs(value)
    elif curve == "bell":
        u = 1 - abs(2 * u - 1)
    elif curve == "smoothstep":
        u = u * u * (3 - 2 * u)
    elif curve in {"ease-in", "square"}:
        u *= u
    elif curve == "ease-out":
        u = 1 - (1 - u) ** 2
    elif curve == "cubic":
        u *= u * u
    elif curve == "sine":
        u = (1 - math.cos(math.pi * u)) / 2
    elif curve == "threshold":
        u = float(u >= .5)
    if curve == "steps" or steps:
        count = steps or 8
        u = round(u * (count - 1)) / (count - 1)
    return _clamp(u) ** power


class _Modulator:
    def __init__(self, config, project, context_at):
        fields(config, {"id", "motion", "axis", "target", "range", "curve", "steps", "power",
                        "invert", "combine", "weight", "lag_frames", "smoothing_frames", "deadzone",
                        "sample_stride"}, "rudiment modulator")
        if not isinstance(config.get("motion"), dict):
            raise ValueError("Modulator needs an independent DanceRudiment motion object")
        self.id = config.get("id", "")
        if not isinstance(self.id, str) or len(self.id) > 64:
            raise ValueError("Modulator id must be a short string")
        self.motion = Motion(config["motion"], project.base, project.clock)
        self.target = GRADIENT_TARGET_ALIASES.get(config.get("target"), config.get("target"))
        if not isinstance(self.target, str) or not self.target.startswith(("visual.", "style.")):
            raise ValueError("Modulation target must be visual.* or style.*")
        self.scope, self.property = self.target.split(".", 1)
        if not self.property.isidentifier() or self.property.startswith("_"):
            raise ValueError("Invalid modulator target")
        if self.scope == "visual" and self.property not in VISUAL_RANGES:
            raise ValueError(f"Unknown visual modulation property: {self.property}")
        dim = self.motion.value_dimensions
        axis = config.get("axis", 0)
        if isinstance(axis, str):
            if axis not in dim:
                raise ValueError(f"Unknown modulator source axis: {axis}")
            axis = dim.index(axis)
        self.axis = integer(axis, "modulation axis", maximum=len(dim) - 1)
        interval = config.get("range", [-1, 1])
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError("Modulation range must be [minimum, maximum]")
        self.lower, self.upper = (number(v, "modulation bound", minimum=-10000, maximum=10000)
                                  for v in interval)
        if self.upper <= self.lower:
            raise ValueError("Modulation range must be increasing")
        self.curve = config.get("curve", "linear")
        if self.curve not in CURVES:
            raise ValueError("Unknown modulator transfer curve")
        self.steps = integer(config.get("steps", 0), "modulator steps", minimum=0, maximum=128)
        if self.steps == 1:
            raise ValueError("Modulator steps must be zero or >=2")
        self.power = number(config.get("power", 1), "modulator power", minimum=.01, maximum=20)
        self.invert = config.get("invert", False)
        if not isinstance(self.invert, bool):
            raise ValueError("Modulator invert must be boolean")
        self.combine = config.get("combine", "set")
        if self.combine not in BLENDS:
            raise ValueError("Unknown modulator combination")
        self.weight = number(config.get("weight", 1), "modulator weight", minimum=0, maximum=1)
        self.lag = integer(config.get("lag_frames", 0), "modulator lag", minimum=0, maximum=600)
        self.smoothing = integer(config.get("smoothing_frames", 0), "modulator smoothing", minimum=0, maximum=120)
        self.stride = integer(config.get("sample_stride", 1), "modulator stride", minimum=1, maximum=60)
        self.deadzone = number(config.get("deadzone", 0), "modulator deadzone", minimum=0, maximum=.99)
        self.context_at = context_at
        self.evidence = dict(id=self.id, target=self.target, axis=dim[self.axis],
                             motion=self.motion.evidence, config=config, sampling="past encoded frames only")

    def output(self, context):
        values = []
        for j in range(self.smoothing + 1):
            n = context.frame - self.lag - j * self.stride
            if n < 0:
                break
            sample = self.context_at(n)
            point = self.motion.values(sample.source_seconds, continuous_beat=sample.beat)[self.axis]
            values.append(number(point, "modulator native value", minimum=-1, maximum=1))
        if not values:
            # No observed driver sample exists yet: neutral modulation.
            return None
        # Recency-weighted FIR. Evaluation is independent of prior render calls.
        weights = [len(values) - j for j in range(len(values))]
        value = sum(w * v for w, v in zip(weights, values)) / sum(weights)
        u = _curve(value, self.curve, self.steps, self.deadzone, self.power)
        if self.invert:
            u = 1 - u
        return self.lower + u * (self.upper - self.lower), value


def _combine(base, value, operation, weight):
    if operation == "set":
        return base * (1 - weight) + value * weight
    if operation == "add":
        return base + weight * value
    if operation == "multiply":
        return base * (1 - weight + weight * value)
    if operation == "min":
        return base * (1 - weight) + min(base, value) * weight
    return base * (1 - weight) + max(base, value) * weight


class RudimentModulations:
    def __init__(self, project, configs, context_at):
        if not isinstance(configs, list) or len(configs) > MAX_MODULATORS:
            raise ValueError("modulations must be a list with <=32 entries")
        self.drivers = [_Modulator(row, project, context_at) for row in configs]
        ids = [x.id for x in self.drivers if x.id]
        if len(set(ids)) != len(ids):
            raise ValueError("Named modulator ids must be unique")
        self.files = [file for driver in self.drivers for file in driver.motion.files]
        self.evidence = [driver.evidence for driver in self.drivers]

    def apply(self, context, base_style, base_visual):
        styles, visuals, results = dict(base_style), dict(base_visual), {}
        for driver in self.drivers:
            result = driver.output(context)
            if result is None:
                continue
            value, source = result
            target = styles if driver.scope == "style" else visuals
            if driver.scope == "visual":
                start = target.get(driver.property, VISUAL_RANGES[driver.property][2])
            else:
                if driver.combine != "set" and driver.property not in target:
                    raise ValueError(f"Style modulation {driver.property}: provide a base style for relative combination")
                start = target.get(driver.property, 0)
                if isinstance(start, bool) or not isinstance(start, (int, float)):
                    raise ValueError("Style modulation needs a numeric renderer style")
            val = _combine(start, value, driver.combine, driver.weight)
            if driver.scope == "style" and driver.property in STYLE_INTEGERS:
                val = round(val)
            target[driver.property] = val
            results[driver.id or driver.target] = dict(target=driver.target, source_value=source, value=val)
        return styles, visuals, results
