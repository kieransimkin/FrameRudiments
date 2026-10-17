"""Isolated composited-element and whole-frame, source-clocked visual effects.

Stages delegate rendering to the same validated VisualStyle as rudiment views.
Independent native DanceRudiments drive all numeric effects and discrete
palette/gradient configuration choices. No mutable temporal effect state, DSP,
user-supplied executable code, or future sample access.
"""

from copy import deepcopy

from PIL import Image

from .io import fields, integer, number
from .modulation import RudimentModulations
from .visual_style import VisualStyle
from .quality_effects import TemporalShutter

MAX_STAGES = 16
MAX_TARGETS = 256
CHOICE_TARGETS = {
    "visual.palette",
    "visual.gradient",
    "stage.coordinate_space",
    "stage.padding",
    *(
        f"visual.gradient.{p}"
        for p in ("mode", "stops", "space", "wrap", "reverse", "blend", "bands", "phase", "mix", "cycles")
    ),
}
CHOICE_DRIVER_FIELDS = {
    "id",
    "target",
    "choices",
    "motion",
    "axis",
    "curve",
    "steps",
    "power",
    "invert",
    "lag_frames",
    "smoothing_frames",
    "deadzone",
    "sample_stride",
}


def _set_choice(visual, target, value):
    """Assign only an allowlisted nested visual field; never traverse objects."""
    result = deepcopy(visual)
    name = target.removeprefix("visual.")
    if name.startswith("gradient."):
        if not isinstance(result.get("gradient", {}), dict):
            raise ValueError("A gradient choice needs a gradient object")
        result.setdefault("gradient", {})[name.split(".", 1)[1]] = deepcopy(value)
    else:
        result[name] = deepcopy(value)
    return result


class ChoiceDriver:
    """Use the *same* native-source sample/curve engine as numeric modulators.

    A proxy numeric target carries the selected index. It is never passed to
    VisualStyle; the final categorical value is validated separately.
    """

    def __init__(self, project, config, context_at, base_visual):
        fields(config, CHOICE_DRIVER_FIELDS, "categorical effect modulator")
        self.target = config.get("target")
        if self.target not in CHOICE_TARGETS:
            raise ValueError("Choice modulation target must be an approved palette/gradient property")
        self.choices = config.get("choices")
        if not isinstance(self.choices, list) or not 2 <= len(self.choices) <= 32:
            raise ValueError("Choice modulation needs 2..32 alternatives")
        # Fail at project load, not halfway through an expensive render.
        for value in self.choices:
            if self.target == "stage.coordinate_space":
                if value not in {"element", "frame"}:
                    raise ValueError("Coordinate-space choices must be element or frame")
            elif self.target == "stage.padding":
                integer(value, "post effect padding choice", minimum=0, maximum=256)
            else:
                VisualStyle(_set_choice(base_visual, self.target, value))
        proxy = {k: v for k, v in config.items() if k not in {"target", "choices"}}
        proxy.update(target="visual.gradient_phase", range=[0, len(self.choices) - 1], combine="set")
        self.native = RudimentModulations(project, [proxy], context_at)
        self.files = self.native.files
        self.id = config.get("id") or self.target
        self.evidence = dict(
            target=self.target,
            id=self.id,
            choices=self.choices,
            native=self.native.evidence,
            policy="nearest categorical choice",
        )

    def choose(self, ctx):
        _, visual, report = self.native.apply(ctx, {}, {})
        if not report:
            return None
        index = max(0, min(len(self.choices) - 1, round(visual["gradient_phase"])))
        return self.choices[index], dict(
            source=next(iter(report.values()))["source_value"], selected_index=index, target=self.target
        )


class EffectStage:
    def __init__(self, project, config, context_at, *, label):
        fields(config, {"id", "visual", "modulations", "coordinate_space", "padding"}, "post effect stage")
        self.id = config.get("id")
        if not isinstance(self.id, str) or not 1 <= len(self.id) <= 64:
            raise ValueError("Post effect stage needs a short nonempty id")
        self.base = config.get("visual", {})
        if not isinstance(self.base, dict):
            raise ValueError("Stage visual must be an object")
        VisualStyle(self.base)
        self.coordinate_space = config.get("coordinate_space", "element")
        if self.coordinate_space not in {"element", "frame"}:
            raise ValueError("coordinate_space must be element or frame")
        self.padding = integer(config.get("padding", 48), "post effect padding", minimum=0, maximum=256)
        rows = config.get("modulations", [])
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError("Stage modulations must be a list of at most 32 entries")
        ids = [row.get("id") or row.get("target") for row in rows if isinstance(row, dict)]
        if len(set(ids)) != len(ids):
            raise ValueError("Post effect modulator ids must be unique within a stage")
        normal, choices, padding_drivers = [], [], []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Each post effect modulator must be an object")
            if "choices" in row:
                choices.append(row)
            else:
                target = row.get("target", "")
                if target == "stage.padding":
                    interval = row.get("range", [0, 256])
                    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                        raise ValueError("stage.padding range must contain exactly two numeric bounds")
                    low, high = (number(v, "stage.padding bound", minimum=0, maximum=256) for v in interval)
                    if high <= low:
                        raise ValueError("stage.padding modulation bounds must be strictly increasing")
                    proxy = dict(row, target="visual.gradient_phase")
                    padding_drivers.append(proxy)
                elif isinstance(target, str) and (
                    target.startswith("visual.") or target.startswith("gradient.")
                ):
                    normal.append(row)
                else:
                    raise ValueError(
                        "Stage numeric modulations must target visual.*, gradient.* or stage.padding"
                    )
        if len(padding_drivers) > 1:
            raise ValueError("Only one stage.padding numeric modulator is allowed per stage")
        self.numeric = RudimentModulations(project, normal, context_at)
        self.padding_drivers = [RudimentModulations(project, [row], context_at) for row in padding_drivers]
        self.choices = [ChoiceDriver(project, row, context_at, self.base) for row in choices]
        # A target may only have one driver. This avoids ordering-dependent conflicts.
        targets = (
            [driver.target for driver in self.numeric.drivers]
            + ["stage.padding" for _ in self.padding_drivers]
            + [driver.target for driver in self.choices]
        )
        if len(set(targets)) != len(targets):
            raise ValueError("Duplicate post effect parameter target")
        self.files = (
            self.numeric.files
            + [ref for driver in self.choices for ref in driver.files]
            + [ref for driver in self.padding_drivers for ref in driver.files]
        )
        self.evidence = dict(
            id=self.id,
            scope=label,
            visual=deepcopy(self.base),
            coordinate_space=self.coordinate_space,
            padding=self.padding,
            native_modulations=[
                *self.numeric.evidence,
                *(
                    dict(row, target="stage.padding")
                    for driver in self.padding_drivers
                    for row in driver.evidence
                ),
                *(driver.evidence for driver in self.choices),
            ],
        )

    def apply(self, image, context, *, frame_scope=False):
        _, visual, sample_report = self.numeric.apply(context, {}, deepcopy(self.base))
        report = dict(sample_report)
        padding = self.padding
        coordinate_space = self.coordinate_space
        for driver in self.padding_drivers:
            _, values, native_report = driver.apply(context, {}, {})
            if native_report:
                padding = integer(
                    round(values["gradient_phase"]), "modulated post padding", minimum=0, maximum=256
                )
                report["stage.padding"] = dict(
                    next(iter(native_report.values())), target="stage.padding", value=padding
                )
        for driver in self.choices:
            chosen = driver.choose(context)
            if chosen is not None:
                value, evidence = chosen
                if driver.target == "stage.coordinate_space":
                    coordinate_space = value
                elif driver.target == "stage.padding":
                    padding = value
                else:
                    visual = _set_choice(visual, driver.target, value)
                report[driver.id] = evidence
        # Numeric gradient_phase/mix/cycles are effective top-level overrides,
        # and all 13 advanced CRT/noise controls are shared VisualStyle fields.
        style = VisualStyle(visual)
        if frame_scope or coordinate_space == "frame":
            result = style.apply(image.copy())
        else:
            bbox = image.getchannel("A").getbbox()
            if bbox is None:
                result = image.copy()
            else:
                margin = padding
                x0 = max(0, bbox[0] - margin)
                y0 = max(0, bbox[1] - margin)
                x1 = min(image.width, bbox[2] + margin)
                y1 = min(image.height, bbox[3] + margin)
                crop = image.crop((x0, y0, x1, y1))
                transformed = style.apply(crop)
                result = Image.new("RGBA", image.size)
                result.alpha_composite(transformed, (x0, y0))
        if result.mode != "RGBA" or result.size != image.size:
            raise ValueError("Post effect stage returned invalid frame dimensions or mode")
        return result, dict(
            id=self.id,
            parameters=visual,
            modulations=report,
            stage_options=dict(padding=padding, coordinate_space=coordinate_space),
        )


class CompositingEffects:
    """Post-render stages run at one of three deterministic scopes.

    ``all_elements`` is applied *independently* to every rendered element,
    ``elements`` selects named elements, and ``frame`` runs on the finished RGBA
    frame. All scopes run before existing sequential MilkDrop/projectM effects.
    """

    def __init__(self, project, config, context_at, *, valid_elements):
        fields(config, {"all_elements", "elements", "frame", "temporal"}, "post_effects")
        elements = config.get("elements", {})
        if not isinstance(elements, dict) or len(elements) > MAX_TARGETS:
            raise ValueError("post_effects.elements must be an object with <=256 targets")
        unknown = set(elements) - set(valid_elements)
        if unknown:
            raise ValueError(f"Unknown post effect element ids: {sorted(unknown)}")

        def build(rows, label):
            if not isinstance(rows, list) or len(rows) > MAX_STAGES:
                raise ValueError(f"{label} must contain <=16 stages")
            result = [EffectStage(project, row, context_at, label=label) for row in rows]
            ids = [stage.id for stage in result]
            if len(set(ids)) != len(ids):
                raise ValueError("Post effect stage ids must be unique within a pipeline")
            return result

        self.all_elements = build(config.get("all_elements", []), "all_elements")
        self.elements = {key: build(value, f"elements.{key}") for key, value in elements.items()}
        self.frame = build(config.get("frame", []), "frame")
        self.temporal = (
            TemporalShutter(project, config["temporal"], context_at) if "temporal" in config else None
        )
        self.enabled = bool(
            self.all_elements or self.frame or any(self.elements.values()) or self.temporal is not None
        )
        stages = [
            *self.all_elements,
            *self.frame,
            *(stage for value in self.elements.values() for stage in value),
        ]
        self.files = [ref for stage in stages for ref in stage.files]
        if self.temporal is not None:
            self.files.extend(self.temporal.files)
        project.inputs.extend(self.files)
        self.evidence = dict(
            order="background, scene, layers, identity, caption/art; then frame; then GPU effects",
            all_elements=[stage.evidence for stage in self.all_elements],
            elements={key: [stage.evidence for stage in stages] for key, stages in self.elements.items()},
            frame=[stage.evidence for stage in self.frame],
            temporal=self.temporal.evidence if self.temporal else None,
        )
        self.last_report = {}

    def begin(self):
        self.last_report = {}

    def affects(self, key):
        return bool(self.all_elements or self.elements.get(key))

    def apply_element(self, image, key, ctx):
        stages = [*self.all_elements, *self.elements.get(key, ())]
        if not stages:
            return image
        reports = []
        for stage in stages:
            image, report = stage.apply(image, ctx)
            reports.append(report)
        self.last_report[key] = reports
        return image

    def apply_frame(self, image, ctx):
        if not self.frame:
            return image
        reports = []
        for stage in self.frame:
            image, report = stage.apply(image, ctx, frame_scope=True)
            reports.append(report)
        self.last_report["frame"] = reports
        return image
