"""Full-frame, sequential MilkDrop post-processing for composed lyric videos."""

from array import array
import base64
import json
import math
import mmap
import os
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageColor, ImageOps

from .encoding import audio_filters
from .io import fields, integer, number, sha256
from .scoped_effects import EffectStage, MAX_STAGES
from .visualiser_compositing import composite_stage, validate_stage_composite

ASSETS = Path(__file__).with_name("visualiser_assets")
PRESETS = ("feedback", "kaleidoscope", "julia")


def _preset_ref(project, engine, preset, *, trusted=False):
    if isinstance(preset, str):
        if preset not in PRESETS:
            raise ValueError(f"Unknown built-in visualiser preset: {preset}")
        path = ASSETS / f"{preset}.{'json' if engine == 'butterchurn' else 'milk'}"
        return project.ref(dict(path=str(path), sha256=sha256(path)))
    if trusted is not True:
        raise ValueError(
            "External presets contain executable equations/shaders: set trusted_preset true after review"
        )
    return project.ref(preset)


def _colour(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a CSS colour string")
    try:
        rgb = ImageColor.getrgb(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a CSS colour string") from exc
    if len(rgb) != 3:
        raise ValueError(f"{label} must not include alpha")
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


def _palette_grade(config, label):
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError(f"{label} must be an object")
    fields(config, {"stops", "amount"}, label)
    stops = config.get("stops")
    if not isinstance(stops, list) or not 2 <= len(stops) <= 16:
        raise ValueError(f"{label}.stops must contain 2 to 16 CSS colours")
    return dict(
        stops=[_colour(value, f"{label}.stops[{index}]") for index, value in enumerate(stops)],
        amount=number(config.get("amount", 1), f"{label}.amount", minimum=0, maximum=1),
    )


def apply_palette_grade(image, config):
    """Map output luminance through an ordered palette while preserving alpha."""
    if config is None or config["amount"] == 0:
        return image
    source = image.convert("RGBA")
    alpha = source.getchannel("A")
    rgb = source.convert("RGB")
    grey = ImageOps.grayscale(rgb)
    colours = [ImageColor.getrgb(value) for value in config["stops"]]
    tables = [[], [], []]
    for value in range(256):
        position = value * (len(colours) - 1) / 255
        lower = min(math.floor(position), len(colours) - 2)
        fraction = position - lower
        for channel in range(3):
            tables[channel].append(
                round(colours[lower][channel] * (1 - fraction) + colours[lower + 1][channel] * fraction)
            )
    graded = Image.merge("RGB", tuple(grey.point(table) for table in tables))
    result = Image.blend(rgb, graded, config["amount"]).convert("RGBA")
    result.putalpha(alpha)
    return result


def effect_specs(project):
    configs = project.data.get("effects", [])
    if not isinstance(configs, list) or len(configs) > 8:
        raise ValueError("effects must be an ordered list with at most eight stages")
    specs = []
    for cfg in configs:
        fields(
            cfg,
            {
                "id",
                "engine",
                "preset",
                "input_mix",
                "seed",
                "trusted_preset",
                "preset_timeline",
                "mesh_width",
                "mesh_height",
                "output_palette_grade",
                "composite",
                "input_effects",
                "feedback_effects",
                "output_effects",
            },
            "full-frame effect",
        )
        name = cfg.get("id")
        if not isinstance(name, str) or not name or name in [item["id"] for item in specs]:
            raise ValueError("Full-frame effect ids must be nonempty and unique")
        engine = cfg.get("engine")
        if engine not in {"butterchurn", "projectm"}:
            raise ValueError("Full-frame engine must be butterchurn or projectm")
        if engine != "butterchurn" and ("mesh_width" in cfg or "mesh_height" in cfg):
            raise ValueError("mesh_width and mesh_height require Butterchurn")
        ref = _preset_ref(
            project,
            engine,
            cfg.get("preset", "feedback"),
            trusted=cfg.get("trusted_preset", False),
        )
        timeline = cfg.get("preset_timeline", [])
        if timeline and engine != "projectm":
            raise ValueError("preset_timeline currently requires projectM")
        if not isinstance(timeline, list) or len(timeline) > 64:
            raise ValueError("preset_timeline must be a list with at most 64 entries")
        resolved_timeline = []
        previous = -1.0
        for index, entry in enumerate(timeline):
            fields(
                entry,
                {"at_source_seconds", "preset", "transition_seconds", "trusted_preset"},
                f"preset_timeline[{index}]",
            )
            at = number(
                entry.get("at_source_seconds"),
                f"preset_timeline[{index}].at_source_seconds",
                minimum=0,
            )
            if at <= previous:
                raise ValueError("preset_timeline source times must be strictly increasing")
            previous = at
            resolved_timeline.append(
                dict(
                    at_source_seconds=at,
                    preset=_preset_ref(
                        project,
                        engine,
                        entry.get("preset"),
                        trusted=entry.get("trusted_preset", False),
                    ),
                    transition_seconds=number(
                        entry.get("transition_seconds", 0.5),
                        f"preset_timeline[{index}].transition_seconds",
                        minimum=0,
                        maximum=30,
                    ),
                )
            )
        specs.append(
            dict(
                id=name,
                engine=engine,
                preset=ref,
                input_mix=number(cfg.get("input_mix", 0.65), "input mix", minimum=0.001, maximum=1),
                seed=integer(cfg.get("seed", 0), "visualiser seed", maximum=2**32 - 1),
                preset_timeline=resolved_timeline,
                mesh_width=integer(
                    cfg.get("mesh_width", 64), "Butterchurn mesh width", minimum=16, maximum=256
                ),
                mesh_height=integer(
                    cfg.get("mesh_height", 48), "Butterchurn mesh height", minimum=12, maximum=256
                ),
                output_palette_grade=_palette_grade(
                    cfg.get("output_palette_grade"), "full-frame effect.output_palette_grade"
                ),
                composite=validate_stage_composite(
                    cfg.get("composite"), "full-frame effect.composite"
                ),
                input_effects=cfg.get("input_effects", []),
                feedback_effects=cfg.get("feedback_effects", []),
                output_effects=cfg.get("output_effects", []),
            )
        )
    return specs


class VisualiserEffectHooks:
    """Validated VisualStyle pipelines at three explicit visualiser boundaries."""

    POINTS = ("input", "feedback", "output")

    def __init__(self, project, specs, context_at):
        self.pipelines = []
        self.last_report = {}
        files = []
        for index, spec in enumerate(specs):
            stage = {}
            for point in self.POINTS:
                rows = spec.get(f"{point}_effects", [])
                if not isinstance(rows, list) or len(rows) > MAX_STAGES:
                    raise ValueError(f"visualiser {point}_effects must contain <=16 stages")
                pipeline = [
                    EffectStage(
                        project,
                        row,
                        context_at,
                        label=f"effects[{index}].{point}_effects",
                    )
                    for row in rows
                ]
                ids = [item.id for item in pipeline]
                if len(ids) != len(set(ids)):
                    raise ValueError(f"visualiser {point}_effects ids must be unique within a stage")
                stage[point] = pipeline
                files.extend(ref for item in pipeline for ref in item.files)
            self.pipelines.append(stage)
        project.inputs.extend(files)
        self.evidence = [
            {
                point: [item.evidence for item in pipeline[point]]
                for point in self.POINTS
            }
            for pipeline in self.pipelines
        ]

    @property
    def enabled(self):
        return any(any(stage[point] for point in self.POINTS) for stage in self.pipelines)

    @property
    def feedback_enabled(self):
        return any(stage["feedback"] for stage in self.pipelines)

    def begin(self):
        self.last_report = {}

    def apply(self, stage_index, point, image, context):
        rows = self.pipelines[stage_index][point]
        reports = []
        for item in rows:
            image, report = item.apply(image.convert("RGBA"), context, frame_scope=True)
            reports.append(report)
        if reports:
            self.last_report[f"stage:{stage_index}:{point}"] = reports
        return image


class EffectAudio:
    """Windowed native-rate PCM from the encoder's exact excerpt/repeat/fade chain."""

    def __init__(self, project, ffmpeg, output):
        self.project, self.handle, self.data = project, None, None
        path = Path(output) / "effects-audio.f32"
        command = [
            ffmpeg,
            "-n",
            "-v",
            "error",
            "-i",
            project.audio_ref["path"],
            "-map",
            "0:a:0",
            "-af",
            audio_filters(project),
            "-ac",
            "2",
            "-ar",
            str(project.sample_rate),
            "-f",
            "f32le",
            str(path),
        ]
        subprocess.run(command, check=True, capture_output=True)
        self.samples = path.stat().st_size // 8
        expected = (project.end_sample - project.start_sample) * project.repeats
        if self.samples != expected or path.stat().st_size % 8:
            raise ValueError("Effect PCM does not match the encoded programme sample count")
        self.evidence = dict(
            sha256=sha256(path),
            samples_per_channel=self.samples,
            sample_rate=project.sample_rate,
            channels=2,
            format="f32le",
            filters=audio_filters(project),
            window="1024 stereo samples ending at floor(output_seconds * native sample_rate); zero padded",
        )
        self.handle = path.open("rb")
        self.data = mmap.mmap(self.handle.fileno(), 0, access=mmap.ACCESS_READ)

    def at(self, frame):
        end = min(self.samples, frame * self.project.sample_rate // self.project.fps)
        start = max(0, end - 1024)
        values = array("f")
        values.frombytes(bytes((1024 - (end - start)) * 8) + self.data[start * 8 : end * 8])
        if __import__("sys").byteorder != "little":
            values.byteswap()
        return values

    def close(self):
        if self.data is not None:
            self.data.close()
            self.data = None
        if self.handle:
            self.handle.close()
            self.handle = None


class ButterchurnBackend:
    def __init__(self, size, fps, preset, mix, seed, work, *, mesh_width=64, mesh_height=48):
        runtime = Path(os.environ.get("FRAMERUDIMENTS_BUTTERCHURN_RUNTIME", ""))
        browser = os.environ.get("FRAMERUDIMENTS_BROWSER")
        node = os.environ.get("FRAMERUDIMENTS_NODE") or shutil.which("node")
        if (
            not node
            or not browser
            or not Path(browser).is_file()
            or not (runtime / "package-lock.json").is_file()
        ):
            raise ValueError(
                "Butterchurn needs its locked Node runtime, FRAMERUDIMENTS_BROWSER and Node.js >=22.12"
            )
        if sha256(runtime / "package-lock.json") != sha256(ASSETS / "package-lock.json"):
            raise ValueError("Butterchurn runtime package lock differs from the tested pin")
        profile_root = Path(os.environ.get("FRAMERUDIMENTS_BROWSER_PROFILE_ROOT") or work).resolve()
        profile_root.mkdir(parents=True, exist_ok=True)
        self.size, self.fps, self.process_handle = size, fps, None
        self.feedback_pixels = None
        self.log = (
            Path(work) / f"butterchurn-{seed}-{len(list(Path(work).glob('butterchurn-*.log')))}.log"
        ).open("wb")
        try:
            self.process_handle = subprocess.Popen(
                [node, str(ASSETS / "butterchurn-worker.mjs")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.log,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            result = self._request(
                dict(
                    command="init",
                    runtime=str(runtime.resolve()),
                    browser=browser,
                    width=size[0],
                    height=size[1],
                    fps=fps,
                    preset=json.loads(Path(preset).read_text(encoding="utf-8")),
                    mix=mix,
                    seed=seed,
                    mesh_width=mesh_width,
                    mesh_height=mesh_height,
                    work=str(Path(work).resolve()),
                    profile_root=str(profile_root),
                )
            )
            self.evidence = dict(
                result["evidence"],
                package_lock_sha256=sha256(ASSETS / "package-lock.json"),
                host_bridge_sha256=sha256(ASSETS / "butterchurn-page.js"),
            )
        except BaseException:
            self.close()
            raise

    def _request(self, message):
        proc = self.process_handle
        try:
            proc.stdin.write(json.dumps(message, allow_nan=False) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("Butterchurn worker exited unexpectedly; inspect its log")
            result = json.loads(line)
            if not result.get("ok"):
                raise RuntimeError("Butterchurn: " + result.get("error", "unknown worker error"))
            return result
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError("Butterchurn worker communication failed") from exc

    def process(self, image, context, pcm):
        return self.advance(image, context, pcm, capture=True)

    def advance(self, image, context, pcm, *, capture=False):
        def encode(data):
            return base64.b64encode(data).decode("ascii")

        # Butterchurn's documented offline input uses 8-bit WebAudio-style PCM.
        left = bytes(max(0, min(255, round(128 + value * 128))) for value in pcm[0::2])
        right = bytes(max(0, min(255, round(128 + value * 128))) for value in pcm[1::2])
        mono = bytes(max(0, min(255, round(128 + (lv + rv) * 64))) for lv, rv in zip(pcm[0::2], pcm[1::2]))
        pixels = image.convert("RGBA").tobytes()
        feedback = self.feedback_pixels
        rgba = None if feedback is None and pixels == getattr(self, "last_pixels", None) else encode(pixels)
        result = self._request(
            dict(
                command="frame",
                time=context.output_seconds,
                capture=capture,
                rgba=rgba,
                feedback_rgba=encode(feedback) if feedback is not None else None,
                left=encode(left),
                right=encode(right),
                mono=encode(mono),
            )
        )
        self.feedback_pixels = None
        self.last_pixels = pixels
        if abs(result["time"] - context.output_seconds) > 1e-9 or result["fps"] != self.fps:
            raise RuntimeError("Butterchurn did not honour the exact frame clock")
        return (
            Image.frombytes("RGBA", self.size, base64.b64decode(result["rgba"], validate=True))
            if capture
            else None
        )

    def set_feedback(self, image):
        if image.mode != "RGBA" or image.size != self.size:
            raise ValueError("Butterchurn feedback hook needs frame-sized RGBA")
        self.feedback_pixels = image.tobytes()

    def close(self):
        proc = self.process_handle
        if proc:
            if proc.poll() is None:
                if hasattr(self, "evidence"):
                    result = self._request(dict(command="close"))
                    self.evidence["cleanup"] = result["cleanup"]
                    if not all(result["cleanup"].values()):
                        raise RuntimeError("Butterchurn owned browser cleanup was not verified")
                else:
                    proc.stdin.close()
                proc.wait(timeout=75)
            proc.stdin.close()
            proc.stdout.close()
            self.process_handle = None
        self.log.close()


class EffectChain:
    def __init__(self, project, specs, ffmpeg, output, *, factories=None, routing=None, hooks=None):
        self.backends, self.next_frame, self.audio = [], 0, None
        self.closed = False
        self.specs = specs
        self.routing = routing
        self.hooks = hooks
        self.preset_timeline_positions = [0 for _ in specs]
        self.preset_switches = []
        self.stage_injections = {}
        requested = getattr(project, "data", {}).get("visualiser_stage_injections", [])
        if not isinstance(requested, list) or len(requested) > 32 or (requested and not specs):
            raise ValueError("visualiser_stage_injections requires a bounded effect chain")
        for entry in requested:
            if not isinstance(entry, dict) or set(entry) - {"before", "elements", "opacity"}:
                raise ValueError("Invalid visualiser stage injection")
            before, names, opacity = entry.get("before"), entry.get("elements"), entry.get("opacity", 1)
            if type(before) is not int or not 1 <= before < len(specs):
                raise ValueError("Stage injection must target a later effect stage")
            if (
                not isinstance(names, list)
                or not names
                or len(names) != len(set(names))
                or any(not isinstance(name, str) or not name for name in names)
            ):
                raise ValueError("Stage injection needs distinct element names")
            if type(opacity) not in (int, float) or not 0 <= opacity <= 1:
                raise ValueError("Stage injection opacity must be between zero and one")
            self.stage_injections.setdefault(before, []).append((tuple(names), opacity))
        default_factories = factories is None
        if factories is None:
            from .projectm_backend import ProjectMBackend

            factories = dict(butterchurn=ButterchurnBackend, projectm=ProjectMBackend)
        try:
            self.audio = EffectAudio(project, ffmpeg, output)
            for spec in specs:
                backend_args = [
                    (project.width, project.height),
                    project.fps,
                    spec["preset"]["path"],
                    spec["input_mix"],
                    spec["seed"],
                    output,
                ]
                backend_kwargs = (
                    dict(
                        mesh_width=spec.get("mesh_width", 64),
                        mesh_height=spec.get("mesh_height", 48),
                    )
                    if default_factories and spec["engine"] == "butterchurn"
                    else {}
                )
                backend = factories[spec["engine"]](*backend_args, **backend_kwargs)
                self.backends.append(backend)
        except BaseException:
            self.close()
            raise

    def process(self, image, context, *, isolated_layers=None):
        if context.frame != self.next_frame:
            raise ValueError(
                "MilkDrop feedback requires sequential frames from zero; recreate and replay to seek"
            )
        overlay = None
        if self.routing is not None:
            if isolated_layers is None:
                raise ValueError("Routed visualisers require the isolated compositor surfaces")
            image, overlay = self.routing.route(context, isolated_layers)
        pcm = self.audio.at(context.frame)
        surfaces = dict(isolated_layers or [])
        if self.stage_injections and isolated_layers is None:
            raise ValueError("Intermediate stage injection needs isolated layer surfaces")
        if self.hooks is not None:
            self.hooks.begin()
        for index, backend in enumerate(self.backends):
            timeline = self.specs[index].get("preset_timeline", [])
            position = self.preset_timeline_positions[index]
            while position < len(timeline) and context.source_seconds >= timeline[position]["at_source_seconds"]:
                event = timeline[position]
                if not hasattr(backend, "switch_preset"):
                    raise ValueError("Scheduled preset changes require a backend with switch_preset support")
                backend.switch_preset(event["preset"]["path"], event["transition_seconds"])
                self.preset_switches.append(
                    dict(
                        frame=context.frame,
                        source_seconds=context.source_seconds,
                        stage=index,
                        scheduled_at_source_seconds=event["at_source_seconds"],
                        transition_seconds=event["transition_seconds"],
                        preset=event["preset"],
                    )
                )
                position += 1
            self.preset_timeline_positions[index] = position
            if index in self.stage_injections:
                image = image.convert("RGBA")
                for names, opacity in self.stage_injections[index]:
                    for name in names:
                        surface = surfaces.get(name)
                        if surface is None:
                            # A gated or invisible layer has no isolated pixels
                            # in this frame; it is a valid empty contribution.
                            continue
                        if surface.mode != "RGBA" or surface.size != image.size:
                            raise ValueError("Stage injection must be full-frame RGBA")
                        source = surface.copy()
                        if opacity != 1:
                            source.putalpha(source.getchannel("A").point(lambda a: round(a * opacity)))
                        image.alpha_composite(source)
            stage_base = image.convert("RGBA")
            stage_input = (
                self.hooks.apply(index, "input", stage_base, context)
                if self.hooks is not None
                else stage_base
            )
            image = backend.process(stage_input, context, pcm)
            if image.size != (self.audio.project.width, self.audio.project.height) or image.mode != "RGBA":
                raise ValueError("Visualiser must return frame-sized RGBA")
            if self.hooks is not None and self.hooks.pipelines[index]["feedback"]:
                if not hasattr(backend, "set_feedback"):
                    raise ValueError("Visualiser backend does not support feedback-state writeback")
                feedback = self.hooks.apply(index, "feedback", image.copy(), context)
                backend.set_feedback(feedback)
            image = apply_palette_grade(image, self.specs[index].get("output_palette_grade"))
            if self.hooks is not None:
                image = self.hooks.apply(index, "output", image, context)
            image = composite_stage(stage_base, image, self.specs[index].get("composite"), surfaces)
        self.next_frame += 1
        if overlay is not None:
            final = image.convert("RGBA")
            final.alpha_composite(overlay)
            image = final
        return image.convert("RGB")

    def evidence(self):
        return dict(
            stage="after scene, layers, identity, captions and lyric art; before encoding",
            sequential_frames=self.next_frame,
            pcm=self.audio.evidence,
            routing=self.routing.evidence if self.routing is not None else None,
            stage_injections={index: entries for index, entries in self.stage_injections.items()},
            effect_hooks=self.hooks.evidence if self.hooks is not None else None,
            feedback_hook_writeback=(
                "processed engine frame is written into the retained feedback target before the next frame"
                if self.hooks is not None and self.hooks.feedback_enabled
                else None
            ),
            preset_switches=self.preset_switches,
            stages=[dict(spec, runtime=backend.evidence) for spec, backend in zip(self.specs, self.backends)],
            limits="GPU/driver and engine noise can vary; not a cross-platform bit-exact contract. Text bounds refer to pre-effect composition.",
        )

    def close(self):
        if self.closed:
            return
        failures = []
        for backend in reversed(self.backends):
            try:
                backend.close()
            except Exception as exc:
                failures.append(exc)
        if self.audio:
            self.audio.close()
        if failures:
            raise RuntimeError("Effect cleanup failed: " + "; ".join(str(exc) for exc in failures))
        self.closed = True
