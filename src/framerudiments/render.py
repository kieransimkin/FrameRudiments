"""Shared render runner, cleanup, evidence and representative encoded stills."""
from pathlib import Path
import subprocess
import json
from importlib.metadata import version

from PIL import Image

from . import encoding, __version__
from .compositor import Compositor
from .io import sha256, write_json
from .project import Project
from .subtitles import export_subtitles
from .visualisers import EffectChain


def checkpoints(project):
    count = project.frame_count
    return sorted({0, count - 1, count // 4, count // 2, 3 * count // 4,
                   *(max(0, min(count - 1, event["frame"] + delta))
                     for event in project.lyrics + project.cues for delta in (-1, 0, 1))})


def _prepare(project, output, ffmpeg, ffprobe, scene, renderers):
    project = Project.load(project) if not isinstance(project, Project) else project
    ffmpeg, ffprobe = encoding.binaries(ffmpeg, ffprobe)
    source_probe = encoding.validate_audio(project, ffprobe)
    # Load every dependency before making the evidence snapshot.
    compositor = Compositor(project, scene=scene, renderers=renderers)
    project.verify_inputs()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "qa").mkdir()
    write_json(output / "plan.json", project.summary())
    write_json(output / "source-probe.json", source_probe)
    export_subtitles(project.lyrics, project.fps, project.frame_count, output, project.width, project.height)
    return project, output, ffmpeg, ffprobe, compositor


def preflight(project, output, *, ffmpeg=None, ffprobe=None, scene=None, renderers=None):
    p, out, ffmpeg, ffprobe, compositor = _prepare(project, output, ffmpeg, ffprobe, scene, renderers)
    selected = set(checkpoints(p))
    effects = None
    try:
        effects = EffectChain(p, compositor.effect_specs, ffmpeg, out) if compositor.effect_specs else None
        with (out / "rudiment-analysis.jsonl").open("w", encoding="utf-8") as analysis:
            for n in range(p.frame_count):
                image, context = compositor.frame(n)
                if effects:
                    if n in selected:
                        image.save(out / "qa" / f"composed-{n:08}.png")
                    image = effects.process(image, context)
                _analysis(analysis, compositor, context)
                if n in selected:
                    image.save(out / "qa" / f"source-{n:08}.png")
        if effects:
            effects.close()
        p.verify_inputs()
        result = dict(p.summary(), composition=compositor.evidence(), full_frame_effects=effects.evidence() if effects else None, analysis_sidecar=_sidecar(out), state="preflight passed; no encoded video")
        write_json(out / "manifest.json", result)
        return result
    except BaseException as exc:
        write_json(out / "failure.json", dict(state="preflight failed", error=str(exc)))
        raise
    finally:
        if effects:
            effects.close()


def render(project, output, *, ffmpeg=None, ffprobe=None, scene=None, renderers=None, preset="fast", crf=20):
    p, out, ffmpeg, ffprobe, compositor = _prepare(project, output, ffmpeg, ffprobe, scene, renderers)
    candidate, final = out / "candidate.mp4", out / "video.mp4"
    cmd = encoding.command(p, candidate, ffmpeg, preset=preset, crf=crf)
    write_json(out / "encode-command.json", cmd)
    selected = set(checkpoints(p))
    process = None
    effects = None
    try:
        effects = EffectChain(p, compositor.effect_specs, ffmpeg, out) if compositor.effect_specs else None
        with (out / "encode.log").open("wb") as log, (out / "rudiment-analysis.jsonl").open("w", encoding="utf-8") as analysis:
            process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            for n in range(p.frame_count):
                image, context = compositor.frame(n)
                if effects:
                    if n in selected:
                        image.save(out / "qa" / f"composed-{n:08}.png")
                    image = effects.process(image, context)
                _analysis(analysis, compositor, context)
                if n in selected:
                    image.save(out / "qa" / f"source-{n:08}.png")
                process.stdin.write(image.tobytes())
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError(f"FFmpeg failed; see {out / 'encode.log'}")
        if effects:
            effects.close()
        p.verify_inputs()
        verified = encoding.verify(candidate, p, ffmpeg, ffprobe)
        source_audio = encoding.probe(p.audio_ref["path"], ffprobe)
        source_channels = next(s["channels"] for s in source_audio["streams"] if s["codec_type"] == "audio")
        output_channels = next(s["channels"] for s in verified["probe"]["streams"] if s["codec_type"] == "audio")
        if source_channels != output_channels:
            raise ValueError("Encoder changed channel count")
        candidate.rename(final)
        # Extract from the actual encoded file, independently of the source Pillow frames.
        selection = "+".join(f"eq(n\\,{n})" for n in sorted(selected))
        subprocess.run([ffmpeg, "-n", "-v", "error", "-i", str(final), "-vf", f"select={selection}",
                        "-fps_mode", "passthrough", "-start_number", "0", "-frames:v", str(len(selected)),
                        str(out / "qa" / "checkpoint-%08d.png")], check=True, capture_output=True)
        for i, n in enumerate(sorted(selected)):
            (out / "qa" / f"checkpoint-{i:08}.png").rename(out / "qa" / f"encoded-{n:08}.png")
        with Image.open(out / "qa" / "encoded-00000000.png") as first:
            first.save(out / "poster.png")
            thumbnail = first.copy()
            thumbnail.thumbnail((360, 640))
            thumbnail.save(out / "phone-preview.png")
        result = dict(p.summary(), composition=compositor.evidence(), full_frame_effects=effects.evidence() if effects else None, analysis_sidecar=_sidecar(out), verification=verified,
                      software={"framerudiments": __version__, "Pillow": version("Pillow"),
                                "dancerudiments": version("dancerudiments"),
                                "ffmpeg": subprocess.check_output([ffmpeg, "-version"], text=True).splitlines()[0]},
                      output={"path": str(final), "sha256": sha256(final), "bytes": final.stat().st_size},
                      state="technical-QA passed; creative and attended listening review pending")
        write_json(out / "manifest.json", result)
        return result
    except BaseException as exc:
        if process and process.poll() is None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()  # only the encoder child owned by this render
                process.wait()
        write_json(out / "failure.json", dict(state="failed; any encoded file remains unapproved", error=str(exc)))
        raise
    finally:
        if effects:
            effects.close()


def _analysis(handle, compositor, context):
    reports = compositor.frame_reports()
    if reports:
        handle.write(json.dumps(dict(schema="framerudiments.rudiment-analysis-frame.v1", frame=context.frame, source_seconds=context.source_seconds,
                                     output_seconds=context.output_seconds, layers=reports), allow_nan=False) + "\n")


def _sidecar(output):
    path = output / "rudiment-analysis.jsonl"
    return dict(path=str(path), sha256=sha256(path), bytes=path.stat().st_size)
