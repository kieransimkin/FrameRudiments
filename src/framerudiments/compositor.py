"""Pillow scene composition; per-release artwork and appearance stay declarative."""
from dataclasses import dataclass
from functools import lru_cache
import math

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

from .effects import alpha_scaled, transformed_bounds, within, directional_trail, atlas_cell
from .io import fields, number, integer, rectangle
from .rudiment_renderers import RudimentLayer, default_renderers
from .particle_engine import ParticleEmitter
from .motion import Motion
from .timeline import EventTrack
from .word_timing import WordTrack
from .rich_subtitles import RichTextAppearance
from .lyric_indicator import WordIndicator
from .visualisers import effect_specs
from .scoped_effects import CompositingEffects


@dataclass(frozen=True)
class FrameContext:
    frame: int
    output_seconds: float
    source_seconds: float
    beat: float
    lyric: dict | None
    cue: dict | None
    word: dict | None = None


class TextBlock:
    def __init__(self, project, config, *, label):
        fields(config, {"box", "font_size", "min_font_size", "colour", "panel", "stroke", "mode",
                        "line_spacing", "neighbour_colour", "word_mode", "word_colour",
                        "completed_colour", "font", "effects"}, label)
        self.project, self.config, self.label = project, config, label
        self.box = rectangle(config["box"], label, project.width, project.height)
        self.maximum = integer(config.get("font_size", 60), "font size", minimum=1, maximum=512)
        self.minimum = integer(config.get("min_font_size", self.maximum), "min font size", minimum=1,
                               maximum=self.maximum)
        self.spacing = integer(config.get("line_spacing", 8), "line spacing", maximum=512)
        self.stroke = integer(config.get("stroke", 1), "text stroke", maximum=20)
        self.font_ref = project.ref(config["font"]) if "font" in config else project.font_ref
        if not self.font_ref:
            raise ValueError("Text needs an explicit pinned font")
        self.rich = RichTextAppearance(config["effects"]) if "effects" in config else None
        self.colour = config.get("colour", "#f5eddf")
        self.mode = config.get("mode", "current")
        if self.mode not in {"current", "neighbours"}:
            raise ValueError("Caption mode must be current or neighbours")
        self.word_mode = config.get("word_mode", "off")
        if self.word_mode not in {"off", "highlight", "progressive", "current"}:
            raise ValueError("word_mode must be off/highlight/progressive/current")
        if self.word_mode != "off" and self.mode == "neighbours":
            raise ValueError("Word highlighting is only supported with captions.mode=current")
        for key in ("word_colour", "completed_colour"):
            value = config.get(key, "#ffe082" if key == "word_colour" else "#9ad9d5")
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a CSS colour")
            ImageColor.getcolor(value, "RGBA")

    @lru_cache(maxsize=256)
    def layout(self, text):
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        left, top, right, bottom = self.box
        for size in range(self.maximum, self.minimum - 1, -1):
            font = ImageFont.truetype(self.font_ref["path"], size)
            rows = []
            for paragraph in text.split("\n"):
                line = ""
                for word in paragraph.split():
                    trial = (line + " " + word).strip()
                    bb = draw.textbbox((0, 0), trial, font=font, stroke_width=self.stroke)
                    if bb[2] - bb[0] > right - left and line:
                        rows.append(line)
                        line = word
                    else:
                        line = trial
                rows.append(line)
            boxes = [draw.textbbox((0, 0), row, font=font, stroke_width=self.stroke) for row in rows]
            height = sum(bb[3] - bb[1] for bb in boxes) + self.spacing * (len(rows) - 1)
            if height <= bottom - top and all(bb[2] - bb[0] <= right - left for bb in boxes):
                return font, tuple(rows), tuple(boxes), height
        raise ValueError(f"{self.label} cannot fit without clipping: {text!r}")

    def draw(self, image, text, *, colour=None):
        if not text:
            return []
        if getattr(getattr(self.project, "output_quality", None), "vector_scale", 1) > 1:
            from .super_sampling import supersampled_text
            return supersampled_text(self, image, text, colour=colour)
        return self._draw_native(image, text, colour=colour)

    def draw_styled(self, image, text, *, active=None, completed=()):
        from .super_sampling import supersampled_text
        return supersampled_text(self, image, text, active=active, completed=completed)

    def _draw_native(self, image, text, *, colour=None):
        if not text:
            return []
        if self.rich is not None:
            return self.rich.draw(image, self, text, colour=colour)
        font, rows, boxes, height = self.layout(text)
        left, top, right, bottom = self.box
        draw = ImageDraw.Draw(image)
        if panel := self.config.get("panel"):
            draw.rounded_rectangle(self.box, radius=12, fill=panel)
        y = top + (bottom - top - height) / 2
        bounds = []
        for row, bb in zip(rows, boxes):
            x = left + (right - left - (bb[2] - bb[0])) / 2 - bb[0]
            actual = (x + bb[0], y, x + bb[2], y + bb[3] - bb[1])
            if not within(actual, self.box):
                raise ValueError(f"Text outside {self.label} box")
            draw.text((x, y - bb[1]), row, font=font, fill=colour or self.colour,
                      stroke_width=self.stroke, stroke_fill="#12111c")
            bounds.append([round(v, 3) for v in actual])
            y += bb[3] - bb[1] + self.spacing
        return bounds


class Sprite:
    def __init__(self, project, cfg):
        fields(cfg, {"id", "type", "asset", "center", "width", "bounds", "crop_alpha", "motion", "mapping",
                     "atlas", "opacity", "trail", "start_seconds", "end_seconds", "cue_labels"}, "layer")
        self.project, self.cfg = project, cfg
        self.id = cfg["id"]
        self.kind = cfg.get("type", "sprite")
        if self.kind not in {"sprite", "bars"}:
            raise ValueError("Supported declarative layers: sprite and bars; use scene hook for custom drawing")
        self.bounds = rectangle(cfg.get("bounds", [0, 0, project.width, project.height]),
                                "layer bounds", project.width, project.height)
        self.opacity = number(cfg.get("opacity", 1), "opacity", minimum=0, maximum=1)
        self.motion = Motion(cfg["motion"], project.base, project.clock) if "motion" in cfg else None
        if self.motion:
            project.inputs.extend(self.motion.files)
        mapping = cfg.get("mapping", {})
        fields(mapping, {"dx", "dy", "rotation", "scale", "x_axis", "y_axis", "rotation_axis", "scale_axis"}, "appearance mapping")
        self.mapping = {k: number(mapping.get(k, 0), k) for k in ("dx", "dy", "rotation", "scale")}
        self.axes = {key: integer(mapping.get(key, default), key, maximum=2)
                     for key, default in (("x_axis", 0), ("y_axis", 1), ("rotation_axis", 2), ("scale_axis", 2))}
        if abs(self.mapping["scale"]) >= 1:
            raise ValueError("Scale amplitude must keep the sprite's size positive")
        if self.kind == "bars":
            if not self.motion or not self.motion.recording:
                raise ValueError("Bars require a native N-dimensional recording")
            self.image = None
            return
        self.asset = project.ref(cfg["asset"])
        with Image.open(self.asset["path"]) as image:
            self.image = image.convert("RGBA")
        if cfg.get("crop_alpha", False):
            box = self.image.getchannel("A").getbbox()
            if box is None:
                raise ValueError("Empty sprite")
            self.image = self.image.crop(box)
        self.width = integer(cfg["width"], "sprite width", minimum=1, maximum=16384)
        self.center = tuple(number(x, "sprite center") for x in cfg["center"])
        if len(self.center) != 2:
            raise ValueError("Sprite center needs two coordinates")
        self.atlas = cfg.get("atlas")
        if self.atlas:
            fields(self.atlas, {"columns", "rows", "count", "cycle_ticks", "phase_beats"}, "atlas")
            cols = integer(self.atlas["columns"], "atlas columns", minimum=1)
            rows = integer(self.atlas["rows"], "atlas rows", minimum=1)
            integer(self.atlas["count"], "atlas count", minimum=1, maximum=cols * rows)
            number(self.atlas["cycle_ticks"], "atlas cycle ticks", minimum=.0001)
            number(self.atlas.get("phase_beats", 0), "atlas phase")
            atlas_cell(self.image, columns=cols, rows=rows, index=0)

    @lru_cache(maxsize=16)
    def transformed(self, index, width, rotation):
        im = self.image
        if self.atlas:
            im = atlas_cell(im, columns=self.atlas["columns"], rows=self.atlas["rows"], index=index)
        im = im.resize((width, max(1, round(im.height * width / im.width))), Image.Resampling.LANCZOS)
        im = im.rotate(rotation, Image.Resampling.BICUBIC, expand=True)
        if trail := self.cfg.get("trail"):
            fields(trail, {"offset", "opacity", "radius"}, "trail")
            im = directional_trail(im, **trail)
        return alpha_scaled(im, self.opacity)

    def draw(self, image, ctx):
        if not self.cfg.get("start_seconds", 0) <= ctx.source_seconds < self.cfg.get("end_seconds", math.inf):
            return None
        if "cue_labels" in self.cfg and (not ctx.cue or ctx.cue["text"] not in self.cfg["cue_labels"]):
            return None
        if self.kind == "bars":
            values = self.motion.recording_values(ctx.source_seconds)
            left, top, right, bottom = self.bounds
            draw = ImageDraw.Draw(image)
            mid = (top + bottom) / 2
            draw.line((left, mid, right, mid), fill="#9ca4bc", width=1)
            width = (right - left) / len(values)
            if width < 2:
                raise ValueError("Too many recording dimensions for the selected bar width")
            for j, value in enumerate(values):
                end = mid - value * (bottom - top) / 2
                draw.rectangle((left + j * width, min(mid, end), left + (j + 1) * width - 1,
                                max(mid, end)), fill="#76d4cf" if value >= 0 else "#edb56d")
            return self.bounds
        v = self.motion.sample(ctx.source_seconds, continuous_beat=ctx.beat) if self.motion else (0, 0, 0)
        index = 0
        if self.atlas:
            phase = (ctx.beat + self.atlas.get("phase_beats", 0)) * 16 / self.atlas["cycle_ticks"]
            index = math.floor((phase % 1) * self.atlas["count"])
        width = max(1, round(self.width * (1 + v[self.axes["scale_axis"]] * self.mapping["scale"])))
        im = self.transformed(index, width, round(v[self.axes["rotation_axis"]] * self.mapping["rotation"], 3))
        x = round(self.center[0] + v[self.axes["x_axis"]] * self.mapping["dx"] - im.width / 2)
        y = round(self.center[1] + v[self.axes["y_axis"]] * self.mapping["dy"] - im.height / 2)
        bounds = transformed_bounds(im, x, y)
        if not within(bounds, self.bounds):
            raise ValueError(f"Frame {ctx.frame}: {self.id} transformed alpha {bounds} exceeds {self.bounds}")
        image.alpha_composite(im, (x, y))
        return bounds


class Compositor:
    def __init__(self, project, *, scene=None, renderers=None):
        self.project, self.scene = project, scene
        self.output_quality = project.output_quality
        self.effect_specs = effect_specs(project)
        bg = project.data.get("background", {})
        fields(bg, {"colour", "image", "fit"}, "background")
        self.background = Image.new("RGBA", (project.width, project.height), bg.get("colour", "#101b2e"))
        if "image" in bg:
            ref = project.ref(bg["image"])
            with Image.open(ref["path"]) as source:
                source = source.convert("RGBA")
            fit = bg.get("fit", "cover")
            if fit == "cover":
                source = ImageOps.fit(source, self.background.size, method=Image.Resampling.LANCZOS)
            elif fit == "contain":
                source = ImageOps.contain(source, self.background.size, method=Image.Resampling.LANCZOS)
            else:
                raise ValueError("Background fit must be cover or contain")
            self.background.alpha_composite(source, ((project.width - source.width) // 2,
                                                       (project.height - source.height) // 2))
        self.lyrics, self.cues = EventTrack(project.lyrics), EventTrack(project.cues)
        self.words = WordTrack(project.words) if project.words else None
        self.renderers = default_renderers() if renderers is None else renderers
        self.layers = [
            RudimentLayer(project, cfg, self.renderers, self.context)
            if cfg.get("type") == "rudiment" else
            ParticleEmitter(project, cfg, self.context)
            if cfg.get("type") == "emitter" else Sprite(project, cfg)
            for cfg in project.data.get("layers", [])
        ]
        ids = [x.id for x in self.layers]
        if len(set(ids)) != len(ids):
            raise ValueError("Layer ids must be unique")
        caption_config = dict(project.data.get("captions", {}))
        if project.word_ref and caption_config and "word_mode" not in caption_config:
            caption_config["word_mode"] = "highlight" if project.lyrics else "current"
        self.caption = TextBlock(project, caption_config, label="captions") if caption_config and (
            "lyrics" in project.data or project.word_ref) else None
        self.word_caption = (TextBlock(project, project.data["word_captions"], label="word captions")
                             if "word_captions" in project.data else None)
        self.word_indicator = (WordIndicator(project, project.data["word_indicator"], self.context, self.caption)
                               if "word_indicator" in project.data else None)
        self.lyric_art = {}
        self.lyric_art_fallback = "text"
        if art := project.data.get("lyric_art"):
            fields(art, {"items", "placement", "fallback"}, "lyric art")
            self.lyric_art_fallback = art.get("fallback", "error")
            if self.lyric_art_fallback not in {"text", "error"}:
                raise ValueError("Lyric art fallback must be text or error")
            for i, item in enumerate(art["items"]):
                fields(item, {"text", "asset"}, "lyric art item")
                if item["text"] in self.lyric_art:
                    raise ValueError("Duplicate lyric art text mapping")
                self.lyric_art[item["text"]] = Sprite(project, dict(art["placement"],
                                                                  id=f"lyric-art-{i}", asset=item["asset"]))
            if self.lyric_art_fallback == "error":
                missing = {e["text"] for e in project.lyrics if e["text"]} - set(self.lyric_art)
                if missing:
                    raise ValueError(f"Missing lyric artwork: {sorted(missing)}")
        self.identity = []
        for item in project.data.get("identity", []):
            fields(item, {"text", "style"}, "identity")
            self.identity.append((item["text"], TextBlock(project, item["style"], label="identity")))
        self.extents, self.text_states = {}, {}
        targets = {"background", *(f"layer:{layer.id}" for layer in self.layers),
                   *(f"identity:{i}" for i in range(len(self.identity))),
                   *(f"lyric-art:{layer.id.rsplit('-', 1)[-1]}" for layer in self.lyric_art.values())}
        if self.scene:
            targets.add("scene")
        if self.caption:
            targets.add("caption")
        if self.word_caption:
            targets.add("word-caption")
        if self.word_indicator:
            targets.add("word-indicator")
        self.post_pipeline = CompositingEffects(project, project.data.get("post_effects", {}),
                                                self.context, valid_elements=targets)

    def context(self, frame):
        p = self.project
        lyric = self.lyrics.at(frame)
        word = self.words.at(frame, lyric if p.lyrics else None) if self.words else None
        return FrameContext(frame, frame / p.fps, p.source_time(frame), p.continuous_beat(frame),
                            lyric, self.cues.at(frame), word)

    def _caption_draw(self, image, text, ctx):
        if not self.words or self.caption.word_mode == "off":
            return self.caption.draw(image, text)
        state = self.words.state(ctx.frame, ctx.lyric if self.project.lyrics else None)
        if self.caption.word_mode == "current":
            return self.caption.draw(image, state["text"])
        if self.caption.rich is not None:
            return self.caption.draw_styled(
                image, text, active=state["active"],
                completed=state["completed"] if self.caption.word_mode == "progressive" else ())
        return self.caption.draw_styled(
            image, text, active=state["active"],
            completed=state["completed"] if self.caption.word_mode == "progressive" else ())

    def _word_caption_draw(self, image, ctx):
        state = self.words.state(ctx.frame, ctx.lyric if self.project.lyrics else None)
        return self.word_caption.draw(image, state["text"])

    def _frame_with_post_effects(self, ctx):
        """Render isolated RGBA elements, then the final frame, in draw order.

        Untargeted elements are still drawn onto transparent scratch surfaces
        when any post pipeline exists. This leaves neighbouring pixels untouched
        and ensures consistent alpha-over semantics across every element type.
        """
        pipeline = self.post_pipeline
        pipeline.begin()
        image = pipeline.apply_element(self.background.copy(), "background", ctx)

        if self.scene:
            if pipeline.affects("scene"):
                # A scene hook with isolated FX paints onto transparent RGBA.
                # Untargeted hooks keep their original composited-background API.
                scene_ink = Image.new("RGBA", self.background.size)
                scene_ink = self.scene(scene_ink, ctx)
                if scene_ink.size != self.background.size or scene_ink.mode != "RGBA":
                    raise ValueError("Custom scene hook must return frame-sized RGBA")
                image.alpha_composite(pipeline.apply_element(scene_ink, "scene", ctx))
            else:
                image = self.scene(image, ctx)
                if image.size != self.background.size or image.mode != "RGBA":
                    raise ValueError("Custom scene hook must return frame-sized RGBA")

        def paint(key, render):
            scratch = Image.new("RGBA", self.background.size)
            bounds = render(scratch)
            if not bounds:
                return None
            scratch = pipeline.apply_element(scratch, key, ctx)
            actual = scratch.getchannel("A").getbbox()
            image.alpha_composite(scratch)
            return actual

        for layer in self.layers:
            bounds = paint(f"layer:{layer.id}", lambda canvas, layer=layer: layer.draw(canvas, ctx))
            if bounds:
                previous = self.extents.get(layer.id, bounds)
                self.extents[layer.id] = [min(bounds[0], previous[0]), min(bounds[1], previous[1]),
                                         max(bounds[2], previous[2]), max(bounds[3], previous[3])]

        text_bounds = []
        for index, (text, style) in enumerate(self.identity):
            def draw_identity(canvas, text=text, style=style):
                boxes = style.draw(canvas, text)
                return boxes or None
            bounds = paint(f"identity:{index}", draw_identity)
            if bounds:
                text_bounds.append(bounds)

        if self.caption:
            text = ctx.lyric["text"] if ctx.lyric else ""
            art_layer = self.lyric_art.get(text)
            if art_layer:
                index = art_layer.id.rsplit("-", 1)[-1]
                bounds = paint(f"lyric-art:{index}", lambda canvas: art_layer.draw(canvas, ctx))
                if bounds:
                    text_bounds.append(bounds)
            elif self.caption.mode == "neighbours":
                before, current, after = self.lyrics.neighbours(ctx.frame)
                text = "\n".join((before["text"] if before else "", current["text"] if current else "",
                                  after["text"] if after else ""))
            if not art_layer or self.word_indicator:
                def draw_caption(canvas):
                    return self._caption_draw(canvas, text, ctx) or None
                bounds = paint("caption", draw_caption)
                if bounds:
                    text_bounds.append(bounds)
            if self.word_indicator:
                bounds = paint("word-indicator", lambda canvas: self.word_indicator.draw(canvas, ctx))
                if bounds:
                    text_bounds.append(bounds)
            if self.word_caption and self.words:
                bounds = paint("word-caption", lambda canvas: self._word_caption_draw(canvas, ctx) or None)
                if bounds:
                    text_bounds.append(bounds)
            self.text_states.setdefault(text, dict(first_frame=ctx.frame, bounds=text_bounds))
        elif self.word_caption and self.words:
            paint("word-caption", lambda canvas: self._word_caption_draw(canvas, ctx) or None)

        return pipeline.apply_frame(image, ctx)

    def _final_output(self, image, context, apply_quality):
        # Historical shutter samples must not receive a separate AA pass.
        if apply_quality and not getattr(self, "_temporal_sampling", False):
            image = self.output_quality.apply(image)
        return image, context

    def frame(self, frame, *, apply_quality=True):
        if self.post_pipeline.temporal is not None and not getattr(self, "_temporal_sampling", False):
            from .quality_effects import render_temporal_frame
            image, context = render_temporal_frame(self, frame)
            return self._final_output(image, context, apply_quality)
        if self.output_quality.time_samples > 1:
            from .super_sampling import temporal_supersample
            image, context = temporal_supersample(self, frame)
            return self._final_output(image, context, apply_quality)
        ctx = self.context(frame)
        if self.post_pipeline.enabled:
            # Final-frame opacity/rotation may leave transparent pixels. The
            # encoded RGB output uses a deterministic black matte, never an
            # implicit alpha-discard that would silently erase opacity.
            matte = Image.new("RGBA", self.background.size, "#000000")
            matte.alpha_composite(self._frame_with_post_effects(ctx))
            return self._final_output(matte.convert("RGB"), ctx, apply_quality)
        image = self.background.copy()
        if self.scene:
            image = self.scene(image, ctx)
            if image.size != self.background.size or image.mode != "RGBA":
                raise ValueError("Custom scene hook must return frame-sized RGBA")
        for layer in self.layers:
            bounds = layer.draw(image, ctx)
            if bounds:
                previous = self.extents.get(layer.id, bounds)
                self.extents[layer.id] = [min(bounds[0], previous[0]), min(bounds[1], previous[1]),
                                         max(bounds[2], previous[2]), max(bounds[3], previous[3])]
        text_bounds = []
        for text, style in self.identity:
            text_bounds.extend(style.draw(image, text))
        if self.caption:
            text = ctx.lyric["text"] if ctx.lyric else ""
            art_layer = self.lyric_art.get(text)
            if art_layer:
                bounds = art_layer.draw(image, ctx)
                if bounds:
                    text_bounds.append(bounds)
            elif self.caption.mode == "neighbours":
                before, current, after = self.lyrics.neighbours(frame)
                text = "\n".join((before["text"] if before else "", current["text"] if current else "",
                                  after["text"] if after else ""))
            if not art_layer or self.word_indicator:
                text_bounds.extend(self._caption_draw(image, text, ctx))
            if self.word_indicator:
                self.word_indicator.draw(image, ctx)
            if self.word_caption and self.words:
                text_bounds.extend(self._word_caption_draw(image, ctx))
            self.text_states.setdefault(text, dict(first_frame=frame, bounds=text_bounds))
        elif self.word_caption and self.words:
            self._word_caption_draw(image, ctx)
        return self._final_output(image.convert("RGB"), ctx, apply_quality)

    def frame_reports(self):
        if hasattr(self, "_supersampled_reports"):
            return dict(self._supersampled_reports)
        reports = {layer.id: layer.frame_report for layer in self.layers
                   if isinstance(layer, (RudimentLayer, ParticleEmitter)) and layer.frame_report is not None}
        if self.word_indicator and self.word_indicator.frame_report is not None:
            reports["word-indicator"] = self.word_indicator.frame_report
        return reports

    def post_effect_reports(self):
        return dict(self.post_pipeline.last_report) if self.post_pipeline.enabled else {}

    def evidence(self):
        return dict(transformed_alpha_extents=self.extents, text_states=self.text_states,
                    render_quality=self.output_quality.evidence,
                    motions={x.id: x.motion.evidence for x in [*self.layers, *self.lyric_art.values()] if x.motion},
                    rudiment_renderers={x.id: x.evidence for x in self.layers if isinstance(x, RudimentLayer)},
                    **({"emitters": {x.id: x.evidence for x in self.layers
                       if isinstance(x, ParticleEmitter)}} if any(isinstance(x, ParticleEmitter)
                       for x in self.layers) else {}),
                    custom_scene=bool(self.scene),
                    **({"word_indicator": self.word_indicator.evidence} if self.word_indicator else {}),
                    **({"word_subtitles": {"enabled": True, "count": len(self.project.words),
                       "highlight_mode": self.caption.word_mode if self.caption else "off",
                       "separate_lane": self.word_caption is not None}} if self.words else {}),
                    **({"post_effects": self.post_pipeline.evidence} if self.post_pipeline.enabled else {}))
