"""Pillow scene composition; per-release artwork and appearance stay declarative."""

from dataclasses import dataclass
from functools import lru_cache
import gc
from copy import deepcopy
import math

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

from .effects import alpha_scaled, transformed_bounds, within, directional_trail, atlas_cell
from .io import fields, number, integer, rectangle
from .rudiment_renderers import RudimentLayer, default_renderers
from .particle_engine import ParticleEmitter
from .natural_materials import NaturalMaterialLayer
from .motion import Motion
from .timeline import EventTrack
from .word_timing import WordTrack
from .rich_subtitles import RichTextAppearance
from .lyric_indicator import WordIndicator
from .visualisers import effect_specs, VisualiserEffectHooks
from .scoped_effects import CompositingEffects
from .effect_graph import EffectGraph
from .effect_graph_modulation import EffectGraphControls
from .effect_graph_integration import validate_graph_sources, evaluate_effect_graph, create_gpu_backend
from .visualiser_routing import VisualiserLayerRouting
from .layer_masks import LayerMasks
from .layer_animation import LayerAnimations
from .layer_displacement import LayerDisplacement
from .note_renderers import NoteLayer


_CREATE_EFFECT_GRAPH_BACKEND = object()


@dataclass(frozen=True)
class FrameContext:
    frame: int
    output_seconds: float
    source_seconds: float
    beat: float
    lyric: dict | None
    cue: dict | None
    word: dict | None = None
    lyric_next: dict | None = None
    lyric_next_beat: float | None = None
    source_beat: float | None = None


class TextBlock:
    def __init__(self, project, config, *, label):
        fields(
            config,
            {
                "box",
                "font_size",
                "min_font_size",
                "colour",
                "panel",
                "stroke",
                "mode",
                "line_spacing",
                "neighbour_colour",
                "word_mode",
                "word_colour",
                "completed_colour",
                "font",
                "effects",
            },
            label,
        )
        self.project, self.config, self.label = project, config, label
        self.box = rectangle(config["box"], label, project.width, project.height)
        self.maximum = integer(config.get("font_size", 60), "font size", minimum=1, maximum=512)
        self.minimum = integer(
            config.get("min_font_size", self.maximum), "min font size", minimum=1, maximum=self.maximum
        )
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
            draw.text(
                (x, y - bb[1]),
                row,
                font=font,
                fill=colour or self.colour,
                stroke_width=self.stroke,
                stroke_fill="#12111c",
            )
            bounds.append([round(v, 3) for v in actual])
            y += bb[3] - bb[1] + self.spacing
        return bounds


class Sprite:
    def __init__(self, project, cfg):
        fields(
            cfg,
            {
                "id",
                "type",
                "asset",
                "center",
                "width",
                "bounds",
                "crop_alpha",
                "motion",
                "mapping",
                "atlas",
                "opacity",
                "trail",
                "start_seconds",
                "end_seconds",
                "cue_labels",
            },
            "layer",
        )
        self.project, self.cfg = project, cfg
        self.id = cfg["id"]
        self.kind = cfg.get("type", "sprite")
        if self.kind not in {"sprite", "bars"}:
            raise ValueError(
                "Supported declarative layers: sprite and bars; use scene hook for custom drawing"
            )
        self.bounds = rectangle(
            cfg.get("bounds", [0, 0, project.width, project.height]),
            "layer bounds",
            project.width,
            project.height,
        )
        self.opacity = number(cfg.get("opacity", 1), "opacity", minimum=0, maximum=1)
        self.motion = Motion(cfg["motion"], project.base, project.clock) if "motion" in cfg else None
        if self.motion:
            project.inputs.extend(self.motion.files)
        mapping = cfg.get("mapping", {})
        fields(
            mapping,
            {"dx", "dy", "rotation", "scale", "x_axis", "y_axis", "rotation_axis", "scale_axis"},
            "appearance mapping",
        )
        self.mapping = {k: number(mapping.get(k, 0), k) for k in ("dx", "dy", "rotation", "scale")}
        self.axes = {
            key: integer(mapping.get(key, default), key, maximum=2)
            for key, default in (("x_axis", 0), ("y_axis", 1), ("rotation_axis", 2), ("scale_axis", 2))
        }
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
            number(self.atlas["cycle_ticks"], "atlas cycle ticks", minimum=0.0001)
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
                draw.rectangle(
                    (left + j * width, min(mid, end), left + (j + 1) * width - 1, max(mid, end)),
                    fill="#76d4cf" if value >= 0 else "#edb56d",
                )
            return self.bounds
        v = self.motion.sample(ctx.source_seconds, continuous_beat=ctx.beat) if self.motion else (0, 0, 0)
        index = 0
        if self.atlas:
            phase = (ctx.beat + self.atlas.get("phase_beats", 0)) * 16 / self.atlas["cycle_ticks"]
            index = math.floor((phase % 1) * self.atlas["count"])
        width = max(1, round(self.width * (1 + v[self.axes["scale_axis"]] * self.mapping["scale"])))
        im = self.transformed(
            index, width, round(v[self.axes["rotation_axis"]] * self.mapping["rotation"], 3)
        )
        x = round(self.center[0] + v[self.axes["x_axis"]] * self.mapping["dx"] - im.width / 2)
        y = round(self.center[1] + v[self.axes["y_axis"]] * self.mapping["dy"] - im.height / 2)
        bounds = transformed_bounds(im, x, y)
        if not within(bounds, self.bounds):
            raise ValueError(f"Frame {ctx.frame}: {self.id} transformed alpha {bounds} exceeds {self.bounds}")
        image.alpha_composite(im, (x, y))
        return bounds


class Compositor:
    def __init__(self, project, *, scene=None, renderers=None,
                 _shared_effect_graph_gpu=_CREATE_EFFECT_GRAPH_BACKEND,
                 layer_cache=None, _cache_native_renderers=None):
        post = project.data.get('post_effects', {})
        if isinstance(post, dict) and 'feedback' in post and (scene is not None or renderers is not None):
            raise ValueError('Native feedback requires declarative sources; custom scene/renderers have no checkpoint identity')
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
            self.background.alpha_composite(
                source, ((project.width - source.width) // 2, (project.height - source.height) // 2)
            )
        self.lyrics, self.cues = EventTrack(project.lyrics), EventTrack(project.cues)
        self.words = WordTrack(project.words) if project.words else None
        self.renderers = default_renderers() if renderers is None else renderers
        self.layers = [
            RudimentLayer(project, cfg, self.renderers, self.context)
            if cfg.get("type") == "rudiment"
            else ParticleEmitter(project, cfg, self.context)
            if cfg.get("type") == "emitter"
            else NaturalMaterialLayer(project, cfg, self.context)
            if cfg.get("type") == "material"
            else NoteLayer(project, cfg)
            if cfg.get("type") == "notes"
            else Sprite(project, cfg)
            for cfg in project.data.get("layers", [])
        ]
        ids = [x.id for x in self.layers]
        if len(set(ids)) != len(ids):
            raise ValueError("Layer ids must be unique")
        caption_config = dict(project.data.get("captions", {}))
        if project.word_ref and caption_config and "word_mode" not in caption_config:
            caption_config["word_mode"] = "highlight" if project.lyrics else "current"
        self.caption = (
            TextBlock(project, caption_config, label="captions")
            if caption_config and ("lyrics" in project.data or project.word_ref)
            else None
        )
        self.word_caption = (
            TextBlock(project, project.data["word_captions"], label="word captions")
            if "word_captions" in project.data
            else None
        )
        self.word_indicator = (
            WordIndicator(project, project.data["word_indicator"], self.context, self.caption)
            if "word_indicator" in project.data
            else None
        )
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
                self.lyric_art[item["text"]] = Sprite(
                    project, dict(art["placement"], id=f"lyric-art-{i}", asset=item["asset"])
                )
            if self.lyric_art_fallback == "error":
                missing = {e["text"] for e in project.lyrics if e["text"]} - set(self.lyric_art)
                if missing:
                    raise ValueError(f"Missing lyric artwork: {sorted(missing)}")
        self.identity = []
        for item in project.data.get("identity", []):
            fields(item, {"text", "style"}, "identity")
            self.identity.append((item["text"], TextBlock(project, item["style"], label="identity")))
        self.extents, self.text_states = {}, {}
        targets = {
            "background",
            *(f"layer:{layer.id}" for layer in self.layers),
            *(f"identity:{i}" for i in range(len(self.identity))),
            *(f"lyric-art:{layer.id.rsplit('-', 1)[-1]}" for layer in self.lyric_art.values()),
        }
        if self.scene:
            targets.add("scene")
        if self.caption:
            targets.add("caption")
        if self.word_caption:
            targets.add("word-caption")
        if self.word_indicator:
            targets.add("word-indicator")
        # Bound captured RGBA route sets before temporal sample work allocates.
        self._temporal_route_count = len(targets)
        matte_sources = {
            spec["composite"]["matte"]["source"]
            for spec in self.effect_specs
            if spec.get("composite") and spec["composite"].get("matte")
        }
        unknown_mattes = matte_sources - targets
        if unknown_mattes:
            raise ValueError(f"Unknown visualiser matte sources: {sorted(unknown_mattes)}")
        self.matte_sources = matte_sources
        underlay_sources = {
            spec["composite"]["underlay"]["source"]
            for spec in self.effect_specs
            if spec.get("composite") and spec["composite"].get("underlay")
        }
        unknown_underlays = underlay_sources - targets
        if unknown_underlays:
            raise ValueError(f"Unknown visualiser underlay sources: {sorted(unknown_underlays)}")
        self.composite_providers = matte_sources | underlay_sources
        self.visualiser_hooks = VisualiserEffectHooks(project, self.effect_specs, self.context)
        self.post_pipeline = CompositingEffects(
            project, project.data.get("post_effects", {}), self.context, valid_elements=targets
        )
        self.effect_graph = (
            EffectGraph(project.effect_graph_spec)
            if "effect_graph" in project.data else None
        )
        if self.effect_graph is None and project.data.get("effect_graph_controls"):
            raise ValueError("effect_graph_controls requires effect_graph")
        self.effect_graph_controls = (
            EffectGraphControls(project, self.effect_graph, project.data.get("effect_graph_controls", []), self.context)
            if self.effect_graph is not None else None
        )
        if getattr(project, "osc_control_take_ref", None) is not None:
            from .osc_control_take_m15 import RecordedOSCControls

            self.effect_graph_controls = RecordedOSCControls(
                project, self.effect_graph, self.effect_graph_controls)
        if getattr(project, "midi_control_take_ref", None) is not None:
            from .midi_control_take_m15 import RecordedMIDIControls

            self.effect_graph_controls = RecordedMIDIControls(
                project, self.effect_graph, self.effect_graph_controls)
        # M14.2 opt-in cached ONNX estimates are external pinned graph sources,
        # not native geometry buffers. Legacy rendering never imports ONNX.
        self.vision_graph = None
        if "vision_sources" in project.data:
            if self.effect_graph is None:
                raise ValueError("M14.2 vision_sources requires an effect_graph")
            # M14.5: prediction requests remain on the authored encoded-frame
            # clock even when the native shutter owns a higher-FPS compositor.
            # The M14.2 model and source-size checks still apply to every view.
            from .vision_graph_m14 import VisionGraphSources
            self.vision_graph = VisionGraphSources(project, project.data["vision_sources"])
        validate_graph_sources(self.effect_graph, targets,
                               extra_sources=self.vision_graph.source_ids if self.vision_graph else ())
        self.effect_graph_backend_mode = project.data.get("effect_graph_backend", "cpu")
        self._owns_effect_graph_gpu = _shared_effect_graph_gpu is _CREATE_EFFECT_GRAPH_BACKEND
        if self._owns_effect_graph_gpu:
            self.effect_graph_gpu = (
                create_gpu_backend(self.effect_graph_backend_mode)
                if self.effect_graph is not None else None
            )
        else:
            self.effect_graph_gpu = _shared_effect_graph_gpu
        mask_order = ["background"]
        mask_order.extend(f"layer:{layer.id}" for layer in self.layers)
        self.layer_masks = LayerMasks(project.data.get("layer_masks", {}), mask_order)
        self.layer_animations = LayerAnimations(project.data.get("layer_animations", {}), mask_order)
        self.layer_displacement = LayerDisplacement(project.data.get("layer_displacement", {}), mask_order)
        self._isolate_stage_injections = bool(
            project.data.get("visualiser_stage_injections") or self.composite_providers
        )
        self.visualiser_routing = None
        if "visualiser_routing" in project.data:
            if not self.effect_specs:
                raise ValueError("visualiser_routing needs at least one Butterchurn/projectM effect")
            # Match the established draw order; the lyric artwork/caption branch
            # determines which one is actually visible at each frame.
            element_order = ["background"]
            if self.scene:
                element_order.append("scene")
            element_order.extend(f"layer:{layer.id}" for layer in self.layers)
            element_order.extend(f"identity:{index}" for index in range(len(self.identity)))
            element_order.extend(
                f"lyric-art:{layer.id.rsplit('-', 1)[-1]}" for layer in self.lyric_art.values()
            )
            if self.caption:
                element_order.append("caption")
            if self.word_indicator:
                element_order.append("word-indicator")
            if self.word_caption:
                element_order.append("word-caption")
            self.visualiser_routing = VisualiserLayerRouting(
                project.data["visualiser_routing"], element_order, self.background.size
            )
        self._route_parts = []
        self._cache_native_renderers = (renderers is None if _cache_native_renderers is None
                                        else _cache_native_renderers)
        self.layer_surfaces = None
        if layer_cache is not False:
            from .compositor_layer_cache import LayerSurfaces
            try:
                self.layer_surfaces = LayerSurfaces(self, layer_cache,
                    native_renderers=self._cache_native_renderers)
            except BaseException:
                self.close()
                raise

    def context(self, frame):
        p = self.project
        lyric = self.lyrics.at(frame)
        lyric_next = self.lyrics.next_effective(frame)
        word = self.words.at(frame, lyric if p.lyrics else None) if self.words else None
        return FrameContext(
            frame,
            frame / p.fps,
            p.source_time(frame),
            p.continuous_beat(frame),
            lyric,
            self.cues.at(frame),
            word,
            lyric_next,
            p.continuous_beat(lyric_next["frame"]) if lyric_next is not None else None,
            source_beat=p.clock.beat_at(p.source_time(frame)),
        )

    def _caption_draw(self, image, text, ctx):
        if not self.words or self.caption.word_mode == "off":
            return self.caption.draw(image, text)
        state = self.words.state(ctx.frame, ctx.lyric if self.project.lyrics else None)
        if self.caption.word_mode == "current":
            return self.caption.draw(image, state["text"])
        if self.caption.rich is not None:
            return self.caption.draw_styled(
                image,
                text,
                active=state["active"],
                completed=state["completed"] if self.caption.word_mode == "progressive" else (),
            )
        return self.caption.draw_styled(
            image,
            text,
            active=state["active"],
            completed=state["completed"] if self.caption.word_mode == "progressive" else (),
        )

    def _word_caption_draw(self, image, ctx):
        state = self.words.state(ctx.frame, ctx.lyric if self.project.lyrics else None)
        return self.word_caption.draw(image, state["text"])

    def _capture_route(self, name, image):
        if self.visualiser_routing is not None or self._isolate_stage_injections:
            self._route_parts.append((name, image.copy()))

    def _frame_with_post_effects(self, ctx):
        """Render isolated RGBA elements, then the final frame, in draw order.

        Untargeted elements are still drawn onto transparent scratch surfaces
        when any post pipeline exists. This leaves neighbouring pixels untouched
        and ensures consistent alpha-over semantics across every element type.
        """
        pipeline = self.post_pipeline
        pipeline.begin()
        self._route_parts = []
        mask_surfaces = {}
        background = self.background.copy()
        if self.layer_surfaces is not None:
            background, _ = self.layer_surfaces.draw("background", ctx,
                lambda canvas: canvas.paste(self.background))
        background = pipeline.apply_element(background, "background", ctx)
        mask_surfaces["background"] = background
        self._capture_route("background", background)
        # Background is a graph provider; later compositing must not mutate it.
        image = background.copy()

        if self.scene:
            isolate_scene = (
                pipeline.affects("scene")
                or self.visualiser_routing is not None
                or (self.effect_graph is not None and "scene" in self.effect_graph.source_ids)
            )
            if isolate_scene:
                # A scene hook with isolated FX paints onto transparent RGBA.
                # Untargeted hooks keep their original composited-background API.
                scene_ink = Image.new("RGBA", self.background.size)
                scene_ink = self.scene(scene_ink, ctx)
                if scene_ink.size != self.background.size or scene_ink.mode != "RGBA":
                    raise ValueError("Custom scene hook must return frame-sized RGBA")
                scene_ink = pipeline.apply_element(scene_ink, "scene", ctx)
                mask_surfaces["scene"] = scene_ink
                self._capture_route("scene", scene_ink)
                image.alpha_composite(scene_ink)
            else:
                image = self.scene(image, ctx)
                if image.size != self.background.size or image.mode != "RGBA":
                    raise ValueError("Custom scene hook must return frame-sized RGBA")

        def paint(key, render):
            if self.layer_surfaces is not None:
                scratch, bounds = self.layer_surfaces.draw(key, ctx, render)
            else:
                scratch = Image.new("RGBA", self.background.size)
                bounds = render(scratch)
            if not bounds:
                return None
            scratch = pipeline.apply_element(scratch, key, ctx)
            scratch = self.layer_animations.apply(key, scratch, ctx)
            scratch = self.layer_displacement.apply(key, scratch, mask_surfaces)
            scratch = self.layer_masks.apply(key, scratch, mask_surfaces, ctx)
            # Retain the masked surface for downstream mattes. Hidden provider
            # layers contribute transparent surfaces, not stale prior frames.
            mask_surfaces[key] = scratch
            actual = scratch.getchannel("A").getbbox()
            self._capture_route(key, scratch)
            # Matte/underlay providers are compositor data, not visible artwork. Keep
            # their full isolated RGBA surface for downstream stages without
            # painting the provider into the clean composition.
            if key not in self.composite_providers and actual:
                # Pillow otherwise allocates a frame-sized temporary even for
                # a bounded caption or sprite. Restricting alpha-over to the
                # exact non-transparent region preserves pixels and avoids a
                # repeated native-resolution allocation.
                image.alpha_composite(scratch, dest=actual[:2], source=actual)
            return actual

        for layer in self.layers:
            bounds = paint(f"layer:{layer.id}", lambda canvas, layer=layer: layer.draw(canvas, ctx))
            if bounds:
                previous = self.extents.get(layer.id, bounds)
                self.extents[layer.id] = [
                    min(bounds[0], previous[0]),
                    min(bounds[1], previous[1]),
                    max(bounds[2], previous[2]),
                    max(bounds[3], previous[3]),
                ]

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
                text = "\n".join(
                    (
                        before["text"] if before else "",
                        current["text"] if current else "",
                        after["text"] if after else "",
                    )
                )
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

        image = pipeline.apply_frame(image, ctx)
        return evaluate_effect_graph(
            self.effect_graph, self.effect_graph_controls, ctx, image, mask_surfaces,
            gpu_backend=self.effect_graph_gpu,
            gpu_strict=self.effect_graph_backend_mode == "gpu",
            vision_sources=self.vision_graph,
        )

    def _final_output(self, image, context, apply_quality):
        # Historical shutter samples must not receive a separate AA pass.
        if apply_quality and not getattr(self, "_temporal_sampling", False):
            image = self.output_quality.apply(image)
        return image, context

    def temporal_contract(self):
        """Inspect native temporal work without rendering pixels or external engines."""
        from .compositor_temporal import temporal_contract
        result = temporal_contract(self.project)
        fx = self.temporal_fx_contract()
        if fx is not None:
            result['temporal_fx'] = fx
        return result

    def temporal_plan(self, frame):
        """Plan native input sampling for one encoded step, before feedback.

        ``feedback_plan`` separately describes any delayed reconstruction.
        """
        from .compositor_temporal import temporal_plan
        return temporal_plan(self, frame)

    def temporal_fx_contract(self):
        """Read-only M11.3 supported effects and bounded source reach."""
        from .compositor_temporal_fx import temporal_fx_contract
        return temporal_fx_contract(self)

    def temporal_fx_plan(self, frame):
        """All deduplicated native read taps for the requested M11.3 output."""
        from .compositor_temporal_fx import temporal_fx_plan
        plan = temporal_fx_plan(self, frame)
        return plan.document() if plan is not None else None

    def sample_frames(self, anchor, offsets, *, boundary='error', repeat_local=True,
                      apply_quality=True):
        """Sample bounded signed integer past/future native frames, in tap order."""
        from .compositor_temporal import sample_frames
        return sample_frames(self, anchor, offsets, boundary=boundary,
            repeat_local=repeat_local, apply_quality=apply_quality)

    def feedback_contract(self):
        from .compositor_feedback import feedback_contract
        return feedback_contract(self)

    def feedback_plan(self, frame):
        from .compositor_feedback import feedback_plan
        return feedback_plan(self, frame)

    def feedback_snapshot(self):
        from .compositor_feedback import feedback_snapshot
        return feedback_snapshot(self)

    def feedback_checkpoint(self):
        from .compositor_feedback import feedback_session
        session = feedback_session(self)
        return session.checkpoint() if session is not None else None

    def restore_feedback(self, checkpoint, *, expected_sha256):
        from .compositor_feedback import feedback_session
        session = feedback_session(self)
        if session is None:
            raise ValueError('This compositor has no configured native feedback')
        session.restore(checkpoint, expected_sha256=expected_sha256)

    def frame(self, frame, *, apply_quality=True, cancelled=None, progress=None):
        surfaces = self.layer_surfaces
        if surfaces is None:
            return self._uncached_frame(frame, apply_quality=apply_quality,
                                        cancelled=cancelled, progress=progress)
        outer = surfaces.begin()
        try:
            result = self._uncached_frame(frame, apply_quality=apply_quality,
                                          cancelled=cancelled, progress=progress)
        except BaseException:
            surfaces.finish(False, outer)
            raise
        surfaces.finish(True, outer)
        return result

    def _uncached_frame(self, frame, *, apply_quality=True, cancelled=None, progress=None):
        if self.post_pipeline.feedback is not None:
            from .compositor_feedback import render_feedback_frame
            return render_feedback_frame(self, frame, apply_quality=apply_quality,
                cancelled=cancelled, progress=progress)
        if self.post_pipeline.temporal_fx is not None:
            from .compositor_temporal_fx import render_temporal_fx_frame
            return render_temporal_fx_frame(self, frame, apply_quality=apply_quality,
                cancelled=cancelled, progress=progress)
        from .compositor_temporal import render_scheduled_frame
        return render_scheduled_frame(self, frame, apply_quality=apply_quality, cancelled=cancelled)

    def _native_frame(self, frame, *, apply_quality=False):
        """One native sample; only the temporal scheduler calls this draw path."""
        ctx = self.context(frame)
        if (
            self.post_pipeline.enabled
            or self.visualiser_routing is not None
            or self.layer_masks.enabled
            or self.layer_animations.enabled
            or self.layer_displacement.enabled
            or self._isolate_stage_injections
            or self.effect_graph is not None
        ):
            # Final-frame opacity/rotation may leave transparent pixels. The
            # encoded RGB output uses a deterministic black matte, never an
            # implicit alpha-discard that would silently erase opacity.
            matte = Image.new("RGBA", self.background.size, "#000000")
            matte.alpha_composite(self._frame_with_post_effects(ctx))
            return self._final_output(matte.convert("RGB"), ctx, apply_quality)
        image = self.background.copy()
        if self.layer_surfaces is not None:
            image, _ = self.layer_surfaces.draw("background", ctx,
                lambda canvas: canvas.paste(self.background))
        if self.scene:
            image = self.scene(image, ctx)
            if image.size != self.background.size or image.mode != "RGBA":
                raise ValueError("Custom scene hook must return frame-sized RGBA")
        for layer in self.layers:
            bounds = (self.layer_surfaces.direct(image, f"layer:{layer.id}", ctx,
                        lambda canvas, layer=layer: layer.draw(canvas, ctx))
                      if self.layer_surfaces is not None else layer.draw(image, ctx))
            if bounds:
                previous = self.extents.get(layer.id, bounds)
                self.extents[layer.id] = [
                    min(bounds[0], previous[0]),
                    min(bounds[1], previous[1]),
                    max(bounds[2], previous[2]),
                    max(bounds[3], previous[3]),
                ]
        text_bounds = []
        for index, (text, style) in enumerate(self.identity):
            boxes = (self.layer_surfaces.direct(image, f"identity:{index}", ctx,
                        lambda canvas, text=text, style=style: style.draw(canvas, text), canvas_dependent=True)
                     if self.layer_surfaces is not None else style.draw(image, text))
            text_bounds.extend(boxes)
        if self.caption:
            text = ctx.lyric["text"] if ctx.lyric else ""
            art_layer = self.lyric_art.get(text)
            if art_layer:
                bounds = (self.layer_surfaces.direct(image, "lyric-art:"+art_layer.id.rsplit("-", 1)[-1],
                            ctx, lambda canvas: art_layer.draw(canvas, ctx))
                          if self.layer_surfaces is not None else art_layer.draw(image, ctx))
                if bounds:
                    text_bounds.append(bounds)
            elif self.caption.mode == "neighbours":
                before, current, after = self.lyrics.neighbours(frame)
                text = "\n".join(
                    (
                        before["text"] if before else "",
                        current["text"] if current else "",
                        after["text"] if after else "",
                    )
                )
            if not art_layer or self.word_indicator:
                boxes = (self.layer_surfaces.direct(image, "caption", ctx,
                            lambda canvas: self._caption_draw(canvas, text, ctx), canvas_dependent=True)
                         if self.layer_surfaces is not None else self._caption_draw(image, text, ctx))
                text_bounds.extend(boxes)
            if self.word_indicator:
                if self.layer_surfaces is not None:
                    self.layer_surfaces.direct(image, "word-indicator", ctx,
                        lambda canvas: self.word_indicator.draw(canvas, ctx), canvas_dependent=True)
                else:
                    self.word_indicator.draw(image, ctx)
            if self.word_caption and self.words:
                boxes = (self.layer_surfaces.direct(image, "word-caption", ctx,
                            lambda canvas: self._word_caption_draw(canvas, ctx), canvas_dependent=True)
                         if self.layer_surfaces is not None else self._word_caption_draw(image, ctx))
                text_bounds.extend(boxes)
            self.text_states.setdefault(text, dict(first_frame=frame, bounds=text_bounds))
        elif self.word_caption and self.words:
            if self.layer_surfaces is not None:
                self.layer_surfaces.direct(image, "word-caption", ctx,
                    lambda canvas: self._word_caption_draw(canvas, ctx), canvas_dependent=True)
            else:
                self._word_caption_draw(image, ctx)
        return self._final_output(image.convert("RGB"), ctx, apply_quality)

    def layer_cache_stats(self):
        return (self.layer_surfaces.cache.stats() if self.layer_surfaces is not None else
                deepcopy(getattr(self, '_closed_layer_cache_stats', None)))

    def frame_reports(self):
        if hasattr(self, "_supersampled_reports"):
            return dict(self._supersampled_reports)
        reports = {
            layer.id: layer.frame_report
            for layer in self.layers
            if isinstance(layer, (RudimentLayer, ParticleEmitter, NaturalMaterialLayer, NoteLayer))
            and layer.frame_report is not None
        }
        if self.word_indicator and self.word_indicator.frame_report is not None:
            reports["word-indicator"] = self.word_indicator.frame_report
        return reports

    def post_effect_reports(self):
        return (dict(self.post_pipeline.last_report)
                if (self.post_pipeline.enabled or self.post_pipeline.feedback is not None
                    or self.post_pipeline.temporal_fx is not None) else {})

    def close(self):
        feedback = getattr(self, '_feedback_session', None)
        if feedback is not None:
            feedback.close()
        if self._owns_effect_graph_gpu and self.effect_graph_gpu is not None:
            self.effect_graph_gpu.close()
        virtual = getattr(self, "_temporal_virtual", None)
        if virtual is not None:
            virtual.close()
        for layer in self.layers:
            if isinstance(layer, (NaturalMaterialLayer, ParticleEmitter)):
                layer.close()
        # Method-level lru_cache instances retain ``self`` and Pillow font
        # objects. Clear them explicitly so a staged Windows font can be
        # removed when a working-cache job completes.
        TextBlock.layout.cache_clear()
        Sprite.transformed.cache_clear()
        if self.word_indicator:
            type(self.word_indicator)._centre.cache_clear()
        from .super_sampling import _ScaledText

        _ScaledText.layout.cache_clear()
        if self.layer_surfaces is not None:
            try:
                self._closed_layer_cache_stats = self.layer_surfaces.cache.stats()
                self._closed_layer_cache_stats['scope'] = 'before compositor resource close'
            finally:
                self.layer_surfaces.close()
                self.layer_surfaces = None
        gc.collect()

    def evidence(self):
        virtual = getattr(self, "_temporal_virtual", None)
        controls = (virtual or self).effect_graph_controls
        return dict(
            **({"midi_controls": controls.midi_evidence}
               if controls is not None and hasattr(controls, "midi_evidence") else {}),
            **({"osc_controls": controls.osc_evidence}
               if controls is not None and hasattr(controls, "osc_evidence") else {}),
            **({'feedback': {'contract': self.feedback_contract(), 'state': self.feedback_snapshot()}}
               if self.post_pipeline.feedback is not None else {}),
            **({'temporal_fx': {'contract': self.temporal_fx_contract(),
                    'last_execution': deepcopy(getattr(self, '_last_temporal_fx', None))}}
               if self.post_pipeline.temporal_fx is not None else {}),
            temporal_schedule={"contract": self.temporal_contract(),
                "last_execution": deepcopy(getattr(self, "_last_temporal_schedule", None))},
            transformed_alpha_extents=self.extents,
            text_states=self.text_states,
            render_quality=self.output_quality.evidence,
            **({"vision_sources": {**self.vision_graph.evidence,
                "last_used": deepcopy(self.vision_graph.last_used)}}
               if self.vision_graph is not None else {}),
            **({"effect_graph_backend": {
                "requested": self.effect_graph_backend_mode,
                "actual": "gpu" if self.effect_graph_gpu is not None else "cpu",
                "capabilities": (
                    vars(self.effect_graph_gpu.capabilities)
                    if self.effect_graph_gpu is not None else None
                ),
                "last_dispatch": (
                    self.effect_graph_gpu.last_dispatch
                    if self.effect_graph_gpu is not None else None
                ),
                "last_transport": (
                    dict(getattr(self.effect_graph_gpu, "last_transport", {}))
                    if self.effect_graph_gpu is not None else None
                ),
                **({"sampling": {
                    "scope": "last_subframe",
                    "sample_fps": virtual.project.fps,
                    "encoded_fps": self.project.fps,
                }} if virtual is not None else {}),
            }} if self.effect_graph is not None else {}),
            motions={x.id: x.motion.evidence for x in [*self.layers, *self.lyric_art.values()] if x.motion},
            rudiment_renderers={x.id: x.evidence for x in self.layers if isinstance(x, RudimentLayer)},
            **({'note_layers': {x.id: x.evidence for x in self.layers if isinstance(x, NoteLayer)}}
               if any(isinstance(x, NoteLayer) for x in self.layers) else {}),
            **(
                {
                    "natural_materials": {
                        x.id: x.evidence for x in self.layers if isinstance(x, NaturalMaterialLayer)
                    }
                }
                if any(isinstance(x, NaturalMaterialLayer) for x in self.layers)
                else {}
            ),
            **(
                {"emitters": {x.id: x.evidence for x in self.layers if isinstance(x, ParticleEmitter)}}
                if any(isinstance(x, ParticleEmitter) for x in self.layers)
                else {}
            ),
            custom_scene=bool(self.scene),
            layer_masks=self.layer_masks.config,
            layer_animations=self.layer_animations.config,
            layer_displacement=self.layer_displacement.config,
            **(
                {"visualiser_routing": self.visualiser_routing.evidence}
                if self.visualiser_routing is not None
                else {}
            ),
            **({"word_indicator": self.word_indicator.evidence} if self.word_indicator else {}),
            **(
                {
                    "word_subtitles": {
                        "enabled": True,
                        "count": len(self.project.words),
                        "highlight_mode": self.caption.word_mode if self.caption else "off",
                        "separate_lane": self.word_caption is not None,
                    }
                }
                if self.words
                else {}
            ),
            **({"post_effects": self.post_pipeline.evidence}
               if (self.post_pipeline.enabled or self.post_pipeline.feedback is not None
                   or self.post_pipeline.temporal_fx is not None) else {}),
        )
