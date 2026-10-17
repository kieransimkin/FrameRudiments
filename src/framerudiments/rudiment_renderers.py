"""Pluggable viewport renderers over native, frame-addressed rudiment values."""
from dataclasses import dataclass
from functools import lru_cache
import inspect
from pathlib import Path
import random
import re
from typing import Protocol, TYPE_CHECKING

from PIL import Image, ImageColor, ImageDraw

from .effects import alpha_scaled, transformed_bounds
from .io import fields, integer, number, rectangle, sha256
from .motion import Motion
from .spectrum import SpectrumBand, spectrum_bands, analyse_spectrum

if TYPE_CHECKING:
    from .compositor import FrameContext


@dataclass(frozen=True)
class RudimentSample:
    context: "FrameContext"
    values: tuple[float, ...]


@dataclass(frozen=True)
class RudimentFrame:
    """Immutable dimensions and chronological observed-frame history."""
    context: "FrameContext"
    dimensions: tuple[str, ...]
    values: tuple[float, ...]
    history: tuple[RudimentSample, ...]
    history_frames: int
    history_stride: int
    spectrum_bands: tuple[SpectrumBand, ...] = ()


class RudimentRenderer(Protocol):
    def render(self, frame: RudimentFrame) -> Image.Image:
        """Return viewport-sized RGBA pixels without changing the frame."""
        ...


class RendererRegistry:
    """Explicit per-composition registry; JSON never imports executable code."""
    def __init__(self):
        self._factories = {}

    def register(self, name, factory):
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ValueError("Renderer names need lowercase letters, digits, hyphens or underscores")
        if name in self._factories:
            raise ValueError(f"Renderer already registered: {name}")
        if not callable(factory):
            raise ValueError("Renderer factory must be callable")
        self._factories[name] = factory

    @property
    def names(self):
        return tuple(self._factories)

    def create(self, name, size, style):
        if not isinstance(name, str) or name not in self._factories:
            raise ValueError(f"Unknown rudiment renderer: {name}; available: {', '.join(self.names)}")
        fields(style, set(style) if isinstance(style, dict) else set(), "renderer style")
        factory = self._factories[name]
        renderer = factory(size, dict(style))
        if not callable(getattr(renderer, "render", None)):
            raise ValueError("Renderer factory must return an object with render(frame)")
        identity = {"name": name, "module": getattr(factory, "__module__", type(factory).__module__),
                    "factory": getattr(factory, "__qualname__", type(factory).__qualname__)}
        source = inspect.getsourcefile(factory) if inspect.isfunction(factory) or inspect.isclass(factory) else None
        if source and Path(source).is_file():
            path = Path(source).resolve()
            identity["source"] = {"path": str(path), "sha256": sha256(path)}
        return renderer, identity


def _palette(style):
    colours = style.get("colours", ["#76d4cf", "#edb56d", "#c8a4f7", "#92b9ff"])
    if not isinstance(colours, list) or not 1 <= len(colours) <= 256:
        raise ValueError("colours must be a nonempty list of at most 256 CSS colour strings")
    if any(not isinstance(value, str) for value in colours):
        raise ValueError("Each colour must be a CSS colour string")
    return tuple(ImageColor.getcolor(value, "RGBA") for value in colours)


class _Viewport:
    def __init__(self, size, style, allowed):
        fields(style, set(allowed) | {"colours", "padding", "background"}, "renderer style")
        self.size, self.style = size, style
        self.colours = _palette(style)
        self.padding = number(style.get("padding", 4), "renderer padding", minimum=0)
        if self.padding * 2 + 2 > min(size):
            raise ValueError("Renderer padding leaves no drawable viewport")
        self.background = ImageColor.getcolor(style["background"], "RGBA") if "background" in style else (0, 0, 0, 0)

    def image(self):
        return Image.new("RGBA", self.size, self.background)

    def area(self, extra=0):
        margin = self.padding + extra
        if margin * 2 + 1 >= min(self.size):
            raise ValueError("Renderer strokes or markers do not fit the viewport")
        return (margin, margin, self.size[0] - 1 - margin, self.size[1] - 1 - margin)


class ParticleRenderer(_Viewport):
    """Seeded anchors moved only by supplied dimensions, with sampled trails."""
    def __init__(self, size, style):
        super().__init__(size, style, {"count", "radius", "amplitude", "seed", "trail", "lag"})
        self.count = integer(style.get("count", 24), "particle count", minimum=1, maximum=512)
        self.radius = number(style.get("radius", 3), "particle radius", minimum=.5, maximum=256)
        self.amplitude = number(style.get("amplitude", .7), "particle amplitude", minimum=0, maximum=1)
        self.trail = integer(style.get("trail", 4), "particle trail samples", minimum=1, maximum=32)
        self.lag = integer(style.get("lag", 1), "particle lag samples", maximum=32)
        rng = random.Random(integer(style.get("seed", 0), "particle seed"))
        self.anchors = tuple((rng.random(), rng.random()) for _ in range(self.count))
        self.area(self.radius)

    def render(self, frame):
        image = self.image()
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = self.area(self.radius)
        for j, anchor in enumerate(self.anchors):
            for age in reversed(range(min(self.trail, len(frame.history)))):
                index = max(0, len(frame.history) - 1 - j * self.lag - age)
                values = frame.history[index].values
                vx, vy = values[j % len(values)], values[(j + 1) % len(values)]
                x = left + (right - left) * (anchor[0] * (1 - self.amplitude) + (vx + 1) / 2 * self.amplitude)
                y = top + (bottom - top) * (anchor[1] * (1 - self.amplitude) + (1 - vy) / 2 * self.amplitude)
                colour = self.colours[j % len(self.colours)]
                colour = (*colour[:3], round(colour[3] / (age + 1)))
                r = self.radius
                draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)
        return image


class BarRenderer(_Viewport):
    def __init__(self, size, style):
        super().__init__(size, style, {"orientation", "gap", "negative_colour", "baseline_colour"})
        self.orientation = style.get("orientation", "vertical")
        if self.orientation not in {"vertical", "horizontal"}:
            raise ValueError("Bar orientation must be vertical or horizontal")
        self.gap = number(style.get("gap", 2), "bar gap", minimum=0)
        self.negative = ImageColor.getcolor(style.get("negative_colour", "#edb56d"), "RGBA")
        self.baseline = ImageColor.getcolor(style.get("baseline_colour", "#9ca4bc"), "RGBA")

    def render(self, frame):
        image = self.image()
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = self.area()
        vertical = self.orientation == "vertical"
        slot = ((right - left) if vertical else (bottom - top)) / len(frame.values)
        if slot - self.gap < 1:
            raise ValueError("Too many dimensions or too much bar gap for this viewport; select axes")
        for j, value in enumerate(frame.values):
            colour = self.colours[j % len(self.colours)] if value >= 0 else self.negative
            if vertical:
                mid = (top + bottom) / 2
                end = mid - value * (bottom - top) / 2
                box = (left + j * slot, min(mid, end), left + (j + 1) * slot - self.gap, max(mid, end))
            else:
                mid = (left + right) / 2
                end = mid + value * (right - left) / 2
                box = (min(mid, end), top + j * slot, max(mid, end), top + (j + 1) * slot - self.gap)
            draw.rectangle(box, fill=colour)
        draw.line((left, (top + bottom) / 2, right, (top + bottom) / 2) if vertical
                  else ((left + right) / 2, top, (left + right) / 2, bottom), fill=self.baseline)
        return image


class WaveformRenderer(_Viewport):
    """Time-domain rudiment traces; this is not PCM decoding or spectrum DSP."""
    def __init__(self, size, style):
        super().__init__(size, style, {"line_width", "layout", "baseline_colour"})
        self.line_width = integer(style.get("line_width", 2), "waveform line width", minimum=1, maximum=64)
        self.layout = style.get("layout", "stacked")
        if self.layout not in {"stacked", "overlay"}:
            raise ValueError("Waveform layout must be stacked or overlay")
        self.baseline = ImageColor.getcolor(style.get("baseline_colour", "#536078"), "RGBA")
        self.area(self.line_width / 2)

    def render(self, frame):
        image = self.image()
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = self.area(self.line_width / 2)
        lanes = len(frame.values) if self.layout == "stacked" else 1
        height = (bottom - top) / lanes
        if height < self.line_width + 1:
            raise ValueError("Too many waveform dimensions for this viewport; select axes")
        span = max(1, (frame.history_frames - 1) * frame.history_stride)
        for axis in range(len(frame.values)):
            middle = top + ((axis if self.layout == "stacked" else 0) + .5) * height
            draw.line((left, middle, right, middle), fill=self.baseline)
            points = [(right - (frame.context.frame - sample.context.frame) / span * (right - left),
                       middle - sample.values[axis] * height / 2) for sample in frame.history]
            colour = self.colours[axis % len(self.colours)]
            if len(points) >= 2:
                draw.line(points, fill=colour, width=self.line_width)
            else:
                draw.point(points[0], fill=colour)
        return image


class PathRenderer(_Viewport):
    def __init__(self, size, style):
        super().__init__(size, style, {"line_width", "radius"})
        self.line_width = integer(style.get("line_width", 2), "path line width", minimum=1, maximum=64)
        self.radius = number(style.get("radius", 3), "path marker radius", minimum=.5, maximum=256)
        self.area(max(self.radius, self.line_width / 2))

    def render(self, frame):
        if len(frame.values) < 2:
            raise ValueError("XY path needs at least two selected dimensions")
        image = self.image()
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = self.area(max(self.radius, self.line_width / 2))
        points = [(left + (sample.values[0] + 1) / 2 * (right - left),
                   top + (1 - sample.values[1]) / 2 * (bottom - top)) for sample in frame.history]
        if len(points) >= 2:
            draw.line(points, fill=self.colours[0], width=self.line_width)
        x, y = points[-1]
        draw.ellipse((x - self.radius, y - self.radius, x + self.radius, y + self.radius),
                     fill=self.colours[min(1, len(self.colours) - 1)])
        return image


class SpectrumRenderer(_Viewport):
    """Calibrated spectrum bars, peak holds, frequency labels and local maxima."""
    requires_spectrum = True

    def __init__(self, size, style):
        super().__init__(size, style, {"floor_db", "ceiling_db", "hold_seconds", "decay_db_per_second",
                                      "peak_count", "min_peak_db", "min_peak_contrast_db",
                                      "min_peak_distance", "gap", "peak_colour", "label_colour",
                                      "font_size", "labels"})
        self.floor = number(style.get("floor_db", -72), "spectrum floor", minimum=-240, maximum=0)
        self.ceiling = number(style.get("ceiling_db", 0), "spectrum ceiling", minimum=self.floor + .01, maximum=60)
        self.settings = dict(floor=self.floor, ceiling=self.ceiling,
                            hold_seconds=number(style.get("hold_seconds", .4), "peak hold seconds", minimum=0, maximum=60),
                            decay_db_per_second=number(style.get("decay_db_per_second", 24), "peak decay", minimum=.01, maximum=240),
                            peak_count=integer(style.get("peak_count", 3), "peak count", minimum=1, maximum=32),
                            min_peak_db=number(style.get("min_peak_db", -60), "peak threshold", minimum=-240, maximum=60),
                            min_peak_contrast_db=number(style.get("min_peak_contrast_db", 3), "adjacent peak contrast", minimum=0, maximum=240),
                            min_peak_distance=integer(style.get("min_peak_distance", 2), "peak separation", minimum=1, maximum=256))
        self.gap = number(style.get("gap", 1), "spectrum gap", minimum=0)
        self.labels = style.get("labels", True)
        if not isinstance(self.labels, bool):
            raise ValueError("Spectrum labels must be boolean")
        self.font_size = integer(style.get("font_size", max(6, min(14, size[0] // 30))), "spectrum font", minimum=6, maximum=128)
        self.peak_colour = ImageColor.getcolor(style.get("peak_colour", "#f5eddf"), "RGBA")
        self.label_colour = ImageColor.getcolor(style.get("label_colour", "#c4d1e7"), "RGBA")
        self.font = None
        self.frame_report = None

    def prepare(self, project):
        if self.labels:
            if not project.font_ref:
                raise ValueError("Spectrum frequency labels need a pinned project font or labels: false")
            from PIL import ImageFont
            self.font = ImageFont.truetype(project.font_ref["path"], self.font_size)

    @staticmethod
    def frequency(value):
        return f"{value / 1000:g}k" if value >= 1000 else f"{value:g}"

    def render(self, frame):
        report = analyse_spectrum(frame, **self.settings)
        self.frame_report = report
        image = self.image()
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = self.area(1)
        if self.labels:
            left += self.font_size * 2.5
            top += self.font_size + 6
            bottom -= self.font_size + 6
        if right <= left or bottom - top < 4:
            raise ValueError("Spectrum labels leave no plotting area; increase bounds or disable labels")
        slot = (right - left) / len(report["bands"])
        if slot - self.gap < 1:
            raise ValueError("Too many spectrum bands for this viewport; select axes or enlarge bounds")
        def y(db):
            return bottom - max(0, min(1, (db - self.floor) / (self.ceiling - self.floor))) * (bottom - top)
        for db in (self.floor, (self.floor + self.ceiling) / 2, self.ceiling):
            draw.line((left, y(db), right, y(db)), fill="#40516a")
            if self.labels:
                draw.text((self.padding, y(db) - self.font_size / 2), f"{db:g}", font=self.font, fill=self.label_colour)
        peak_set = {row["index"] for row in report["peaks"]}
        for j, band in enumerate(report["bands"]):
            x = left + j * slot
            draw.rectangle((x, y(band["display_dbfs"]), x + slot - self.gap, bottom),
                           fill=self.colours[j % len(self.colours)])
            draw.line((x, y(band["held_dbfs"]), x + slot - self.gap, y(band["held_dbfs"])),
                      fill=self.peak_colour, width=2)
            if j in peak_set:
                centre, height = x + slot / 2, y(band["display_dbfs"])
                draw.polygon(((centre - 3, max(top, height - 5)), (centre + 3, max(top, height - 5)),
                              (centre, height)), fill=self.peak_colour)
        if self.labels:
            for j in sorted({0, len(report["bands"]) // 2, len(report["bands"])}):
                hz = frame.spectrum_bands[j].low_hz if j < len(frame.spectrum_bands) else frame.spectrum_bands[-1].high_hz
                text = self.frequency(hz) + "Hz"
                bb = draw.textbbox((0, 0), text, font=self.font)
                x = min(right - (bb[2] - bb[0]), max(left, left + j * slot - (bb[2] - bb[0]) / 2))
                draw.text((x, bottom + 3), text, font=self.font, fill=self.label_colour)
            text = "Peaks:"
            for peak in report["peaks"]:
                candidate = text + f" {self.frequency(peak['low_hz'])}-{self.frequency(peak['high_hz'])}Hz"
                bb = draw.textbbox((0, 0), candidate, font=self.font)
                if bb[2] - bb[0] > right - left:
                    break
                text = candidate
            if not report["peaks"]:
                text += " none"
            draw.text((left, self.padding), text, font=self.font, fill=self.peak_colour)
        return image



def default_renderers():
    """An independent registry: extending it cannot alter another composition."""
    registry = RendererRegistry()
    for name, factory in (("particles", ParticleRenderer), ("bars", BarRenderer),
                          ("waveform", WaveformRenderer), ("path", PathRenderer), ("spectrum", SpectrumRenderer)):
        registry.register(name, factory)
    from .advanced_renderers import register_extended_renderers
    register_extended_renderers(registry)
    from .circular_renderers import register_circular_renderers
    register_circular_renderers(registry)
    from .effect_renderers import register_effect_renderers
    register_effect_renderers(registry)
    from .sprite_renderers import register_sprite_renderers
    register_sprite_renderers(registry)
    return registry


class RudimentLayer:
    def __init__(self, project, cfg, registry, context_at):
        fields(cfg, {"id", "type", "renderer", "motion", "bounds", "axes", "style", "history_frames",
                     "history_stride", "opacity", "start_seconds", "end_seconds", "cue_labels",
                     "visual", "modulations"}, "rudiment layer")
        self.project, self.cfg, self.context_at = project, cfg, context_at
        self.id = cfg["id"]
        self.bounds = rectangle(cfg.get("bounds", [0, 0, project.width, project.height]),
                                "rudiment bounds", project.width, project.height)
        if any(x != int(x) for x in self.bounds):
            raise ValueError("Rudiment bounds must be integer pixels")
        self.bounds = tuple(map(int, self.bounds))
        self.opacity = number(cfg.get("opacity", 1), "opacity", minimum=0, maximum=1)
        self.start = number(cfg.get("start_seconds", 0), "layer start", minimum=0)
        self.end = number(cfg["end_seconds"], "layer end", minimum=self.start) if "end_seconds" in cfg else float("inf")
        if "cue_labels" in cfg and (not isinstance(cfg["cue_labels"], list) or
                                    any(not isinstance(x, str) for x in cfg["cue_labels"])):
            raise ValueError("cue_labels must be a list of strings")
        self.motion = Motion(cfg["motion"], project.base, project.clock)
        project.inputs.extend(self.motion.files)
        dimensions = self.motion.value_dimensions
        axes = cfg.get("axes", list(dimensions))
        if not isinstance(axes, list) or not 1 <= len(axes) <= 256:
            raise ValueError("Select one to 256 rudiment axes")
        self.axes = []
        for axis in axes:
            index = dimensions.index(axis) if isinstance(axis, str) and axis in dimensions else axis
            self.axes.append(integer(index, "rudiment axis", maximum=len(dimensions) - 1))
        self.dimensions = tuple(dimensions[index] for index in self.axes)
        spectral_view = cfg["renderer"] == "spectrum" or (
            isinstance(cfg["renderer"], str) and cfg["renderer"].startswith(("spectrum-", "spectrogram-", "sprite-spectrum-", "sprite-spectrogram-")))
        self.history_frames = integer(cfg.get("history_frames", 256 if spectral_view else 64), "history frames", minimum=1, maximum=512)
        self.history_stride = integer(cfg.get("history_stride", 1), "history stride", minimum=1, maximum=120)
        from .visual_style import VisualStyle
        from .modulation import RudimentModulations
        self._registry = registry
        self._renderer_name = cfg["renderer"]
        self._size = (self.bounds[2] - self.bounds[0], self.bounds[3] - self.bounds[1])
        if not isinstance(cfg.get("visual", {}), dict):
            raise ValueError("visual must be a style object")
        self.visual_config = dict(cfg.get("visual", {}))
        self.visual_template = VisualStyle(self.visual_config)
        self.base_style = dict(cfg.get("style", {}))
        if self.visual_template.palette is not None:
            self.base_style["colours"] = ["#{:02x}{:02x}{:02x}{:02x}".format(*colour)
                                          for colour in self.visual_template.palette]
        if self._renderer_name.startswith("sprite-"):
            from .sprite_renderers import SpriteModulations
            self.modulations = SpriteModulations(project, cfg.get("modulations", []), context_at,
                                                 base_style=self.base_style, base_visual=self.visual_config)
        else:
            self.modulations = RudimentModulations(project, cfg.get("modulations", []), context_at)
        project.inputs.extend(self.modulations.files)
        self._styled_renderers = {}
        self._hires_renderers = {}
        self.renderer, identity = registry.create(cfg["renderer"], self._size, self.base_style)
        if "source" in identity:
            project.inputs.append(identity["source"])
        self.spectrum_bands = spectrum_bands(self.motion, self.axes) if getattr(self.renderer, "requires_spectrum", False) else ()
        if callable(getattr(self.renderer, "prepare", None)):
            self.renderer.prepare(project)
        self.frame_report = None
        self.evidence = {"renderer": identity, "dimensions": self.dimensions,
                         "history_frames": self.history_frames, "history_stride": self.history_stride,
                         "history_clock": "encoded frame; native source/continuous clock per motion",
                         "viewport": self.bounds, "style": cfg.get("style", {}),
                         "visual": self.visual_config, "modulations": self.modulations.evidence,
                         "clipping": "viewport", "config": cfg}

    def active(self, context):
        return (self.start <= context.source_seconds < self.end and
                ("cue_labels" not in self.cfg or context.cue and context.cue["text"] in self.cfg["cue_labels"]))

    @lru_cache(maxsize=2048)
    def sample(self, frame):
        context = self.context_at(frame)
        values = self.motion.values(context.source_seconds, continuous_beat=context.beat)
        selected = tuple(number(values[axis], "rudiment sample", minimum=-1, maximum=1) for axis in self.axes)
        return RudimentSample(context, selected)

    def data(self, context):
        indices = []
        for age in range(self.history_frames):
            n = context.frame - age * self.history_stride
            if n < 0 or not self.active(self.context_at(n)):
                break
            indices.append(n)
        history = tuple(self.sample(n) for n in reversed(indices))
        return RudimentFrame(context, self.dimensions, self.sample(context.frame).values,
                             history, self.history_frames, self.history_stride, self.spectrum_bands)

    def draw(self, image, context):
        self.frame_report = None
        if not self.active(context):
            return None
        from .visual_style import VisualStyle
        style, visual, modulation_report = self.modulations.apply(context, self.base_style, self.visual_config)
        renderer = self.renderer
        if style != self.base_style:
            key = repr(sorted(style.items()))
            renderer = self._styled_renderers.get(key)
            if renderer is None:
                renderer, _ = self._registry.create(self._renderer_name, self._size, style)
                if callable(getattr(renderer, "prepare", None)):
                    renderer.prepare(self.project)
                if len(self._styled_renderers) >= 16:
                    self._styled_renderers.clear()
                self._styled_renderers[key] = renderer
        scale = getattr(getattr(self.project, "output_quality", None), "vector_scale", 1)
        if scale > 1 and not self._renderer_name.startswith("sprite-"):
            from .super_sampling import vector_viewport
            viewport, report = vector_viewport(self._registry, self._renderer_name,
                self._size, style, self.data(context), scale, self.project, self._hires_renderers)
        else:
            viewport = renderer.render(self.data(context))
            report = getattr(renderer, "frame_report", None)
        expected = self._size
        if not isinstance(viewport, Image.Image) or viewport.mode != "RGBA" or viewport.size != expected:
            raise ValueError(f"Renderer {self.cfg['renderer']} must return viewport-sized RGBA image")
        if modulation_report:
            report = dict(report, modulations=modulation_report) if report is not None else dict(
                kind="rudiment-visual-modulation", modulations=modulation_report)
        self.frame_report = report
        if visual:
            viewport = VisualStyle(visual).apply(viewport, getattr(renderer, "background", (0, 0, 0, 0)))
        viewport = alpha_scaled(viewport, self.opacity)
        bounds = transformed_bounds(viewport, self.bounds[0], self.bounds[1])
        image.alpha_composite(viewport, self.bounds[:2])
        return bounds
