"""Controlled audio response sweep: fresh renderer per condition, no neural inference.

Separate output from legacy and behaviour catalogues. Nothing is published remotely.
"""
from __future__ import annotations

import argparse
from array import array
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    # Use this checkout for capture orchestration, never an older installed wheel.
    sys.path.insert(0, str(ROOT / "src"))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, sort_keys=True, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


@contextmanager
def exclusive(work):
    lock = work / "run.lock"
    try:
        handle = lock.open("x")
    except FileExistsError as exc:
        raise ValueError(f"Another run or interrupted process owns {lock}. Check that process before removing this lock.") from exc
    try:
        with handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def run_stage(command, log, timeout):
    with Path(log).open("wb") as stream:
        try:
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(ROOT / "src")
            return subprocess.run(command, cwd=ROOT, env=environment, stdout=stream,
                                  stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            return 124


def ordered_presets(rows, seed):
    rng = random.Random(seed)
    groups = {engine: [r for r in rows if r["engine"] == engine] for engine in ("projectm", "butterchurn")}
    for group in groups.values():
        rng.shuffle(group)
    result = []
    while any(groups.values()):
        for engine in groups:
            if groups[engine]:
                result.append(groups[engine].pop())
    return result


def supplied_music(path, start, trial, directory, ffmpeg):
    """A separate analysis excerpt; never rewrites artist audio or its clocks."""
    from framerudiments.audio_probes import DURATION, SAMPLE_RATE
    if start < 0 or not path.is_file():
        raise ValueError("Invalid supplied musical excerpt")
    target = directory / f"music-{trial}.f32"
    receipt = target.with_suffix(".json")
    identity = {"source_sha256": digest(path), "source_start_seconds": start,
                "sample_rate": SAMPLE_RATE, "duration_seconds": DURATION, "trial": trial}
    if receipt.exists():
        saved = read(receipt)
        if any(saved.get(k) != v for k, v in identity.items()) or digest(target) != saved["sha256"]:
            raise ValueError("Supplied music changed; use a new output workspace")
        return saved
    decode = directory / f"music-decode-{time.time_ns()}.f32"
    subprocess.run([ffmpeg, "-n", "-v", "error", "-ss", str(start), "-i", str(path),
                    "-t", "8.5", "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", str(decode)], check=True)
    data = decode.read_bytes()
    if len(data) < int(8.5 * SAMPLE_RATE) * 8:
        raise ValueError("Supply at least 8.5 seconds of music after the selected start")
    data = bytes(2 * SAMPLE_RATE * 8) + data[:int(8.5 * SAMPLE_RATE) * 8] + bytes(int(1.5 * SAMPLE_RATE) * 8)
    pcm = array("f")
    pcm.frombytes(data)
    if sys.byteorder != "little":
        pcm.byteswap()
    import math
    if any(not math.isfinite(x) or abs(x) > 1 for x in pcm):
        raise ValueError("Musical PCM contains non-finite or out-of-range samples")
    target.write_bytes(data)
    saved = dict(identity, id="music", kind="music", path=str(target), sha256=digest(target),
                 channels=2, format="f32le", events=[], analysis_window=[2, 10.5], warmup_seconds=2,
                 samples_per_channel=len(data)//8,
                 rms=math.sqrt(sum(x*x for x in pcm[2*SAMPLE_RATE*2:int(10.5*SAMPLE_RATE)*2])/(8.5*SAMPLE_RATE*2)),
                 peak=max(abs(x) for x in pcm),
                 level_convention="Original excerpt level; no automatic loudness matching")
    write(receipt, saved)
    return saved


def run(args):
    from framerudiments.audio_probes import CORE, EXTENDED, PROTOCOL, make_probe
    from framerudiments.preset_library import load_library
    out = (args.output if args.output.is_absolute() else ROOT / args.output).resolve()
    if not out.is_relative_to((ROOT / "visualiser-analysis").resolve()):
        raise ValueError("Output must stay under this project's visualiser-analysis directory")
    if not args.analysis_python.is_file():
        raise ValueError("Install the isolated audio-effects environment first")
    subprocess.run([str(args.analysis_python), "-I", "-c", "import cv2; from prismmoves.controlled_response import analyse"], check=True)
    library = load_library(args.catalogue.resolve())
    if library.get("textures"):
        parents = {str(Path(r["path"]).resolve().parent) for r in library["textures"]}
        if len(parents) != 1:
            raise ValueError("Preset textures must share their installed directory")
        for ref in library["textures"]:
            if digest(ref["path"]) != ref["sha256"]:
                raise ValueError("Installed texture changed")
        os.environ["FRAMERUDIMENTS_TEXTURES_DIR"] = next(iter(parents))
    rows = [r for r in library["presets"] if (not args.ids or r["id"] in args.ids)
            and (not args.engine or r["engine"] == args.engine)]
    if args.ids and set(args.ids) - {r["id"] for r in rows}:
        raise ValueError("Selected preset not found")
    profiles = list(EXTENDED if args.extended else CORE)
    if args.audio:
        profiles.append("music")
    roles = ["engine-only"] + (["neutral-a", "neutral-b"] if args.texture_controls else [])
    count = len(profiles) * len(roles) * args.trials * (12 * args.capture_fps + 1)
    if count > 32768:
        raise ValueError("This protocol exceeds 32768 frames per preset; reduce capture fps or trials")
    # New process code must not be mistaken for a cached prior experiment.
    fingerprints = {name: digest(ROOT / name) for name in
        ("scripts/controlled_preset_analysis.py", "src/framerudiments/audio_probes.py",
         "src/framerudiments/controlled_profiling.py", "src/framerudiments/visualisers.py",
         "src/framerudiments/projectm_backend.py", "src/framerudiments/visualiser_assets/butterchurn-page.js",
         "src/framerudiments/visualiser_assets/butterchurn-worker.mjs")}
    runtime = {key: digest(os.environ[key]) for key in
               ("FRAMERUDIMENTS_PROJECTM_LIBRARY", "FRAMERUDIMENTS_BROWSER", "FRAMERUDIMENTS_NODE")
               if os.environ.get(key) and Path(os.environ[key]).is_file()}
    settings = dict(protocol=PROTOCOL, profiles=profiles, input_roles=roles, trials=args.trials,
                    render_fps=60, capture_fps=args.capture_fps, edge=args.edge, host_code=fingerprints,
                    runtime=runtime, catalogue_sha256=digest(args.catalogue),
                    audio_sha256=digest(args.audio) if args.audio else None, audio_start=args.audio_start)
    work = out / "_work"
    work.mkdir(parents=True, exist_ok=True)
    config = out / "experiment.json"
    if config.exists() and read(config) != settings:
        raise ValueError("Capture configuration changed; retain these results and choose a new --output")
    if not config.exists() and any(p.name not in {"_work", ".gitignore"} for p in out.iterdir()):
        raise ValueError("Unrecognised output directory; do not overwrite other workflows")
    write(config, settings)
    (out / ".gitignore").write_text("_work/\n", encoding="utf-8")
    with exclusive(work):
        stimuli = {}
        for trial in range(args.trials):
            for profile in profiles:
                stimuli[profile, trial] = (supplied_music(args.audio, args.audio_start, trial, work / "stimuli", args.ffmpeg)
                    if profile == "music" else make_probe(profile, trial, work / "stimuli"))
        schedule = ordered_presets(rows, args.order_seed if args.order_seed is not None else time.time_ns())
        write(work / "schedule.json", {"presets": [r["id"] for r in schedule]})
        processed = 0
        failures = 0
        for row in schedule:
            key = hashlib.sha256(row["id"].encode()).hexdigest()[:24]
            folder = work / key
            folder.mkdir(exist_ok=True)
            manifest_path = folder / "captures.json"
            # Finished transport is skippable, but failed analysis is not.
            public = out / key / "response.json"
            if public.is_file() and read(public).get("state") == "completed" and not args.reanalyse:
                # Retained captures, not just a successful report flag, define a resume.
                prior = read(manifest_path)
                for condition in prior["conditions"]:
                    refs = [condition["source"], condition["stimulus"]]
                    refs.extend(frame["output"] for frame in condition["captures"])
                    if any(digest(ref["path"]) != ref["sha256"] for ref in refs):
                        raise ValueError("Saved capture/source/stimulus changed; use a new output directory")
                continue
            if args.limit and processed >= args.limit:
                break
            print(f"[{processed+1}] {row['engine']}: {row['name']} | {count} frames; zero neural calls", flush=True)
            conditions = []
            tasks = [(role, profile, trial) for role in roles for profile in profiles for trial in range(args.trials)]
            random.Random(1451).shuffle(tasks)
            for number, (role, profile, trial) in enumerate(tasks, 1):
                cid = f"{role}__{profile}__{trial}"
                destination = folder / cid
                if args.retry_failed and (destination / "condition.json").exists() and read(destination / "condition.json").get("state") != "captured":
                    destination.rename(folder / f"{cid}-previous-{time.time_ns()}")
                elif destination.exists() and not (destination / "condition.json").exists():
                    destination.rename(folder / f"{cid}-interrupted-{time.time_ns()}")
                job = {"id": cid, "protocol": PROTOCOL, "input_role": role,
                       "input_mix": 0.0 if role == "engine-only" else .15,
                       "seed": 4051 + trial, "trial": trial, "preset": row,
                       "render_fps": 60, "capture_fps": args.capture_fps, "edge": args.edge,
                       "stimulus": stimuli[profile, trial], "output": str(destination)}
                job_path = folder / "job.json"
                write(job_path, job)
                print(f"  capture {number}/{len(tasks)} {cid}", flush=True)
                code = run_stage([sys.executable, "-u", "-m", "framerudiments.controlled_profiling", "--job", str(job_path)],
                                 folder / f"{cid}.log", args.timeout)
                saved = read(destination / "condition.json") if (destination / "condition.json").is_file() else dict(job, captures=[])
                if code:
                    saved["state"] = "failed"
                    saved["process_exit"] = code
                conditions.append(saved)
                write(manifest_path, {"protocol": PROTOCOL, "preset": row, "conditions": conditions,
                                      "settings": settings, "expected_conditions": len(tasks)})
            report_dir = out / key
            print("  Measuring temporal evidence and repeated audio contrasts...", flush=True)
            code = run_stage([str(args.analysis_python), "-I", "-u", "-m", "prismmoves.controlled_response", "analyse",
                              "--captures", str(manifest_path), "--out", str(report_dir)], folder / "analysis.log", args.timeout)
            if code not in (0, 2):
                raise ValueError(f"Analysis failed ({code}); see {folder / 'analysis.log'}")
            if code == 2:
                failures += 1
            subprocess.run([str(args.analysis_python), "-I", "-m", "prismmoves.controlled_response", "index", "--out", str(out)], check=True)
            processed += 1
        print(f"Processed {processed} presets ({failures} partial). Open {out / 'index.html'}", flush=True)
    return 2 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "visualiser-analysis" / "audio-response-catalogue")
    parser.add_argument("--catalogue", type=Path, default=ROOT / "models" / "preset-library" / "catalogue.json")
    parser.add_argument("--limit", type=int, default=2, help="0 processes the remaining catalogue")
    parser.add_argument("--trials", type=int, choices=range(3, 7), default=3)
    parser.add_argument("--capture-fps", type=int, choices=(5, 10, 15, 20, 30), default=15)
    parser.add_argument("--edge", type=int, default=384)
    parser.add_argument("--extended", action="store_true")
    parser.add_argument("--texture-controls", action="store_true")
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--audio-start", type=float, default=0)
    parser.add_argument("--ffmpeg", default=os.environ.get("FRAMERUDIMENTS_FFMPEG") or shutil.which("ffmpeg"))
    parser.add_argument("--engine", choices=("butterchurn", "projectm"))
    parser.add_argument("--id", dest="ids", action="append", default=[])
    parser.add_argument("--order-seed", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--reanalyse", action="store_true", help="Recompute reports while reusing verified captures")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        if args.limit < 0 or not 64 <= args.edge <= 1024 or not 1 <= args.timeout <= 86400:
            raise ValueError("Invalid limits")
        if args.audio and not args.ffmpeg:
            raise ValueError("FFmpeg is required for supplied music")
        return run(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Controlled analysis stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
