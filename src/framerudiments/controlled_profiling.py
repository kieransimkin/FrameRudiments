"""One isolated audio/input intervention, using the existing native renderer bridges.

Every condition is a fresh process in the driver; no presets, physics, or image
analysis are reimplemented. Zero source mix means no reference-image contribution,
not a black image repeatedly darkening the feedback buffer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace

from PIL import Image

from .audio_probes import DURATION, PROTOCOL, load_pcm, pcm_window
from .io import sha256, write_json


def source_image(role: str, edge: int) -> Image.Image:
    if type(edge) is not int or not 64 <= edge <= 1024:
        raise ValueError("Capture edge must be 64..1024")
    if role == "engine-only":
        return Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    if role not in {"neutral-a", "neutral-b"}:
        raise ValueError("Only engine-only or generated achromatic controls are accepted")
    rng = random.Random(7123 if role == "neutral-a" else 9431)
    size = 37 if role == "neutral-a" else 29
    image = Image.frombytes("L", (size, size), bytes(rng.randrange(35, 210) for _ in range(size * size)))
    try:
        return image.resize((edge, edge), Image.Resampling.BILINEAR).convert("RGBA")
    finally:
        image.close()


def job_signature(job: dict) -> str:
    # Own code and bridge bytes are part of every resumable condition identity.
    identity = dict(job)
    identity.pop("output", None)
    identity["host_code"] = {name: sha256(Path(__file__).parent / name) for name in
                             ("controlled_profiling.py", "audio_probes.py", "visualisers.py",
                              "projectm_backend.py", "visualiser_assets/butterchurn-page.js",
                              "visualiser_assets/butterchurn-worker.mjs")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_job(job: dict) -> None:
    if job.get("protocol") != PROTOCOL:
        raise ValueError("Unknown controlled capture protocol")
    fps, capture = job.get("render_fps"), job.get("capture_fps")
    if (type(fps) is not int or not 44 <= fps <= 120 or type(capture) is not int
            or not 5 <= capture <= 30 or fps % capture):
        raise ValueError("Render fps must be 44..120 and a multiple of capture fps (5..30)")
    if math.ceil(44100 / fps) > 1024:
        raise ValueError("Audio windows would leave gaps")
    if not 0 <= job.get("trial", -1) < 12:
        raise ValueError("Invalid trial")
    if job.get("input_role") not in {"engine-only", "neutral-a", "neutral-b"}:
        raise ValueError("Reference media are not allowed in this protocol")
    expected_mix = 0.0 if job["input_role"] == "engine-only" else .15
    if job.get("input_mix") != expected_mix:
        raise ValueError("Reference feed does not match the declared isolation role")
    if job["preset"].get("engine") not in {"butterchurn", "projectm"}:
        raise ValueError("Unsupported renderer")
    if sha256(job["preset"]["preset"]["path"]) != job["preset"]["preset"]["sha256"]:
        raise ValueError("Preset source bytes changed")
    if job["stimulus"].get("trial") != job["trial"]:
        raise ValueError("Stimulus and trial disagree")


def capture_condition(job: dict, *, factory=None) -> dict:
    validate_job(job)
    signature = job_signature(job)
    out = Path(job["output"]).resolve()
    receipt_path = out / "condition.json"
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        if previous.get("signature") != signature:
            raise ValueError("Condition identity changed; use a new workspace")
        if previous.get("state") == "captured":
            if sha256(previous["source"]["path"]) != previous["source"]["sha256"]:
                raise ValueError("Saved source control changed")
            for frame in previous["captures"]:
                if sha256(frame["output"]["path"]) != frame["output"]["sha256"]:
                    raise ValueError("Saved capture changed; never silently reuse it")
            load_pcm(job["stimulus"])  # validate the stimulus on resume too
            return previous
        raise ValueError("An earlier attempt is partial; retain it and use --retry-failed")
    out.mkdir(parents=True, exist_ok=True)
    pcm = load_pcm(job["stimulus"])
    image = source_image(job["input_role"], job["edge"])
    source = out / "input.png"
    image.save(source)
    input_ref = {"path": str(source), "sha256": sha256(source)}
    ref = job["preset"]
    result = {"id": job["id"], "signature": signature, "protocol": PROTOCOL,
              "state": "capturing", "captures": [], "trial": job["trial"],
              "input_role": job["input_role"], "input_mix": job["input_mix"],
              "source_pixels_sha256": hashlib.sha256(image.tobytes()).hexdigest(),
              "source": input_ref, "stimulus": job["stimulus"],
              "audio_mode": job["stimulus"]["id"], "imagery": job["input_role"],
              "render_fps": job["render_fps"], "capture_fps": job["capture_fps"],
              "edge": job["edge"], "seed": job["seed"], "fresh_instance": True,
              "seed_control": "paired" if ref["engine"] == "butterchurn" else "not_available",
              "pcm_delivery": "1024 stereo history samples at every render tick; no temporal gaps",
              "preset_sha256": ref["preset"]["sha256"], "processed_frames": 0}
    write_json(receipt_path, result)
    backend = None
    prior_profile_root = os.environ.get("FRAMERUDIMENTS_BROWSER_PROFILE_ROOT")
    if not prior_profile_root:
        # Avoid a many-hundred-character browser profile path on Windows.
        os.environ["FRAMERUDIMENTS_BROWSER_PROFILE_ROOT"] = str(out.parents[2] / "browser")
    try:
        if factory is None:
            from .visualisers import ButterchurnBackend
            from .projectm_backend import ProjectMBackend
            factory = {"butterchurn": ButterchurnBackend, "projectm": ProjectMBackend}[ref["engine"]]
        backend = factory(image.size, job["render_fps"], ref["preset"]["path"],
                          job["input_mix"], job["seed"], out)
        count = round(DURATION * job["render_fps"]) + 1
        stride = job["render_fps"] // job["capture_fps"]
        for frame in range(count):
            t = frame / job["render_fps"]
            window = pcm_window(pcm, frame, job["render_fps"])
            capture = frame % stride == 0
            context = SimpleNamespace(frame=frame, output_seconds=t, source_seconds=t)
            processed = backend.advance(image, context, window, capture=capture)
            result["processed_frames"] += 1
            if capture:
                if processed is None or processed.size != image.size:
                    raise ValueError("Renderer did not produce the requested raster")
                target = out / f"f-{frame:05}.png"
                try:
                    processed.save(target)
                finally:
                    processed.close()
                result["captures"].append({"frame": frame, "output_seconds": t,
                    "input": input_ref, "output": {"path": str(target), "sha256": sha256(target)},
                    "pcm_rms": math.sqrt(sum(x * x for x in window) / len(window))})
            if frame % job["render_fps"] == 0:
                print(f"{job['id']}: {t:.0f}/{DURATION:g}s", flush=True)
        result["state"] = "captured"
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(state="failed", error=type(exc).__name__ + ": " + str(exc))
    finally:
        image.close()
        if backend is not None:
            try:
                backend.close()
            except Exception as exc:
                result.update(state="cleanup-failed", error=type(exc).__name__ + ": " + str(exc))
            result["runtime"] = backend.evidence
        if prior_profile_root is None:
            os.environ.pop("FRAMERUDIMENTS_BROWSER_PROFILE_ROOT", None)
        write_json(receipt_path, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = capture_condition(json.loads(args.job.read_text(encoding="utf-8")))
        return 0 if result["state"] == "captured" else 2
    except (ValueError, OSError) as exc:
        print(f"Controlled capture stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
