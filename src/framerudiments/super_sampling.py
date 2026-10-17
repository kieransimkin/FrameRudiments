"""Real high-resolution source rendering and subframe integrations.

Pinned font outlines and procedural vector geometry are rasterised above output
resolution. Source time subframes are rendered by an independent high-fps
compositor, not synthesized by blending repeated ordinary frames. Output timing,
recording identities and original cue/word timings remain authoritative.
"""
from copy import copy, deepcopy
from functools import lru_cache
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .quality_effects import linear_shutter_composite


# These are physical pixel dimensions, not amplitudes, tempo or the number of
# spectrum/Rudiment channels. Unknown style keys remain untouched.
_PIXEL_STYLE = frozenset((
    "line_width", "padding", "font_size", "sprite_size", "dot_size", "bar_gap",
    "stroke_width", "peak_marker_size", "point_size", "grid_width",
))


def vector_viewport(renderer_registry, name, size, style, frame, scale, project=None, cache=None):
    """Make a genuine high-res vector viewport, then reduce to encoded pixels.

    Sprite-backed renderer names are deliberately excluded by the caller, to
    retain authored bitmap pixel edges and checksum-pinned source texels.
    """
    if scale == 1:
        view, _ = renderer_registry.create(name, size, style)
        if project is not None and callable(getattr(view, "prepare", None)):
            view.prepare(project)
        return view.render(frame), getattr(view, "frame_report", None)
    high_style = dict(style)
    for key in _PIXEL_STYLE & high_style.keys():
        value = high_style[key]
        if type(value) in (int, float):
            high_style[key] = max(1, round(value * scale))
    scaled_size = (size[0] * scale, size[1] * scale)
    key = (name, scaled_size, repr(sorted(high_style.items())))
    high = cache.get(key) if cache is not None else None
    if high is None:
        high, _ = renderer_registry.create(name, scaled_size, high_style)
        if project is not None and callable(getattr(high, "prepare", None)):
            high.prepare(project)
        if cache is not None:
            if len(cache) >= 16:
                cache.clear()
            cache[key] = high
    source = high.render(frame)
    if not isinstance(source, Image.Image) or source.mode != "RGBA" or source.size != scaled_size:
        raise ValueError("Supersampled vector must render a full-size RGBA viewport")
    return source.resize(size, Image.Resampling.LANCZOS), getattr(high, "frame_report", None)


class _ScaledText:
    """Pillow layout adapter: same word wraps, larger real TrueType outlines."""

    def __init__(self, original, scale, crop_x, crop_y):
        self.original = original
        self.factor = scale
        self.box = tuple(round((v - (crop_x if i % 2 == 0 else crop_y)) * scale)
                         for i, v in enumerate(original.box))
        self.stroke = original.stroke * scale
        self.spacing = original.spacing * scale
        self.maximum = original.maximum * scale
        self.minimum = original.minimum * scale
        self.font_ref = original.font_ref
        self.colour = original.colour
        self.config = original.config

    @lru_cache(maxsize=64)
    def layout(self, text):
        base_font, rows, _, _ = self.original.layout(text)
        font = ImageFont.truetype(self.font_ref["path"], base_font.size * self.factor)
        draw = ImageDraw.Draw(Image.new("L", (1, 1)))
        boxes = tuple(draw.textbbox((0, 0), row, font=font, stroke_width=self.stroke) for row in rows)
        height = sum(box[3] - box[1] for box in boxes) + self.spacing * (len(boxes) - 1)
        return font, rows, boxes, height

    def draw(self, image, text, *, colour=None):
        if not text:
            return []
        font, rows, boxes, height = self.layout(text)
        left, top, right, bottom = self.box
        draw = ImageDraw.Draw(image)
        if panel := self.config.get("panel"):
            draw.rounded_rectangle(self.box, radius=12 * self.factor, fill=panel)
        y = top + (bottom - top - height) / 2
        bounds = []
        for row, bb in zip(rows, boxes):
            x = left + (right - left - (bb[2] - bb[0])) / 2 - bb[0]
            draw.text((x, y - bb[1]), row, font=font, fill=colour or self.colour,
                      stroke_width=self.stroke, stroke_fill="#12111c")
            bounds.append((x + bb[0], y, x + bb[2], y + bb[3] - bb[1]))
            y += bb[3] - bb[1] + self.spacing
        return bounds


def _high_rich(appearance, factor):
    """Copy bounded glyph effects and scale their geometric pixel dimensions."""
    high = copy(appearance)
    for prop, keys in (("stroke", ("width",)), ("bevel", ("depth",)),
                       ("emboss", ("depth",)), ("inner", ("radius",)),
                       ("outer", ("radius",)), ("shadow", ("dx", "dy", "blur"))):
        data = dict(getattr(high, prop))
        for key in keys:
            data[key] *= factor
        setattr(high, prop, data)
    return high


def supersampled_text(block, image, text, *, colour=None, active=None, completed=()):
    """Supersample text only, not an entire 4K canvas for each subtitle call.

    Cropping includes the bounded halo for shadows/glow. Return ordinary output
    coordinate bounds for existing compositor/QA and word tracking.
    """
    if not text:
        return []
    factor = getattr(getattr(block.project, "output_quality", None), "vector_scale", 1)
    if factor <= 1:
        if block.rich is not None:
            return block.rich.draw(image, block, text, colour=colour, active=active, completed=completed)
        if active is not None:
            from .word_timing import draw_highlighted_line
            return draw_highlighted_line(block, image, text, active=active, completed=completed,
                                         colour=block.config.get("word_colour", "#ffe082"),
                                         completed_colour=block.config.get("completed_colour", "#9ad9d5"))
        return block._draw_native(image, text, colour=colour)
    left, top, right, bottom = block.box
    halo = 16 + block.stroke
    if block.rich is not None:
        appearance = block.rich
        halo = min(192, max(halo, appearance.outer["radius"] * 4,
                            appearance.shadow["blur"] * 4 + max(abs(appearance.shadow["dx"]),
                                                                  abs(appearance.shadow["dy"]))))
    x0, y0 = max(0, math.floor(left - halo)), max(0, math.floor(top - halo))
    x1, y1 = min(image.width, math.ceil(right + halo)), min(image.height, math.ceil(bottom + halo))
    high = Image.new("RGBA", ((x1 - x0) * factor, (y1 - y0) * factor))
    scaled = _ScaledText(block, factor, x0, y0)
    if block.rich is not None:
        bounds = _high_rich(block.rich, factor).draw(high, scaled, text, colour=colour,
                                                     active=active, completed=completed)
    elif active is not None:
        from .word_timing import draw_highlighted_line
        bounds = draw_highlighted_line(scaled, high, text, active=active, completed=completed,
                                       colour=block.config.get("word_colour", "#ffe082"),
                                       completed_colour=block.config.get("completed_colour", "#9ad9d5"))
    else:
        bounds = scaled.draw(high, text, colour=colour)
    reduced = high.resize((x1-x0, y1-y0), Image.Resampling.LANCZOS)
    image.alpha_composite(reduced, (x0, y0))
    return [tuple(round((value / factor + (x0 if i % 2 == 0 else y0)), 3)
                  for i, value in enumerate(rect)) for rect in bounds]


def _interpolate_native_motion(value):
    if isinstance(value, dict):
        # Add it only for native procedural motion; never override explicit off,
        # and never reinterpret recorded/rerecorded audio frame timing.
        if value.get("kind") in {"named", "table", "match"}:
            value.setdefault("interpolate", True)
        for nested in value.values():
            _interpolate_native_motion(nested)
    elif isinstance(value, list):
        for nested in value:
            _interpolate_native_motion(nested)


def _virtual_project(original, factor):
    """Derive a private render-time project at higher FPS, without time stretch."""
    virtual = copy(original)
    virtual.data = deepcopy(original.data)
    virtual.inputs = list(original.inputs)
    virtual.fps = original.fps * factor
    virtual.frame_count = math.ceil(original.duration * virtual.fps)
    virtual.output_quality = original.output_quality.virtual_quality()
    virtual._time_sampling_factor = factor
    _interpolate_native_motion(virtual.data.get("layers", []))
    _interpolate_native_motion(virtual.data.get("word_indicator", {}))
    _interpolate_native_motion(virtual.data.get("post_effects", {}))
    # Avoid applying historical encoded-frame shutter to each subframe. The
    # source compositor integrates the shutter after these subframes if asked.
    virtual.data.get("post_effects", {}).pop("temporal", None)
    # Index-based animation times are expressed in original output frames;
    # scale the time durations to keep the original seconds per transition.
    indicator = virtual.data.get("word_indicator")
    if isinstance(indicator, dict):
        indicator["travel_frames"] = indicator.get("travel_frames", 8) * factor
        particles = indicator.setdefault("particles", {})
        particles["lifetime_frames"] = particles.get("lifetime_frames", 35) * factor
        particles["speed"] = particles.get("speed", 1.7) / factor
        particles["wind"] = particles.get("wind", 0) / (factor * factor)
        particles["gravity"] = particles.get("gravity", 0) / (factor * factor)
        for kind in ("on_word", "on_cue"):
            profile = particles.setdefault(kind, {})
            profile["duration_frames"] = profile.get("duration_frames", 12) * factor
    for layer in virtual.data.get("layers", []):
        if layer.get("type") == "rudiment":
            layer["history_stride"] = min(120, layer.get("history_stride", 1) * factor)
    # Lag and smoothing are measured in encoded-frame counts, not seconds.
    # Maintain their original wall-clock duration for the high-FPS compositor.
    def scaled_modulations(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                if key == "modulations" and isinstance(inner, list):
                    for row in inner:
                        if isinstance(row, dict):
                            if "lag_frames" in row and type(row["lag_frames"]) is int:
                                row["lag_frames"] = min(600, row["lag_frames"] * factor)
                            original_smoothing = row.get("smoothing_frames", 0)
                            original_stride = row.get("sample_stride", 1)
                            if type(original_smoothing) is int and original_smoothing > 0:
                                row["smoothing_frames"] = min(120, original_smoothing * factor)
                                wanted_span = original_smoothing * original_stride * factor
                                row["sample_stride"] = min(60, max(1, math.ceil(
                                    wanted_span / row["smoothing_frames"])))
                else:
                    scaled_modulations(inner)
        elif isinstance(value, list):
            for inner in value:
                scaled_modulations(inner)
    scaled_modulations(virtual.data)
    from .timeline import parse_lrc, parse_cue
    from .word_timing import parse_word_lrc, word_frame_ledger, assign_words_to_lines
    virtual.lyrics = virtual.ledger(parse_lrc(original.lyric_ref["path"])) if original.lyric_ref else []
    if "cues" in original.data:
        raw = virtual.ref(original.data["cues"])
        reader = parse_cue if Path(raw["path"]).suffix.lower() == ".cue" else parse_lrc
        events = reader(raw["path"]) if reader is parse_cue else reader(raw["path"], lyrics=False)
        virtual.cues = virtual.ledger(events)
    else:
        virtual.cues = []
    if original.word_ref:
        virtual.words = word_frame_ledger(parse_word_lrc(original.word_ref["path"]),
                                          start_sample=virtual.start_sample, end_sample=virtual.end_sample,
                                          sample_rate=virtual.sample_rate, fps=virtual.fps,
                                          repeats=virtual.repeats, frame_count=virtual.frame_count)
        if virtual.words and virtual.lyrics:
            assign_words_to_lines(virtual.words, virtual.lyrics)
    else:
        virtual.words = []
    return virtual


def temporal_supersample(compositor, frame):
    """Integrate true source-clock subframes into an ordinary output frame.

    Works at random-access frames, including word/cue events and variable BPM.
    The optional projectM/Butterchurn engines remain at their native *output*
    frame rate and are never invoked multiple times per encoded frame.
    """
    factor = compositor.output_quality.time_samples
    if factor == 1:
        raise ValueError("Temporal supersampling called with one sample")
    virtual = getattr(compositor, "_temporal_virtual", None)
    if virtual is None:
        from .compositor import Compositor
        project = _virtual_project(compositor.project, factor)
        scene = compositor.scene
        if scene is not None:
            # Source clock remains fractional; original integer frame indices
            # retain their meaning for trusted custom scene hooks.
            from dataclasses import replace

            def scene_adapter(image, context):
                return scene(image, replace(context, frame=context.frame // factor))
        else:
            scene_adapter = None
        virtual = Compositor(project, scene=scene_adapter, renderers=compositor.renderers)
        compositor._temporal_virtual = virtual
    images = []
    for j in range(factor):
        sample, _ = virtual.frame(min(frame * factor + j, virtual.project.frame_count - 1),
                                  apply_quality=False)
        images.append(sample)
    # Source samples are genuinely different instants in the virtual clock.
    combined = linear_shutter_composite(images, [1] * factor, strength=1)
    compositor.extents.update(virtual.extents)
    for word, status in virtual.text_states.items():
        output_status = deepcopy(status)
        if "first_frame" in output_status:
            output_status["first_frame"] = output_status["first_frame"] // factor
        compositor.text_states.setdefault(word, output_status)
    compositor.post_pipeline.last_report = deepcopy(virtual.post_pipeline.last_report)
    reports = deepcopy(virtual.frame_reports())
    for report in reports.values():
        if isinstance(report, dict) and "frame" in report:
            report["subframe_index"] = report["frame"]
            report["frame"] = frame
    compositor._supersampled_reports = reports
    return combined, compositor.context(frame)
