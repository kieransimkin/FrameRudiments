"""Exact-frame, source-bound DanceRudiment modulation of visualisation parameters.

No independent oscillator, future-peeking, random walk or mutable smoothing
accumulator: any encoded frame can be rendered alone with identical pixels.
"""

import math
from copy import deepcopy

from .io import fields, number, integer
from .motion import Motion
from .visual_style import VISUAL_RANGES


CURVES = {
    "linear",
    "smoothstep",
    "ease-in",
    "ease-out",
    "sine",
    "square",
    "cubic",
    "absolute",
    "steps",
    "threshold",
    "triangle",
    "bell",
}
BLENDS = {"set", "add", "multiply", "min", "max"}
STYLE_INTEGERS = {
    "line_width",
    "count",
    "trail",
    "lag",
    "seed",
    "font_size",
    "peak_count",
    "min_peak_distance",
    "history_limit",
    "led_segments",
    "rings",
    "axis",
    "fill_alpha",
    "spokes",
    "sides",
    "segments",
    "quantize",
    "stride",
    "sprite_size",
    "sprite_index",
    "segment_count",
    "bin_stride",
    "sample_stride",
    "sprite_columns",
    "sprite_rows",
    "animation_frames",
    "animation_stride",
    "max_stamps",
    "sprite_pixel_size",
    "sprite_outline",
    "sprite_palette_shift",
    "texture_octaves",
    "erosion_iterations",
    "volumetric_steps",
    "shadow_map_size",
}
GRADIENT_TARGET_ALIASES = {
    "gradient.phase": "visual.gradient_phase",
    "gradient.mix": "visual.gradient_mix",
    "gradient.cycles": "visual.gradient_cycles",
}
MAX_MODULATORS = 32

# The renderer and generated authoring forms share these field contracts. The
# descriptors contain no oscillator or sampling state: Motion and _Modulator
# remain the sole source of movement, transfer curves and source-clock timing.
MODULATOR_FIELDS = {
    'id': {'type': 'text', 'default': '', 'max_length': 64},
    'axis': {'type': 'enum', 'default': 0, 'choices': [0]},
    'range': {'type': 'number_pair', 'default': [-1, 1], 'range': [-10000, 10000]},
    'curve': {'type': 'enum', 'default': 'linear', 'choices': sorted(CURVES)},
    'steps': {'type': 'integer', 'default': 0, 'range': [0, 128], 'excluded': [1]},
    'power': {'type': 'number', 'default': 1, 'range': [.01, 20]},
    'invert': {'type': 'boolean', 'default': False, 'choices': [False, True]},
    'combine': {'type': 'enum', 'default': 'set', 'choices': sorted(BLENDS)},
    'weight': {'type': 'number', 'default': 1, 'range': [0, 1]},
    'lag_frames': {'type': 'integer', 'default': 0, 'range': [0, 600], 'units': 'frames'},
    'smoothing_frames': {'type': 'integer', 'default': 0, 'range': [0, 120], 'units': 'frames'},
    'deadzone': {'type': 'number', 'default': 0, 'range': [0, .99]},
    'sample_stride': {'type': 'integer', 'default': 1, 'range': [1, 60], 'units': 'frames'},
}


def modulation_descriptors():
    """Detached native driver-field metadata for tools and authoring clients."""
    return deepcopy(MODULATOR_FIELDS)


def _driver_number(config, key, label):
    contract = MODULATOR_FIELDS[key]
    validate = integer if contract['type'] == 'integer' else number
    lower, upper = contract['range']
    return validate(config.get(key, contract['default']), label, minimum=lower, maximum=upper)


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
        u = float(u >= 0.5)
    if curve == "steps" or steps:
        count = steps or 8
        u = round(u * (count - 1)) / (count - 1)
    return _clamp(u) ** power


class _Modulator:
    def __init__(self, config, project, context_at):
        fields(
            config,
            {
                "id",
                "motion",
                "axis",
                "target",
                "range",
                "curve",
                "steps",
                "power",
                "invert",
                "combine",
                "weight",
                "lag_frames",
                "smoothing_frames",
                "deadzone",
                "sample_stride",
            },
            "rudiment modulator",
        )
        if not isinstance(config.get("motion"), dict):
            raise ValueError("Modulator needs an independent DanceRudiment motion object")
        self.id = config.get("id", MODULATOR_FIELDS['id']['default'])
        if not isinstance(self.id, str) or len(self.id) > MODULATOR_FIELDS['id']['max_length']:
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
        axis = config.get("axis", MODULATOR_FIELDS['axis']['default'])
        if isinstance(axis, str):
            if axis not in dim:
                raise ValueError(f"Unknown modulator source axis: {axis}")
            axis = dim.index(axis)
        self.axis = integer(axis, "modulation axis", maximum=len(dim) - 1)
        interval = config.get("range", MODULATOR_FIELDS['range']['default'])
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError("Modulation range must be [minimum, maximum]")
        self.lower, self.upper = (
            number(v, "modulation bound", minimum=MODULATOR_FIELDS['range']['range'][0],
                   maximum=MODULATOR_FIELDS['range']['range'][1]) for v in interval
        )
        if self.upper <= self.lower:
            raise ValueError("Modulation range must be increasing")
        self.curve = config.get("curve", MODULATOR_FIELDS['curve']['default'])
        if self.curve not in CURVES:
            raise ValueError("Unknown modulator transfer curve")
        self.steps = _driver_number(config, 'steps', 'modulator steps')
        if self.steps in MODULATOR_FIELDS['steps']['excluded']:
            raise ValueError("Modulator steps must be zero or >=2")
        self.power = _driver_number(config, 'power', 'modulator power')
        self.invert = config.get("invert", MODULATOR_FIELDS['invert']['default'])
        if not isinstance(self.invert, bool):
            raise ValueError("Modulator invert must be boolean")
        self.combine = config.get("combine", MODULATOR_FIELDS['combine']['default'])
        if self.combine not in BLENDS:
            raise ValueError("Unknown modulator combination")
        self.weight = _driver_number(config, 'weight', 'modulator weight')
        self.lag = _driver_number(config, 'lag_frames', 'modulator lag')
        self.smoothing = _driver_number(config, 'smoothing_frames', 'modulator smoothing')
        self.stride = _driver_number(config, 'sample_stride', 'modulator stride')
        self.deadzone = _driver_number(config, 'deadzone', 'modulator deadzone')
        self.context_at = context_at
        self.evidence = dict(
            id=self.id,
            target=self.target,
            axis=dim[self.axis],
            motion=self.motion.evidence,
            config=config,
            sampling="past encoded frames only",
        )

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
                    raise ValueError(
                        f"Style modulation {driver.property}: provide a base style for relative combination"
                    )
                start = target.get(driver.property, 0)
                if isinstance(start, bool) or not isinstance(start, (int, float)):
                    raise ValueError("Style modulation needs a numeric renderer style")
            val = _combine(start, value, driver.combine, driver.weight)
            if driver.scope == "style" and driver.property in STYLE_INTEGERS:
                val = round(val)
            target[driver.property] = val
            results[driver.id or driver.target] = dict(target=driver.target, source_value=source, value=val)
        return styles, visuals, results
