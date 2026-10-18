"""Sequential preset response experiments using ordinary FrameRudiments projects."""

from array import array
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont

from . import __version__, encoding
from .compositor import Compositor
from .io import file_ref, sha256, write_json
from .preset_library import load_library
from .project import Project
from .visualisers import EffectAudio, ButterchurnBackend
from .projectm_backend import ProjectMBackend

CARDS = ("colour-bars", "grid-rings", "text-card")


def make_cards(directory, size, font):
    """Original static calibration illustrations, not simulated renderer evidence."""
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    w, h = size
    paths = {}
    for name in CARDS:
        image = Image.new("RGB", size, "#111111")
        draw = ImageDraw.Draw(image)
        if name == "colour-bars":
            colours = ("white", "yellow", "cyan", "lime", "magenta", "red", "blue", "black")
            for i, colour in enumerate(colours):
                draw.rectangle((i * w // 8, 0, (i + 1) * w // 8, 3 * h // 4), fill=colour)
            for x in range(w):
                value = round(255 * x / max(1, w - 1))
                draw.line((x, 3 * h // 4, x, h), fill=(value,) * 3)
        elif name == "grid-rings":
            step = max(8, min(size) // 12)
            for x in range(0, w, step):
                draw.line((x, 0, x, h), fill="#888888")
            for y in range(0, h, step):
                draw.line((0, y, w, y), fill="#888888")
            for radius in range(step, min(size) // 2, step):
                draw.ellipse(
                    (w // 2 - radius, h // 2 - radius, w // 2 + radius, h // 2 + radius),
                    outline="white",
                    width=2,
                )
            draw.rectangle((0, 0, step, step), fill="red")
            draw.rectangle((w - step, h - step, w - 1, h - 1), fill="cyan")
        else:
            draw.rectangle((0, 0, w, h // 2), fill="white")
            for i, text in enumerate(("FRAME 012345", "Aa Bb Cc !?", "top LEFT", "bottom RIGHT")):
                face = ImageFont.truetype(str(font), max(8, min(size) // 17))
                draw.text(
                    (w // 12, h * (i * 2 + 1) // 9), text, font=face, fill="black" if i < 2 else "white"
                )
        path = out / (name + ".png")
        image.save(path)
        paths[name] = dict(path=str(path.resolve()), sha256=sha256(path))
    return paths


def capture_frames(intervals, fps, frame_count):
    if not intervals or any(not math.isfinite(x) or x < 0 for x in intervals):
        raise ValueError("Capture intervals must be finite nonnegative output seconds")
    frames = sorted({math.ceil(x * fps - 1e-9) for x in intervals})
    if frames[-1] >= frame_count:
        raise ValueError("A capture interval is outside the project programme")
    if len(frames) > 512:
        raise ValueError("At most 512 captures per condition")
    return frames


def _signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _saved_job(path, signature):
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("signature") != signature:
        raise ValueError("Resume configuration differs; choose a new output directory")
    for condition in data.get("conditions", []):
        for capture in condition.get("captures", []):
            for key in ("input", "output"):
                ref = capture[key]
                if not Path(ref["path"]).is_file() or sha256(ref["path"]) != ref["sha256"]:
                    raise ValueError("Resume capture bytes differ; never reuse a stale profile")
    return data


def profile_presets(
    suite_path,
    catalogue,
    output,
    *,
    intervals=(0, 1, 2, 4),
    images=("cover-lyrics", *CARDS),
    audio_modes=("programme", "silence"),
    ids=(),
    limit=None,
    resume=False,
    retry_failed=False,
    prism_python=None,
    models=(),
    max_edge=256,
    ffmpeg=None,
    ffprobe=None,
    factories=None,
):
    """Each condition starts a clean engine and replays every frame, including uncaptured intervals."""
    suite_path, catalogue = Path(suite_path).resolve(), Path(catalogue).resolve()
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    if suite.get("schema") != "framerudiments.preset-suite.v1" or not suite.get("projects"):
        raise ValueError("Expected a nonempty framerudiments.preset-suite.v1 projects list")
    if (
        set(images) - {"cover-lyrics", *CARDS}
        or not images
        or not audio_modes
        or set(audio_modes) - {"programme", "silence"}
    ):
        raise ValueError("Unknown image/audio experiment condition")
    if limit is not None and (isinstance(limit, bool) or limit < 1):
        raise ValueError("Preset limit must be positive")
    source_assets = [file_ref(ref, suite_path.parent) for ref in suite.get("assets", [])]
    library = load_library(catalogue)
    for texture in library.get("textures", []):
        file_ref(texture, catalogue.parent) if set(texture) <= {"path", "sha256"} else file_ref(
            {key: texture[key] for key in ("path", "sha256")}, catalogue.parent
        )
    texture_parents = {str(Path(x["path"]).resolve().parent) for x in library.get("textures", [])}
    if len(texture_parents) > 1:
        raise ValueError("Library textures must share one installed texture directory")
    rows = [x for x in library["presets"] if not ids or x["id"] in ids]
    if set(ids) - {x["id"] for x in rows}:
        raise ValueError("Unknown selected preset id")
    if limit is not None:
        rows = rows[:limit]
    ffmpeg, ffprobe = encoding.binaries(ffmpeg, ffprobe)
    projects = []
    for ref in suite["projects"]:
        verified = file_ref(ref, suite_path.parent)
        project = Project.load(verified["path"])
        if not project.lyric_ref or not project.data.get("background", {}).get("image"):
            raise ValueError("Every sample project must pin cover artwork and timed lyrics")
        if project.data.get("effects"):
            raise ValueError("Sample projects must be the untreated composition")
        encoding.validate_audio(project, ffprobe)
        compositor = Compositor(project)
        project.verify_inputs()
        frames = capture_frames(intervals, project.fps, project.frame_count)
        projects.append((project, verified, frames, compositor))
    analysis_identity = None
    if prism_python:
        probe = subprocess.run(
            [
                str(prism_python),
                "-I",
                "-c",
                "import json,importlib.metadata as m;print(json.dumps({n:m.version(n) for n in ['prismmoves','pixelcue','numpy','Pillow']}))",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        analysis_identity = json.loads(probe.stdout)
    settings = dict(
        analysis_identity=analysis_identity,
        schema="framerudiments.preset-experiment.v1",
        version=__version__,
        suite_sha256=sha256(suite_path),
        catalogue_sha256=sha256(catalogue),
        source_assets=source_assets,
        textures=[dict(path=x["path"], sha256=sha256(x["path"])) for x in library.get("textures", [])],
        project_inputs=[p.summary() for p, _, _, _ in projects],
        intervals=list(intervals),
        images=list(images),
        audio_modes=list(audio_modes),
        models=list(models),
        max_edge=max_edge,
        preset_ids=[x["id"] for x in rows],
        host_code={
            name: sha256(Path(__file__).parent / name)
            for name in (
                "preset_profiling.py",
                "profile_analysis.py",
                "visualisers.py",
                "projectm_backend.py",
                "io.py",
                "visualiser_assets/butterchurn-page.js",
                "visualiser_assets/butterchurn-worker.mjs",
                "visualiser_assets/package-lock.json",
            )
        },
        runtime_files={
            key: dict(path=os.environ[key], sha256=sha256(os.environ[key]))
            for key in ("FRAMERUDIMENTS_PROJECTM_LIBRARY", "FRAMERUDIMENTS_BROWSER", "FRAMERUDIMENTS_NODE")
            if os.environ.get(key) and Path(os.environ[key]).is_file()
        },
        input_mix=suite.get("input_mix", 0.35),
        seed=suite.get("seed", 0),
    )
    mix = settings["input_mix"]
    if not isinstance(mix, (int, float)) or isinstance(mix, bool) or not 0.001 <= mix <= 1:
        raise ValueError("Input mix must be in [.001,1]")
    seed = settings["seed"]
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Seed must be a uint32")
    signature = _signature(settings)
    out = Path(output).resolve()
    plan = out / "experiment.json"
    if out.exists():
        if (
            not resume
            or not plan.is_file()
            or json.loads(plan.read_text(encoding="utf-8"))["signature"] != signature
        ):
            raise ValueError("Existing output needs --resume and an identical experiment")
    else:
        out.mkdir(parents=True)
        write_json(plan, dict(signature=signature, **settings))
    factories = factories or {"butterchurn": ButterchurnBackend, "projectm": ProjectMBackend}
    conditions = []
    audio_handles = []
    previous_profile_root = os.environ.get("FRAMERUDIMENTS_BROWSER_PROFILE_ROOT")
    if previous_profile_root is None:
        os.environ["FRAMERUDIMENTS_BROWSER_PROFILE_ROOT"] = str(out / ".browser")
    previous_texture_dir = os.environ.get("FRAMERUDIMENTS_TEXTURES_DIR")
    if texture_parents:
        os.environ["FRAMERUDIMENTS_TEXTURES_DIR"] = next(iter(texture_parents))
    try:
        for index, (project, _, frames, _) in enumerate(projects):
            work = out / "inputs" / f"sample-{index + 1:02}"
            work.mkdir(parents=True, exist_ok=True)
            # Decode once per sample; same encoder-owned trim/repeat/fade contract.
            pcm_path = work / "effects-audio.f32"
            if pcm_path.exists():
                # A resumed run writes a fresh decode to a new directory, never trusts old PCM.
                decode = work / f"decode-{len(list(work.glob('decode-*'))):04}"
                decode.mkdir()
            else:
                decode = work
            audio = EffectAudio(project, ffmpeg, decode)
            audio_handles.append(audio)
            cards = make_cards(work / "cards", (project.width, project.height), project.font_ref["path"])
            for imagery in images:
                data = copy.deepcopy(project.data)
                if imagery != "cover-lyrics":
                    for key in ("lyrics", "canonical_lyrics", "lyric_art", "identity", "layers", "cues"):
                        data.pop(key, None)
                    data["background"] = dict(image=cards[imagery], fit="contain")
                p = Project(data, project.base)
                c = Compositor(p)
                for mode in audio_modes:
                    condition_id = f"sample-{index + 1:02}-{imagery}-{mode}"
                    captures = {}
                    for frame in frames:
                        image, context = c.frame(frame)
                        target = work / f"{imagery}-{frame:08}.png"
                        image.save(target)
                        captures[frame] = dict(
                            frame=frame,
                            output_seconds=context.output_seconds,
                            source_seconds=context.source_seconds,
                            input=dict(path=str(target), sha256=sha256(target)),
                        )
                    conditions.append(
                        dict(
                            id=condition_id,
                            project=p,
                            audio=audio,
                            compositor=c,
                            mode=mode,
                            imagery=imagery,
                            sample=index + 1,
                            captures=captures,
                        )
                    )
        jobs = []
        for row in rows:
            preset = file_ref(row["preset"], catalogue.parent)
            job_sig = _signature(dict(experiment=signature, preset=row))
            directory = out / "presets" / row["id"].replace(":", "-")
            manifest_path = directory / "captures.json"
            saved = _saved_job(manifest_path, job_sig)
            if saved and saved.get("complete") and (saved["state"] == "captured" or not retry_failed):
                result = saved
            else:
                # Retry uses a fresh attempt, retaining every earlier failure receipt.
                attempt = directory / f"attempt-{len(list(directory.glob('attempt-*'))):04}"
                attempt.mkdir(parents=True)
                result = dict(
                    schema="framerudiments.preset-captures.v1",
                    signature=job_sig,
                    preset=row,
                    experiment_signature=signature,
                    conditions=[],
                    state="captured",
                    complete=False,
                    limits=[
                        "Appearance is conditioned on inputs, mix, frame rate, resolution, runtime and GPU.",
                        "projectM exposes no deterministic seed API; cross-run differences are not isolated audio causality.",
                        "No legibility, flash safety, rights or aesthetic approval is inferred.",
                    ],
                )
                for condition in conditions:
                    p = condition["project"]
                    work = attempt / condition["id"]
                    work.mkdir()
                    backend = None
                    receipt = dict(
                        id=condition["id"],
                        sample=condition["sample"],
                        imagery=condition["imagery"],
                        audio_mode=condition["mode"],
                        state="captured",
                        captures=[],
                        fps=p.fps,
                        dimensions=[p.width, p.height],
                        title=p.data.get("title"),
                        audio=condition["audio"].evidence,
                    )
                    try:
                        backend = factories[row["engine"]](
                            (p.width, p.height), p.fps, preset["path"], mix, seed, work
                        )
                        compositor = Compositor(p)  # reset any stateful native/history layers
                        for n in range(max(condition["captures"]) + 1):
                            image, context = compositor.frame(n)
                            pcm = (
                                condition["audio"].at(n)
                                if condition["mode"] == "programme"
                                else array("f", [0]) * 2048
                            )
                            if hasattr(backend, "advance"):
                                processed = backend.advance(
                                    image, context, pcm, capture=n in condition["captures"]
                                )
                            else:
                                processed = backend.process(image, context, pcm)
                            if n in condition["captures"]:
                                target = work / f"output-{n:08}.png"
                                processed.save(target)
                                receipt["captures"].append(
                                    dict(
                                        condition["captures"][n],
                                        output=dict(path=str(target), sha256=sha256(target)),
                                    )
                                )
                        p.verify_inputs()
                    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                        receipt.update(state="failed", error=f"{type(exc).__name__}: {exc}")
                        result["state"] = "partial"
                    finally:
                        if backend:
                            try:
                                backend.close()
                            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
                                receipt.update(state="cleanup-failed", cleanup_error=str(exc))
                                result["state"] = "partial"
                            receipt["runtime"] = backend.evidence
                    receipt["processed_frames"] = (
                        max(condition["captures"]) + 1 if receipt["state"] == "captured" else None
                    )
                    result["conditions"].append(receipt)
                    write_json(manifest_path, result)
                result["complete"] = True
                write_json(manifest_path, result)
            if prism_python:
                command = [
                    str(prism_python),
                    "-I",
                    str(Path(__file__).with_name("profile_analysis.py")),
                    str(manifest_path),
                    "--max-edge",
                    str(max_edge),
                ]
                command.extend(("--models-dir", str(out / "models")))
                for model in models:
                    command.extend(("--model", model))
                analysis = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
                write_json(
                    directory / "analysis-run.json",
                    dict(
                        command=command,
                        returncode=analysis.returncode,
                        stdout=analysis.stdout[-4000:],
                        stderr=analysis.stderr[-4000:],
                    ),
                )
            jobs.append(
                dict(
                    id=row["id"],
                    name=row["name"],
                    state=result["state"],
                    manifest=str(manifest_path),
                    sha256=sha256(manifest_path),
                    profile=str(directory / "identity" / "response.json"),
                    analysis_state=json.loads(
                        (directory / "identity" / "response.json").read_text(encoding="utf-8")
                    )["state"]
                    if (directory / "identity" / "response.json").is_file()
                    else "unavailable",
                )
            )
            write_json(
                out / "index.json",
                dict(
                    experiment_signature=signature,
                    catalogue_total=len(library["presets"]),
                    selected=len(rows),
                    finished=len(jobs),
                    jobs=jobs,
                ),
            )
            print(json.dumps(jobs[-1]), flush=True)
        return jobs
    finally:
        for audio in audio_handles:
            audio.close()
        if previous_profile_root is None:
            os.environ.pop("FRAMERUDIMENTS_BROWSER_PROFILE_ROOT", None)
        if previous_texture_dir is None:
            os.environ.pop("FRAMERUDIMENTS_TEXTURES_DIR", None)
        else:
            os.environ["FRAMERUDIMENTS_TEXTURES_DIR"] = previous_texture_dir
