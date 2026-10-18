"""Resumable full-catalogue preset experiment and PrismMoves/PixelCue publication.

This is the *engine*, normally launched via Run-FullPresetAnalysis.ps1.
No models, analysis cache or temporary engine profiles are written outside the
FrameRudiments checkout. Only curated results under visualiser-analysis/ are
eligible for Git; downloaded models stay in ignored ./models/.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import gzip
import hashlib
import html
import json
import os
import random
import secrets
from pathlib import Path
import shutil
import statistics
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "visualiser-analysis" / "full-catalogue"
MODELS = ROOT / "models"
SCHEMA = "framerudiments.full-preset-analysis.v1"
DEFAULT_MODELS = ("clip", "dinov2", "blip", "ocr")
DEFAULT_VLM = ("smolvlm-256m",)
DEFAULT_IMAGES = ("cover-lyrics", "colour-bars", "grid-rings", "text-card")
FULL_IMAGES = ("cover-lyrics", "colour-bars", "grid-rings", "text-card")
DEFAULT_AUDIO = ("programme", "silence")
DEFAULT_INTERVALS = (0.0, 0.5, 1.5, 3.0)
SEMANTIC_BATCH = 8


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_digest(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stable_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    write_atomic(path, stable_json(value))


def read_json(path: Path) -> dict:
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return result


def assert_local_directory(directory: Path) -> Path:
    path = directory.resolve()
    if path == ROOT or not path.is_relative_to(ROOT) or path.is_relative_to(MODELS):
        raise ValueError("The output must be a child of the FrameRudiments repository, outside models/")
    return path


def name_key(name: str) -> str:
    """No unsafe, user-controlled or engine-specific preset names become paths."""
    if not isinstance(name, str) or not name:
        raise ValueError("Preset id must be a nonempty string")
    return digest(name.encode("utf-8"))[:24]


ENGINE_SEQUENCE = ("projectm", "butterchurn")


def alternating_random_order(presets: list[dict], seed: int) -> list[dict]:
    """Shuffle each renderer pool, then alternate while both have pending work.

    Preset identities and experiment settings are unchanged. The seed is kept in
    a separate invocation receipt, *not* in the immutable experiment signature.
    This permits a fresh random pending queue on every resume without treating
    already-completed presets as new jobs.
    """
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError("The schedule seed must be an unsigned 64-bit integer")
    buckets = {engine: [] for engine in ENGINE_SEQUENCE}
    seen = set()
    for preset in presets:
        engine, identity = preset.get("engine"), preset.get("id")
        if engine not in buckets or not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("Expected uniquely identified projectM or Butterchurn presets")
        seen.add(identity)
        buckets[engine].append(preset)
    rng = random.Random(seed)
    for engine in ENGINE_SEQUENCE:
        rng.shuffle(buckets[engine])
    if all(buckets.values()):
        next_engine = rng.choice(ENGINE_SEQUENCE)
    else:
        next_engine = next((name for name in ENGINE_SEQUENCE if buckets[name]), None)
    queue = []
    while next_engine is not None:
        queue.append(buckets[next_engine].pop())
        other = next(name for name in ENGINE_SEQUENCE if name != next_engine)
        if buckets[other]:
            next_engine = other
        elif not buckets[next_engine]:
            next_engine = None
    return queue


def record_schedule(out: Path, queue: list[dict], signature: str, seed: int) -> Path:
    """Retain the exact selection order, including work not reached by --limit.

    This is intentionally in ignored _work/, never part of the committed atlas
    or semantic model evidence. The order may change on a subsequent invocation.
    """
    order = [{"id": row["id"], "engine": row["engine"]} for row in queue]
    receipt = {
        "schema": SCHEMA + ".invocation-schedule.v1",
        "experiment_signature": signature,
        "seed": seed,
        "selected_total": len(order),
        "by_engine": {engine: sum(x["engine"] == engine for x in order)
                      for engine in ENGINE_SEQUENCE},
        "starting_engine": order[0]["engine"] if order else None,
        "order_sha256": digest(stable_json(order)),
        "order": order,
        "note": "Pending queue randomized for this invocation only; completed presets not replayed.",
    }
    path = out / "_work" / "schedules" / f"invocation-{time.time_ns()}-{seed:016x}.json"
    write_json(path, receipt)
    return path


def model_environment(models: Path = MODELS) -> dict[str, str]:
    root = models.resolve()
    settings = {
        "PIXELCUE_MODELS_DIR": root,
        "HF_HOME": root / ".cache" / "huggingface",
        "HF_HUB_CACHE": root / ".cache" / "huggingface" / "hub",
        "HUGGINGFACE_HUB_CACHE": root / ".cache" / "huggingface" / "hub",
        "HF_XET_CACHE": root / ".cache" / "huggingface" / "xet",
        "HF_DATASETS_CACHE": root / ".cache" / "huggingface" / "datasets",
        "TRANSFORMERS_CACHE": root / ".cache" / "huggingface" / "hub",
        "TORCH_HOME": root / ".cache" / "torch",
        "XDG_CACHE_HOME": root / ".cache",
        "UV_CACHE_DIR": root / ".cache" / "uv",
        "PIP_CACHE_DIR": root / ".cache" / "pip",
        "TESSDATA_PREFIX": root / "tesseract",
        "FRAMERUDIMENTS_BROWSER_PROFILE_ROOT": root / "browser-profiles",
    }
    for path in settings.values():
        path.mkdir(parents=True, exist_ok=True)
        if not path.resolve().is_relative_to(root):
            raise ValueError("A dependency cache escaped project-local models/")
    environment = dict(os.environ)
    # A legacy user override would otherwise redirect *all* PixelCue VLMs
    # to one potentially external directory and make them overwrite one another.
    environment.pop("PIXELCUE_MODEL_DIR", None)
    environment.update({name: str(path) for name, path in settings.items()})
    return environment


@contextmanager
def exclusive_run(work: Path):
    """OS-held lock (released by a process crash; no stale PID lock)."""
    work.mkdir(parents=True, exist_ok=True)
    lockfile = work / ".run.lock"
    with lockfile.open("r+b" if lockfile.exists() else "w+b") as handle:
        if not lockfile.stat().st_size:
            handle.write(b"0")
            handle.flush()
        if os.name == "nt":
            import msvcrt
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another preset analysis process is already running") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Another preset analysis process is already running") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def run_logged(command: list[str], log: Path, *, environment: dict[str, str],
               timeout: int, on_poll=None) -> int:
    """One independently killable subprocess, with periodic safe progress polling.

    on_poll runs on the parent process, not in a renderer thread, so it can
    inspect atomic capture receipts without sharing OpenGL or a browser.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as output:
        output.write(("\n" + " ".join(str(part) for part in command) + "\n").encode("utf-8", "replace"))
        output.flush()
        options = {"cwd": ROOT, "env": environment, "stdout": output, "stderr": subprocess.STDOUT}
        if os.name == "nt":
            options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(command, **options)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    result = process.wait(timeout=max(.01, min(4, deadline - time.monotonic())))
                    if on_poll is not None:
                        on_poll()
                    return result
                except subprocess.TimeoutExpired:
                    if on_poll is not None:
                        on_poll()
                    if time.monotonic() >= deadline:
                        break
        except KeyboardInterrupt:
            _stop_process_tree(process, output)
            output.write(b"\nINTERRUPTED by operator; child process tree stopped\n")
            raise
        except BaseException:
            # A progress-hook/file I/O failure must not orphan a native GPU,
            # browser or model subprocess when the parent stops monitoring it.
            _stop_process_tree(process, output)
            raise
        _stop_process_tree(process, output)
        output.write(f"\nTIMEOUT after {timeout} seconds\n".encode())
        return 124


def _stop_process_tree(process, log):
    """Terminate native engine + owned descendants on timeout/interrupt."""
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=log, stderr=subprocess.STDOUT, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=30)


def write_current_progress(out: Path, preset: dict, phase: str, *,
                           elapsed: float = 0, log: Path | None = None, **details) -> dict:
    """Human-readable, atomically replaced heartbeat under ignored _work/."""
    relative_log = log.relative_to(out).as_posix() if log is not None else None
    entry = {
        "schema": SCHEMA + ".current-job.v1",
        "preset_id": preset["id"], "engine": preset["engine"],
        "name": preset.get("name", ""), "phase": phase,
        "seconds_elapsed": round(elapsed, 1), "updated_unix": time.time(),
        "log": relative_log,
        **details,
    }
    write_json(out / "_work" / "current-job.json", entry)
    return entry


def tail_failed_log(path: Path, *, lines: int = 9, max_chars: int = 1800) -> str:
    """Best-effort diagnostic; original full log stays in ignored _work."""
    try:
        # Only read a bounded tail even when a renderer emits huge logs.
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 16384))
            message = stream.read().decode("utf-8", "replace")
        return "\n".join(message.splitlines()[-lines:])[-max_chars:]
    except OSError:
        return "(log unavailable)"


def get_preset_catalogue(path: Path) -> dict:
    content = read_json(path)
    if content.get("schema") != "framerudiments.preset-library.v1":
        raise ValueError("Expected framerudiments.preset-library.v1")
    rows = content.get("presets")
    if not isinstance(rows, list) or not rows:
        raise ValueError("The installed preset library must contain presets")
    ids = [r.get("id") for r in rows]
    if len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError("Preset identities must be unique nonempty strings")
    return content


def including_builtin_presets(library: dict) -> dict:
    """Include six bundled Butterchurn/projectM examples, not only upstream packs."""
    from framerudiments.visualisers import ASSETS

    expanded = dict(library)
    rows = list(library["presets"])
    for engine, extension in (("butterchurn", "json"), ("projectm", "milk")):
        for name in ("feedback", "kaleidoscope", "julia"):
            path = ASSETS / f"{name}.{extension}"
            rows.append({
                "id": f"builtin:{engine}:{name}", "engine": engine, "name": f"Built-in {name}",
                "preset": {"path": str(path.resolve()), "sha256": file_digest(path)},
                "aliases": [], "source_pack": "framerudiments-builtin",
                "licence": "See FrameRudiments source and upstream runtime licences",
                "compatibility": "To be established by actual capture",
            })
    expanded["presets"] = rows
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Built-in presets conflict with installed preset identities")
    return expanded


def prepare_synthetic_suite(work: Path, font: Path) -> Path:
    """No private songs: pinned, reproducible demo artwork, audio and LRC."""
    from framerudiments.demo import create_demo
    from framerudiments.io import read_json as read_project, write_json as write_project, sha256

    folder = work / "fixtures" / "synthetic"
    source = folder / "project.json"
    if not source.exists():
        folder.parent.mkdir(parents=True, exist_ok=True)
        if folder.exists():
            raise ValueError(f"Incomplete synthetic fixture directory: {folder}")
        create_demo(folder, font, width=160, height=160)
        data = read_project(source)
        data["background"] = {"image": {"path": "sprite.png", "sha256": sha256(folder / "sprite.png")}}
        write_project(source, data)
    suite = work / "fixtures" / "suite.json"
    manifest = {"schema": "framerudiments.preset-suite.v1",
                "projects": [{"path": str(source.resolve()), "sha256": file_digest(source)}],
                "input_mix": 0.35, "seed": 7}
    if suite.is_file():
        if read_json(suite) != manifest:
            raise ValueError("Synthetic fixture changed; do not overwrite prior experiment input")
    else:
        write_json(suite, manifest)
    return suite


def prepare_artwork_suite(work: Path, font: Path, directory: Path) -> Path:
    """Test supplied real cover images against a *declared synthetic* audio source.

    A full authored music/lyrics programme uses --suite instead. The generated
    projects and source filename receipts remain in Git-ignored _work/. Only
    the provided images are selected; no personal folders are scanned implicitly.
    """
    from framerudiments.demo import create_demo
    from framerudiments.io import read_json as read_project, write_json as write_project

    folder = Path(directory).expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("--artwork-directory must name an existing folder")
    supported = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    covers = sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in supported),
                    key=lambda p: p.relative_to(folder).as_posix().casefold())
    if not covers or len(covers) > 48:
        raise ValueError("Artwork directory must contain between 1 and 48 supported images")
    for cover in covers:
        if cover.stat().st_size > 64 * 1024**2:
            raise ValueError(f"Artwork exceeds 64 MiB: {cover.name}")
    baseline = prepare_synthetic_suite(work, font)
    entries = list(read_json(baseline)["projects"])
    home = work / "fixtures" / "artworks"
    home.mkdir(parents=True, exist_ok=True)
    for cover in covers:
        checksum = file_digest(cover)
        project_key = digest((str(cover.relative_to(folder)) + checksum).encode("utf-8"))[:24]
        candidate = home / project_key / "project.json"
        if not candidate.is_file():
            if candidate.parent.exists():
                raise ValueError(f"Incomplete artwork test fixture: {candidate.parent}")
            generated = create_demo(candidate.parent, font, width=160, height=160)
            doc = read_project(generated)
            doc["background"] = {"image": {"path": str(cover), "sha256": checksum}}
            doc["title"] = f"Artwork conditioning: {cover.stem[:100]}"
            doc["artist"] = "Supplied cover with synthetic test audio"
            doc["notes"] = ("A real supplied album artwork with synthetic test audio and lyrics. "
                            "No actual artist programme or musical response is inferred.")
            write_project(candidate, doc)
        else:
            doc = read_project(candidate)
            if doc.get("background", {}).get("image") != {"path": str(cover), "sha256": checksum}:
                raise ValueError(f"Artwork fixture has changed: {cover.name}")
        entries.append({"path": str(candidate.resolve()), "sha256": file_digest(candidate)})
    content = {"schema": "framerudiments.preset-suite.v1", "projects": entries,
               "input_mix": 0.35, "seed": 7}
    target = work / "fixtures" / "artwork-suite.json"
    if target.exists() and read_json(target) != content:
        raise ValueError("Artwork selection changed. Archive the existing run and use a fresh output directory")
    if not target.exists():
        write_json(target, content)
    return target


def _archive_without_overwrite(source: Path, destination: Path) -> None:
    """Relocate an old receipt without ever replacing an existing archived byte.

    A crashed Windows migration can leave some files in .staging and the rest
    at the output root. We merge only disjoint names, including nested work
    folders. Existing destination names are never silently overwritten.
    """
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not source.is_dir() or not destination.is_dir():
            raise ValueError(f"Archive name collision; original kept unchanged: {source}")
        for child in sorted(source.iterdir()):
            _archive_without_overwrite(child, destination / child.name)
        # Windows may have an open directory handle: only remove an EMPTY
        # directory when permitted, leaving the data-free folder otherwise.
        try:
            source.rmdir()
        except OSError:
            if any(source.iterdir()):
                raise
    else:
        shutil.move(str(source), str(destination))


def _verified_archive(archive: Path, signature: str) -> dict[str, dict]:
    """Verify that all indexed old reports survived, even after an interrupted move."""
    prior = read_json(archive / "experiment.json")
    if prior.get("signature") != signature:
        raise ValueError("The old experiment signature does not match its archive directory")
    index = archive / "index.json"
    archived_rows = existing_rows(archive, signature)
    if index.is_file():
        entries = read_json(index).get("entries", [])
        if not isinstance(entries, list) or {x["id"] for x in entries} != set(archived_rows):
            raise ValueError("Previous experiment archive is missing indexed reports; no new run started")
        if any(archived_rows[x["id"]]["interrupted"] for x in entries):
            raise ValueError("Previous experiment archive has missing/changed previews or semantic receipts")
    return archived_rows


def upgrade_experiment(out: Path, settings: dict, *, enabled: bool) -> None:
    """Losslessly upgrade an old pilot, including an already interrupted Windows move.

    Windows can deny a rename of an *entire populated directory* even when
    the earlier file moves succeeded (WinError 5, antivirus/indexer handles).
    Never rename that directory again: on a new upgrade, archive straight into
    its final name; on a retry, adopt the original .staging folder as a durable
    archive. The old experiment is verified before any new settings are written.
    """
    experiment = out / "experiment.json"
    target_signature = digest(stable_json(settings))
    previous_root = out / "_work" / "previous-experiments"
    previous = read_json(experiment) if experiment.is_file() else None
    if previous and previous.get("settings") == settings and previous.get("signature") == target_signature:
        return

    # A previous attempt may have moved experiment.json into .staging *before*
    # Windows rejected the final rename. Detect this before assuming a fresh run.
    candidate = None
    if previous is None and previous_root.is_dir():
        incomplete = [p for p in previous_root.iterdir() if p.is_dir() and
                      (p.name.endswith(".staging") or not (p / "migration.json").is_file()) and
                      (p / "experiment.json").is_file()]
        if len(incomplete) > 1:
            raise ValueError("Multiple incomplete previous experiments; manual review required")
        if incomplete:
            candidate = incomplete[0]
            previous = read_json(candidate / "experiment.json")
        else:
            # After archiving, a crash before writing the new experiment is
            # harmless, but restarting with DIFFERENT new settings is not.
            completed = [read_json(p / "migration.json") for p in previous_root.iterdir()
                         if p.is_dir() and (p / "migration.json").is_file()]
            if completed and target_signature not in {p.get("target_signature") for p in completed}:
                raise ValueError("An earlier coverage upgrade already selected different settings")
    if previous is None:
        return
    if not enabled:
        raise ValueError("Capture/semantic coverage changed or upgrade was interrupted; "
                         "rerun with --upgrade-coverage to preserve the old pilot")
    prior_signature = previous.get("signature")
    if not isinstance(prior_signature, str) or len(prior_signature) != 64:
        raise ValueError("Existing experiment has no trusted signature: refusing upgrade")

    standard = previous_root / prior_signature
    staging = previous_root / (prior_signature + ".staging")
    if standard.exists() and staging.exists():
        raise ValueError("Both archived and staged copies exist; refusing to combine unknown evidence")
    archive = staging if staging.exists() else standard
    if candidate is not None and archive != candidate:
        raise ValueError("Interrupted upgrade signature does not match its staging directory")
    # Old code left .staging containing the moved material. Treat it as the
    # final archive *in place*, rather than renaming a directory Windows locks.
    archive.mkdir(parents=True, exist_ok=True)
    for name in ("experiment.json", "index.json", "index.html", "SUMMARY.md", "reports", "previews"):
        _archive_without_overwrite(out / name, archive / name)
    _archive_without_overwrite(out / "_work" / "jobs", archive / "jobs")
    archived_rows = _verified_archive(archive, prior_signature)
    write_json(archive / "migration.json", {
        "schema": SCHEMA + ".migration.v1", "source_signature": prior_signature,
        "target_signature": target_signature, "preserved_reports": len(archived_rows),
        "complete": True,
        "note": "Original evidence retained; no weights, jobs or raw captures discarded",
    })
    (out / "reports").mkdir(exist_ok=True)
    (out / "previews").mkdir(exist_ok=True)
    print(f"Preserved old experiment ({len(archived_rows)} reports) at {archive}", flush=True)


def ensure_workspace(out: Path) -> Path:
    out = assert_local_directory(out)
    (out / "_work").mkdir(parents=True, exist_ok=True)
    (out / "reports").mkdir(exist_ok=True)
    (out / "previews").mkdir(exist_ok=True)
    # Generated work can be terabytes; never stage it, weights or executable presets.
    ignore = "_work/\n*.tmp\n"
    path = out / ".gitignore"
    if path.exists() and path.read_text(encoding="utf-8") != ignore:
        raise ValueError("Analysis .gitignore was modified; refusing to replace protections")
    if not path.exists():
        write_atomic(path, ignore.encode())
    return out


def experiment_settings(catalogue: Path, suite: Path, args) -> dict:
    return {
        "schema": SCHEMA,
        "catalogue_sha256": file_digest(catalogue),
        "suite_sha256": file_digest(suite),
        "intervals": list(args.intervals), "images": list(args.images),
        "audio_modes": list(args.audio_modes),
        "models": list(args.models), "keyword_models": list(args.keyword_models),
        "device": args.device, "max_edge": args.max_edge, "semantic_edge": args.semantic_edge,
        "semantic_images": args.semantic_images,
        "artwork_directory": str(Path(args.artwork_directory).resolve()) if getattr(args, "artwork_directory", None) else None,
        "include_builtin": args.include_builtin,
        "render_python": str(Path(args.render_python).resolve()),
        "analysis_python": str(Path(args.analysis_python).resolve()),
        "runner_schema": 3,
    }


def validate_experiment(out: Path, settings: dict) -> str:
    experiment_file = out / "experiment.json"
    signature = digest(stable_json(settings))
    if experiment_file.exists():
        current = read_json(experiment_file)
        if current.get("signature") != signature or current.get("settings") != settings:
            raise ValueError("Inputs, models or render conditions changed; use a new output directory")
    else:
        write_json(experiment_file, {"signature": signature, "settings": settings})
    return signature


def semantic_sources(manifest: dict, limit: int) -> list[Path]:
    chosen = []
    conditions = manifest.get("conditions", [])
    priorities = sorted(conditions, key=lambda c: (
        c.get("imagery") != "cover-lyrics", c.get("audio_mode") != "programme", c.get("sample", 0)
    ))
    for condition in priorities:
        captures = condition.get("captures", [])
        if condition.get("state") != "captured":
            continue
        for capture in sorted(captures, key=lambda item: item.get("output_seconds", 0), reverse=True):
            path = Path(capture.get("output", {}).get("path", ""))
            if path.is_file() and file_digest(path) == capture["output"]["sha256"] and path not in chosen:
                chosen.append(path)
                if len(chosen) == limit:
                    return chosen
    return chosen


def semantic_capture_records(manifest: dict) -> tuple[list[dict], list[str]]:
    """Bind every output image to its exact experiment, control and frame.

    Invalid or incomplete captures are recorded as missing evidence rather than
    silently replaced with other frames, so the final result becomes partial.
    """
    entries, problems = [], []
    for condition in manifest.get("conditions", []):
        label = str(condition.get("id", "unknown"))
        if condition.get("state") != "captured":
            problems.append(f"Condition {label} is not captured")
            continue
        for capture in condition.get("captures", []):
            source = capture.get("output", {})
            name, expected = source.get("path"), source.get("sha256")
            frame = capture.get("frame")
            if not isinstance(name, str) or not isinstance(expected, str):
                problems.append(f"Missing output reference at {label} frame {frame}")
                continue
            path = Path(name)
            if not path.is_file() or file_digest(path) != expected:
                problems.append(f"Changed or missing capture: {label} frame {frame}")
                continue
            entries.append({
                "condition_id": label, "sample": condition.get("sample"),
                "title": condition.get("title"),
                "imagery": condition.get("imagery"), "audio_mode": condition.get("audio_mode"),
                "frame": frame, "output_seconds": capture.get("output_seconds"),
                "sha256": expected, "path": path,
            })
    return entries, problems


def semantic_plan(manifest: dict, limit: int = 0) -> tuple[list[dict], dict]:
    """All output frames by default; a positive limit opts into fair sampling.

    Sampling round-robins actual experimental conditions, not just the final
    cover-art frame. It remains explicit in every stored coverage receipt.
    """
    rows, errors = semantic_capture_records(manifest)
    if limit < 0:
        raise ValueError("Semantic image limit cannot be negative")
    chosen = rows
    if limit and len(rows) > limit:
        groups = defaultdict(list)
        for row in rows:
            groups[row["condition_id"]].append(row)
        for values in groups.values():
            values.sort(key=lambda r: (float(r.get("output_seconds") or 0), str(r["sha256"])))
        # Prefer a mature frame for each condition, then sample early/mid frames.
        chosen = []
        depth = 0
        while len(chosen) < limit:
            progressed = False
            for values in groups.values():
                # Descending from latest through earlier evidence in each condition.
                if depth < len(values):
                    chosen.append(values[-1 - depth])
                    progressed = True
                    if len(chosen) == limit:
                        break
            if not progressed:
                break
            depth += 1
    return chosen, {
        "available_outputs": len(rows), "selected_outputs": len(chosen),
        "conditions": len({x["condition_id"] for x in rows}),
        "sampled": bool(limit and len(rows) > limit), "missing_or_failed": errors,
        "selected_sha256": [r["sha256"] for r in chosen],
    }


def semantic_batches(records: list[dict], batch_size: int = SEMANTIC_BATCH) -> list[list[dict]]:
    """Inference is per unique byte-identical image, with full bindings retained."""
    unique = {}
    for row in records:
        unique.setdefault(row["sha256"], row)
    selected = list(unique.values())
    return [selected[start:start + batch_size] for start in range(0, len(selected), batch_size)]


def reusable_prism_report(path: Path, rows: list[dict], required: set[str]) -> dict | None:
    if not path.is_file():
        return None
    try:
        document = read_json(path)
        items = document.get("items", [])
        expected = {r["sha256"] for r in rows}
        actual = {item.get("analysis", {}).get("source", {}).get("sha256") for item in items}
        if len(items) != len(rows) or expected != actual:
            return None
        for item in items:
            if item.get("status") != "completed":
                return None
            models = item.get("analysis", {}).get("models", [])
            if {m.get("name") for m in models} != required or any(
                m.get("status") != "completed" for m in models
            ):
                return None
        return document
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _median(values):
    values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return statistics.median(values) if values else None


def coherent_synthesis(manifest: dict, response: dict | None, semantic: dict | None) -> dict:
    """Evidence-linked explanation of what the preset changes across conditions.

    This is *not* a new semantic model or an invented aesthetic score. It
    organises existing PixelCue measurements and PrismMoves hypotheses into a
    cross-input/time/control narrative, retaining the boundaries of inference.
    """
    examples = (semantic or {}).get("examples", [])
    quantified = (response or {}).get("conditions", [])
    imagery = defaultdict(list)
    temporal = []
    times = set()
    observed = 0
    for condition in quantified:
        for point in condition.get("source_to_output", []):
            observed += 1
            times.add(point.get("output_seconds"))
            imagery[str(condition.get("imagery", "unknown"))].append(point)
        for transition in condition.get("temporal", []):
            v = transition.get("comparison", {})
            if isinstance(v.get("texture_lbp_js_divergence_bits"), (int, float)):
                temporal.append(float(v["texture_lbp_js_divergence_bits"]))
    axes = {}
    for image_type, pairs in sorted(imagery.items()):
        axes[image_type] = {
            "observations": len(pairs),
            "hue_js_divergence_bits_median": _median(p.get("hue_js_divergence_bits") for p in pairs),
            "texture_lbp_js_divergence_bits_median": _median(
                p.get("texture_lbp_js_divergence_bits") for p in pairs),
            "saturation_absolute_difference_median": _median(
                p.get("saturation_absolute_difference") for p in pairs),
        }
    captions = Counter()
    tags = Counter()
    prompts = defaultdict(Counter)
    captions_by_condition = defaultdict(Counter)
    for example in examples:
        if example.get("status") != "completed":
            continue
        condition = str(example.get("condition_id", "unknown"))
        for caption in example.get("caption_hypotheses", []):
            item = " ".join(str(caption).strip().split())[:250]
            if item:
                captions[item] += 1
                captions_by_condition[condition][item] += 1
        for tag in set(str(t).strip() for t in example.get("tags", [])):
            if tag and tag.casefold() not in {"sfw", "nsfw", "nsfw.", "unspecified"}:
                tags[tag] += 1
        for axis, row in example.get("prompt_hypotheses", {}).items():
            if row.get("prompt"):
                prompts[axis][str(row["prompt"])] += 1
    controls = []
    for item in (response or {}).get("controlled_comparisons", []):
        if "programme versus silence" in str(item.get("relation", "")):
            controls.extend(item.get("comparisons", []))
    control_hue = _median(x.get("comparison", {}).get("hue_js_divergence_bits") for x in controls)
    def fmt(value, suffix=""):
        return (f"{value:.3f}{suffix}" if isinstance(value, (float, int)) else "unavailable")
    conditions_total = len(manifest.get("conditions", []))
    times_desc = ", ".join(f"{float(x):g}" for x in sorted(x for x in times if x is not None))
    cover_count = sum(c.get("imagery") == "cover-lyrics" for c in manifest.get("conditions", []))
    scope = (f"Observed {observed} paired source/output measurements across "
             f"{conditions_total} declared conditions, including {cover_count} cover/lyric conditions"
             f" and output times {times_desc or 'not available'} seconds.")
    parts = []
    for family, values in axes.items():
        parts.append(f"{family}: hue divergence median {fmt(values['hue_js_divergence_bits_median'])} bits, "
                     f"texture divergence median {fmt(values['texture_lbp_js_divergence_bits_median'])} bits "
                     f"across {values['observations']} observed pairs")
    physical = "Across input families, " + "; ".join(parts) + "." if parts else "No comparable input/output image pairs were measured."
    appearance = ("Repeated model-generated captions included " +
                  "; ".join(f"{label} ({count} frames)" for label, count in captions.most_common(3)) + "."
                  if captions else "Captioning produced no completed output hypotheses.")
    if prompts:
        appearance += " Frequent prompt-relative hypotheses: " + "; ".join(
            f"{axis}: {counts.most_common(1)[0][0]} ({counts.most_common(1)[0][1]}/{len(examples)} frames)"
            for axis, counts in sorted(prompts.items()) if counts
        ) + "."
    if tags:
        appearance += " Frequent optional PixelCue tag hypotheses: " + ", ".join(
            f"{name} ({count})" for name, count in tags.most_common(8)) + "."
    time_text = (f"For successive captures of the same input condition, median measured "
                 f"texture divergence was {fmt(_median(temporal))} bits across {len(temporal)} transitions."
                 if temporal else "No consecutive-capture comparison is available.")
    audio_text = (f"{len(controls)} matched programme-versus-silence comparisons had median "
                  f"hue divergence {fmt(control_hue)} bits. Differences may also reflect "
                  "unseeded projectM rendering or feedback evolution, not just musical response."
                  if controls else "No matched programme-versus-silence comparisons are available; audio response is unknown.")
    return {
        "coverage": {"paired_measurements": observed, "conditions": conditions_total,
                     "times_seconds": sorted(x for x in times if x is not None),
                     "semantic_frames": len(examples)},
        "per_input_family": dict(axes),
        "temporal_texture_median": _median(temporal),
        "programme_silence_hue_median": control_hue,
        "frequent_caption_hypotheses": captions.most_common(5),
        "frequent_tag_hypotheses": tags.most_common(16),
        "frequent_prompt_hypotheses": {key: val.most_common(3) for key, val in prompts.items()},
        "caption_hypotheses_by_condition": {k: v.most_common(3) for k, v in captions_by_condition.items()},
        "scope": scope,
        "observed_effects": physical,
        "appearance_hypotheses": appearance,
        "temporal_behaviour": time_text,
        "audio_control": audio_text,
        "interpretation_limits": (
            "PixelCue features quantify sampled rasters, not subjective quality or creative intention. "
            "BLIP captions, CLIP prompts and VLM tags are fallible per-frame hypotheses. "
            "Still frames cannot establish flash safety or perceived motion quality. "
            "Only an authenticated artist-provided suite can establish behaviour on actual album audio."
        ),
    }

def short_semantic(item: dict) -> dict:
    output = {"image_sha256": item.get("analysis", {}).get("source", {}).get("sha256"),
              "image_name": item.get("name"), "status": item.get("status"),
              "caption_hypotheses": [], "prompt_hypotheses": {}, "tags": [], "ocr_words": [],
              "models": []}
    for model in item.get("analysis", {}).get("models", []):
        entry = {"name": model.get("name"), "status": model.get("status"),
                 "revision": model.get("resolved_revision"), "reason": model.get("reason")}
        output["models"].append(entry)
        if model.get("status") != "completed":
            continue
        result = model.get("result", {})
        if result.get("caption"):
            output["caption_hypotheses"].append(str(result["caption"])[:750])
        if result.get("tags"):
            output["tags"].extend(str(x)[:90] for x in result["tags"][:60])
        if result.get("words"):
            output["ocr_words"].extend(str(x.get("text", ""))[:90] for x in result["words"][:40])
        for axis, choices in result.get("axes", {}).items():
            if choices:
                output["prompt_hypotheses"][axis] = {
                    "prompt": choices[0].get("prompt"),
                    "cosine_similarity": choices[0].get("cosine_similarity"),
                    "interpretation": "Prompt-relative candidate, not a calibrated probability",
                }
    return output


def make_contact_sheets(out: Path, key: str, captures: dict) -> list[dict]:
    """Compact, labelled, Git-friendly proof of EVERY retained output frame.

    Raw PNGs remain under ignored _work; each contact sheet represents at most
    18 capture frames. This avoids millions of individual preview files.
    """
    from PIL import Image, ImageDraw, ImageFont

    rows, _ = semantic_capture_records(captures)
    if not rows:
        return []
    panels = []
    width, image_height, label_height = 186, 126, 36
    font = ImageFont.load_default()
    for group_index, start in enumerate(range(0, len(rows), 18)):
        selection = rows[start:start + 18]
        columns = 3
        row_count = (len(selection) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * width, row_count * (image_height + label_height)), "#0e1725")
        draw = ImageDraw.Draw(sheet)
        for i, record in enumerate(selection):
            x, y = (i % columns) * width, (i // columns) * (image_height + label_height)
            with Image.open(record["path"]) as original:
                image = original.convert("RGB")
                image.thumbnail((width - 8, image_height - 8), Image.Resampling.LANCZOS)
                px = x + (width - image.width) // 2
                py = y + (image_height - image.height) // 2
                sheet.paste(image, (px, py))
            title = f"{record['sample']} {record['imagery']} / {record['audio_mode']}"
            moment = f"{float(record.get('output_seconds') or 0):.3f}s | frame {record.get('frame')}"
            draw.text((x + 4, y + image_height), title[:30], font=font, fill="#e7f4ff")
            draw.text((x + 4, y + image_height + 14), moment[:35], font=font, fill="#a1d3de")
        name = f"previews/{key}{'-' + str(group_index + 1).zfill(2) if group_index else ''}.webp"
        target = out / name
        tmp = target.with_name(target.stem + ".tmp.webp")
        sheet.save(tmp, "WEBP", quality=68, method=4)
        os.replace(tmp, target)
        panels.append({"path": name, "sha256": file_digest(target), "frames": len(selection),
                       "conditions": list(dict.fromkeys(r["condition_id"] for r in selection))})
        sheet.close()
    return panels

def publish_record(out: Path, preset: dict, signature: str, captures: dict,
                   response: dict | None, semantic: dict | None, *, error: str = "",
                   required_models: tuple[str, ...] = ()) -> dict:
    key = name_key(preset["id"])
    captured = captures.get("state") == "captured" and captures.get("complete") is True
    quantitative = response is not None and response.get("state") == "completed"
    examples = semantic.get("examples", [semantic]) if semantic else []
    models_ok = bool(examples) and all(
        item.get("status") == "completed" and bool(item.get("models")) and
        all(model.get("status") == "completed" for model in item["models"])
        for item in examples
    )
    if required_models:
        expected = set(required_models)
        models_ok = models_ok and all(
            len(item.get("models", [])) == len(expected)
            and {model.get("name") for model in item["models"]} == expected
            for item in examples
        )
    coverage = (semantic or {}).get("coverage", {})
    if coverage and (coverage.get("missing_or_failed") or
                     coverage.get("selected_outputs") != len(examples)):
        models_ok = False
    status = "complete" if captured and quantitative and models_ok else "partial"
    contact_sheets = make_contact_sheets(out, key, captures)
    preview_name = contact_sheets[0]["path"] if contact_sheets else None
    preview_sha256 = contact_sheets[0]["sha256"] if contact_sheets else None
    synthesis = coherent_synthesis(captures, response, semantic)
    semantic_receipt = out / "reports" / f"{key}.pixelcue.json.gz"
    semantic_sha256 = file_digest(semantic_receipt) if semantic_receipt.is_file() else None
    numeric = None
    if response:
        numeric = {
            "measured_response_ranges": response.get("measured_response_ranges"),
            "measured_axis_change_ranges": response.get("measured_axis_change_ranges"),
            "conditions": [{k: v for k, v in item.items() if k in
                            {"id", "sample", "imagery", "audio_mode", "state", "source_to_output", "temporal", "error"}}
                           for item in response.get("conditions", [])],
            "controlled_comparisons": response.get("controlled_comparisons"),
            "unknowns": response.get("unknowns", []),
        }
    report = {
        "schema": SCHEMA + ".preset",
        "experiment_signature": signature,
        "id": preset["id"], "name": preset["name"], "engine": preset["engine"],
        "source_pack": preset.get("source_pack"), "licence": preset.get("licence"),
        "aliases": [{"pack": x.get("pack"), "original": x.get("original"), "name": x.get("name")}
                    for x in preset.get("aliases", [])],
        "preset_sha256": preset["preset"]["sha256"],
        "status": status, "capture_state": captures.get("state"),
        "analysis_state": response.get("state") if response else "unavailable",
        "preview": preview_name, "preview_sha256": preview_sha256,
        "contact_sheets": contact_sheets,
        "synthesis": synthesis,
        "semantic": semantic, "semantic_receipt_sha256": semantic_sha256,
        "required_models": list(required_models),
        "quantitative": numeric,
        "error": error[:1200],
        "limits": ["Semantic captions, tags and prompts are model hypotheses, not verified facts.",
                   "Static captures cannot establish motion comfort, flash safety or artistic intent.",
                   "Renderer/GPU-dependent results require validation on the target system."],
    }
    write_json(out / "reports" / f"{key}.json", report)
    main_caption = next(iter((semantic or {}).get("caption_hypotheses", [])), "No completed model caption")
    prompts = [f"{k}: {v.get('prompt')}" for k, v in (semantic or {}).get("prompt_hypotheses", {}).items()]
    safe_title = html.escape(" ".join(str(preset["name"]).split())[:240])
    text = [f"# {safe_title}", "", f"- Identity: `{html.escape(preset['id'])}`",
            f"- Engine: {html.escape(preset['engine'])}",
            f"- Status: **{status}**",
            "", "## What this preset does across the tested inputs", "",
            html.escape(synthesis["scope"]), "", html.escape(synthesis["observed_effects"]),
            "", "## Repeated model interpretations (not verified facts)", "",
            html.escape(synthesis["appearance_hypotheses"]),
            "", "## Changes over time", "", html.escape(synthesis["temporal_behaviour"]),
            "", "## Music versus silence", "", html.escape(synthesis["audio_control"]),
            "", "## Complete capture overview", ""]
    for image in contact_sheets:
        text.extend([f"![{image['frames']} labelled output frames]"
                     f"(../{image['path']})", ""])
    text += ["", "## Per-input quantitative responses", ""]
    for family, metrics in synthesis["per_input_family"].items():
        text.append(f"- **{html.escape(family)}** ({metrics['observations']} paired observations): "
                    f"median hue divergence {metrics['hue_js_divergence_bits_median']}, "
                    f"texture divergence {metrics['texture_lbp_js_divergence_bits_median']} bits")
    text += ["", "## Individual caption hypothesis", html.escape(main_caption),
             "", "## Prompt-relative visual characteristics",
             *(f"- {html.escape(p)}" for p in prompts),
             "", "## Tag hypotheses",
             html.escape(", ".join((semantic or {}).get("tags", [])) or "No tags available"),
             "", "## OCR hypotheses",
             html.escape(" ".join((semantic or {}).get("ocr_words", [])) or "No OCR result"),
             "", "## Controlled visual response", ""]
    if response:
        for axis, stats in (response.get("measured_response_ranges") or {}).items():
            if stats:
                text.append(f"- {axis}: median {stats['median']:.4f} (n={stats['n']}); "
                            f"range {stats['min']:.4f}–{stats['max']:.4f}")
    else:
        text.append("Not available (renderer capture or analysis failed).")
    text += ["", "## Evidence limits", html.escape(synthesis["interpretation_limits"]),
             *(f"- {x}" for x in report["limits"])]
    if error:
        text += ["", "## Error receipt", f"`{html.escape(error[:300])}`"]
    write_atomic(out / "reports" / f"{key}.md", ("\n".join(text) + "\n").encode("utf-8"))
    # Tags remain model hypotheses, but they are genuinely useful for finding
    # presets with similar motifs/materials in the full searchable catalogue.
    tags = list(dict.fromkeys(str(tag)[:80] for tag in (semantic or {}).get("tags", [])
                              if str(tag).strip()))[:48]
    row = {"id": preset["id"], "name": preset["name"], "engine": preset["engine"],
           "source_pack": preset.get("source_pack"), "key": key, "status": status,
           "preview": preview_name, "preview_sha256": preview_sha256,
           "caption": main_caption[:400], "tags": tags,
           "summary": synthesis["observed_effects"][:500],
           "frames": sum(item["frames"] for item in contact_sheets),
           "contact_sheets": len(contact_sheets),
           "report_sha256": file_digest(out / "reports" / f"{key}.json")}
    return row


def update_index(out: Path, rows: list[dict], *, total: int, signature: str) -> dict:
    counts = {"complete": 0, "partial": 0, "pending": 0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    counts["pending"] = total - len(rows)
    if counts["pending"] < 0:
        raise ValueError("More report entries than installed catalogue presets")
    index = {"schema": SCHEMA + ".index", "experiment_signature": signature,
             "total": total, "processed": len(rows), "counts": counts,
             "entries": sorted(rows, key=lambda x: (x["engine"], x["name"].casefold(), x["id"]))}
    write_json(out / "index.json", index)
    # Use only escaped text for HTML; all local media links use stable computed IDs.
    cards = []
    for row in index["entries"]:
        image = (f'<img loading="lazy" src="{html.escape(row["preview"])}" alt="Preset sample">'
                 if row["preview"] else '<div class="missing">No capture</div>')
        terms = " ".join((row["name"], row["engine"], row["id"], *row.get("tags", []),
                          row.get("summary", ""))).lower()
        visible_tags = ", ".join(row.get("tags", [])[:12])
        cards.append(f'<article data-search="{html.escape(terms, quote=True)}">'
                     f'{image}<h2>{html.escape(row["name"])}</h2><p>{html.escape(row["engine"])} · '
                     f'{html.escape(row["status"])}</p><p>{html.escape(row["caption"])}</p>'
                     f'<p class="tags">Tags (model hypotheses): {html.escape(visible_tags)}</p>'
                     f'<p>{html.escape(row.get("summary", ""))}</p>'
                     f'<p>{row.get("frames", 0)} captured output frames · '
                     f'{row.get("contact_sheets", 0)} contact sheet(s)</p>'
                     f'<a href="reports/{row["key"]}.md">Evidence and cross-input summary</a>'
                     + (f' · <a href="{html.escape(row["preview"])}">All-condition frames</a>'
                        if row.get("preview") else '') + '</article>')
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>FrameRudiments full visualisation preset atlas</title>
<style>body{font:15px system-ui;background:#0d1220;color:#eaf3fa;margin:24px auto;max-width:1480px;padding:0 24px}input{padding:12px;width:min(100%,600px)}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(216px,1fr));gap:16px;margin-top:20px}article{border:1px solid #33465d;background:#172335;border-radius:9px;padding:13px;overflow:hidden}img{width:100%;max-height:300px;object-fit:contain;background:#0b1424}h2{font-size:16px}p{line-height:1.35}a{color:#9bcdff}.missing{height:150px;color:#b9a8a8}</style>
<h1>FrameRudiments preset analysis atlas</h1><p>Measured visual effects with PrismMoves/PixelCue. Captions, tags and prompt matches are model interpretations, not verified descriptions.</p>
<input aria-label="Filter presets" id="q" placeholder="Search presets, engine, status"><main id="results">''' + "".join(cards) + '''</main><script>const input=document.getElementById('q');input.addEventListener('input',()=>{const q=input.value.toLowerCase();for(const card of document.querySelectorAll('article'))card.hidden=!card.dataset.search.includes(q)});</script></html>'''
    write_atomic(out / "index.html", document.encode("utf-8"))
    summary = ["# Full FrameRudiments preset analysis", "", f"Catalogued presets: {total}",
               f"Analysed: {len(rows)}", f"Complete: {counts['complete']}",
               f"Partial: {counts['partial']}", f"Pending: {counts['pending']}",
               "", "The gallery is generated from actual renderer captures and PixelCue/PrismMoves receipts.",
               "Textual tags and captions are hypotheses; unresolved entries remain explicitly partial.",
               "", "## Engine coverage"]
    for engine in sorted({r["engine"] for r in rows}):
        subset = [r for r in rows if r["engine"] == engine]
        summary.append(f"- {engine}: {sum(r['status']=='complete' for r in subset)}/{len(subset)} complete so far")
    write_atomic(out / "SUMMARY.md", ("\n".join(summary) + "\n").encode())
    return index


def existing_rows(out: Path, signature: str) -> dict[str, dict]:
    rows = {}
    previous_index = read_json(out / "index.json") if (out / "index.json").is_file() else {}
    if previous_index and previous_index.get("experiment_signature") != signature:
        raise ValueError("Existing index belongs to a different experiment")
    expected_digests = {x["id"]: x["report_sha256"] for x in previous_index.get("entries", [])}
    for file in (out / "reports").glob("*.json"):
        item = read_json(file)
        if item.get("experiment_signature") != signature:
            raise ValueError("Existing per-preset report belongs to another experiment")
        key = name_key(item["id"])
        if file.stem != key:
            raise ValueError("Report path and preset id disagree")
        if item["id"] in expected_digests and expected_digests[item["id"]] != file_digest(file):
            raise ValueError("Committed analysis report bytes changed outside the runner")
        preview = item.get("preview")
        semantic_sha = item.get("semantic_receipt_sha256")
        receipt = out / "reports" / f"{key}.pixelcue.json.gz"
        contact_sheets = item.get("contact_sheets", [])
        damaged_sheets = any(not (out / entry["path"]).is_file() or
                             file_digest(out / entry["path"]) != entry.get("sha256")
                             for entry in contact_sheets)
        incomplete = (not (out / "reports" / f"{key}.md").is_file() or damaged_sheets or
                      bool(preview and (not (out / preview).is_file() or
                                        file_digest(out / preview) != item.get("preview_sha256"))) or
                      bool(semantic_sha and (not receipt.is_file() or file_digest(receipt) != semantic_sha)))
        rows[item["id"]] = {
            "id": item["id"], "name": item["name"], "engine": item["engine"],
            "source_pack": item.get("source_pack"), "key": key, "status": item["status"],
            "preview": preview, "preview_sha256": item.get("preview_sha256"),
            "interrupted": incomplete,
            "caption": next(iter((item.get("semantic") or {}).get("caption_hypotheses", [])),
                            "No completed model caption")[:400],
            "tags": list(dict.fromkeys(str(tag)[:80]
                                        for tag in (item.get("semantic") or {}).get("tags", [])
                                        if str(tag).strip()))[:48],
            "summary": item.get("synthesis", {}).get("observed_effects", "")[:500],
            "frames": sum(entry["frames"] for entry in contact_sheets),
            "contact_sheets": len(contact_sheets),
            "report_sha256": file_digest(file),
        }
    return rows


def captured_manifest(work: Path, row: dict) -> Path:
    key = name_key(row["id"])
    return work / "jobs" / key / "run" / "presets" / row["id"].replace(":", "-") / "captures.json"


def _report_failed(out: Path, row: dict, signature: str, note: str, existing: dict | None = None) -> dict:
    return publish_record(out, row, signature, existing or {"state": "failed", "complete": False},
                          None, None, error=note)


def run_preset(args, row: dict, signature: str, out: Path, catalogue: Path, suite: Path,
               environment: dict[str, str]) -> dict:
    key = name_key(row["id"])
    folder = out / "_work" / "jobs" / key
    directory = folder / "run"
    folder.mkdir(parents=True, exist_ok=True)
    capture_log = folder / "capture.log"
    existing_capture = captured_manifest(out / "_work", row)
    started = time.monotonic()
    last_manifest_mtime = -1
    last_heartbeat = 0.0

    def progress(phase, *, log=None, **details):
        nonlocal last_heartbeat
        now = time.monotonic()
        state = write_current_progress(out, row, phase, elapsed=now - started,
                                       log=log, **details)
        # Real, periodic feedback; no fabricated ETA or measurements.
        if now - last_heartbeat > 15 or details.get("new_frames"):
            info = f" {details['captured_frames']} captured frames" if "captured_frames" in details else ""
            print(f"  [{row['engine']} {phase}: {state['seconds_elapsed']:.0f}s{info}]", flush=True)
            last_heartbeat = now

    def poll_captures():
        nonlocal last_manifest_mtime
        completed, count, new_frames = 0, 0, False
        if existing_capture.is_file():
            try:
                mtime = existing_capture.stat().st_mtime_ns
                if mtime != last_manifest_mtime:
                    manifest = read_json(existing_capture)
                    sheets = make_contact_sheets(out, key, manifest)
                    frames = sum(sheet["frames"] for sheet in sheets)
                    completed = sum(c.get("state") == "captured" for c in manifest.get("conditions", []))
                    last_manifest_mtime = mtime
                    new_frames = bool(frames)
                    count = frames
                    progress("capturing", log=capture_log, captured_frames=count,
                             completed_conditions=completed, new_frames=new_frames,
                             preview_files=[item["path"] for item in sheets])
                    return
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                # Partial/temporarily inaccessible receipt; next poll retries.
                progress("capturing", log=capture_log, progress_note=str(exc)[:240])
                return
        progress("capturing", log=capture_log)

    progress("capturing", log=capture_log)
    if directory.exists() and not (directory / "experiment.json").is_file():
        orphan = folder / f"orphan-{int(time.time())}"
        directory.rename(orphan)
    command = [str(args.render_python), "-m", "framerudiments", "presets-profile",
               str(suite), str(catalogue), "--output", str(directory),
               "--id", row["id"], "--intervals", *map(str, args.intervals)]
    for image in args.images:
        command.extend(("--image", image))
    for mode in args.audio_modes:
        command.extend(("--audio-mode", mode))
    if directory.exists():
        command += ["--resume"]
        if args.retry_failed:
            command += ["--retry-failed"]
    capture_code = run_logged(command, capture_log, environment=environment,
                              timeout=args.capture_timeout, on_poll=poll_captures)
    if capture_code:
        print(f"  [capture exit {capture_code}] {tail_failed_log(capture_log)}", flush=True)
    if not existing_capture.is_file():
        progress("capture-failed", log=capture_log, exit_code=capture_code)
        return _report_failed(out, row, signature,
                              f"Capture subprocess exit {capture_code}; see ignored _work/jobs/{key}/capture.log")
    try:
        captured = read_json(existing_capture)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _report_failed(out, row, signature, f"Invalid capture manifest: {exc}")
    if captured.get("preset", {}).get("id") != row["id"]:
        raise ValueError("Capture manifest identity mismatch")
    # The renderer has finished. Publish the entire contact sheet now, before
    # running potentially hours of CPU-only PixelCue/PrismMoves inference.
    sheets = make_contact_sheets(out, key, captured)
    progress("capture-complete", log=capture_log, exit_code=capture_code,
             captured_frames=sum(p["frames"] for p in sheets),
             preview_files=[p["path"] for p in sheets])
    if not sheets:
        # Incompatible/failed projectM presets can reject every condition.
        # They must still receive an honest partial receipt, but running heavy
        # numerical and neural stages with zero images wastes minutes to hours.
        failed = [c for c in captured.get("conditions", []) if c.get("state") != "captured"]
        descriptions = [f"{c.get('id', 'unknown')}: {str(c.get('error', c.get('state')))[:150]}"
                        for c in failed[:5]]
        message = (f"No captured outputs; capture exit {capture_code}; "
                   + "; ".join(descriptions))
        progress("capture-failed", log=capture_log, exit_code=capture_code,
                 failed_conditions=len(failed))
        return _report_failed(out, row, signature, message, captured)
    response = None
    numeric_code = -1
    numeric_path = existing_capture.parent / "identity" / "response.json"
    if captured.get("complete"):
        progress("numerical-analysis", log=folder / "numerical.log")
        command = [str(args.analysis_python), str(ROOT / "src" / "framerudiments" / "profile_analysis.py"),
                   str(existing_capture), "--max-edge", str(args.max_edge), "--models-dir", str(MODELS)]
        numeric_code = run_logged(command, folder / "numerical.log", environment=environment,
                                  timeout=args.analysis_timeout,
                                  on_poll=lambda: progress("numerical-analysis", log=folder / "numerical.log"))
        if numeric_code and (numeric_path.parent).exists():
            # An interrupted PrismMoves batch can leave a directory without
            # checksums. Preserve it, then retry from a clean analysis folder.
            archived = folder / f"identity-interrupted-{int(time.time())}"
            numeric_path.parent.rename(archived)
            numeric_code = run_logged(command, folder / "numerical.log", environment=environment,
                                      timeout=args.analysis_timeout,
                                      on_poll=lambda: progress("numerical-analysis-retry", log=folder / "numerical.log"))
        if numeric_path.is_file():
            try:
                response = read_json(numeric_path)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
    # Numeric comparison already uses PixelCue for EVERY captured input/output
    # pair. Semantic inference must additionally cover every distinct output
    # (or a clearly labelled user-requested balanced sample), not just one
    # favoured cover frame. Batch to satisfy PrismMoves' bounded CLI and make
    # partial runs safely resumable even after a model or GPU crash.
    selected, coverage = semantic_plan(captured, args.semantic_images)
    semantic = None
    semantic_code = 0
    if selected:
        semantic_root = folder / "semantic"
        semantic_root.mkdir(exist_ok=True)
        required_models = set(args.models) | {"pixelcue-tagger:" + tagger for tagger in args.keyword_models}
        all_items: dict[str, dict] = {}
        batch_receipts = []
        batches = semantic_batches(selected)
        for index, batch in enumerate(batches):
            progress("semantic-analysis", log=folder / "semantic.log",
                     batch=index + 1, batches=len(batches), images_in_batch=len(batch),
                     total_images=len(selected))
            destination = semantic_root / f"batch-{index:04d}"
            report_path = destination / "analysis.json"
            existing = reusable_prism_report(report_path, batch, required_models)
            if existing is not None:
                result = existing
            elif report_path.is_file() and not args.retry_failed:
                try:
                    result = read_json(report_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    result = {"items": []}
                semantic_code = 2
            else:
                if destination.exists():
                    # All earlier attempts are retained; no evidence is overwritten.
                    attempt = semantic_root / f"archived-{index:04d}-{time.time_ns()}"
                    destination.rename(attempt)
                command = [str(args.analysis_python), "-m", "prismmoves",
                           *(str(row["path"]) for row in batch),
                           "--out", str(destination), "--models-dir", str(MODELS),
                           "--max-edge", str(args.semantic_edge), "--device", args.device,
                           "--role", "visualisation preset output",
                           "--lineage", row["id"], "--models", *args.models]
                if args.keyword_models:
                    command.extend(["--pixelcue-models", *args.keyword_models, "--allow-download"])
                code = run_logged(command, folder / "semantic.log", environment=environment,
                                  timeout=args.semantic_timeout,
                                  on_poll=lambda index=index: progress(
                                      "semantic-analysis", log=folder / "semantic.log",
                                      batch=index + 1, batches=len(batches),
                                      total_images=len(selected)))
                if code:
                    semantic_code = 2
                try:
                    result = read_json(report_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    result = {"items": []}
                    semantic_code = 2
            batch_receipts.append({"batch": index, "images": [r["sha256"] for r in batch],
                                   "report": result})
            expected = {r["sha256"] for r in batch}
            for item in result.get("items", []):
                sha = item.get("analysis", {}).get("source", {}).get("sha256")
                if sha in expected and sha not in all_items:
                    all_items[sha] = item
            if len(expected - set(all_items)):
                semantic_code = 2
        examples = []
        repeated_hashes = Counter(frame["sha256"] for frame in selected)
        for frame in selected:
            original = all_items.get(frame["sha256"])
            if original:
                item = short_semantic(original)
            else:
                item = {"image_sha256": frame["sha256"], "status": "unavailable",
                        "caption_hypotheses": [], "prompt_hypotheses": {}, "tags": [],
                        "ocr_words": [], "models": [{"name": name, "status": "unavailable",
                                                "reason": "No usable PrismMoves/PixelCue output for this capture"}
                                               for name in sorted(required_models)]}
            item.update({key: frame.get(key) for key in
                         ("condition_id", "sample", "title", "imagery", "audio_mode", "frame", "output_seconds")})
            item["reused_exact_sha"] = repeated_hashes[frame["sha256"]] > 1
            examples.append(item)
        semantic = dict(examples[0], examples=examples, coverage=coverage)
        semantic["caption_hypotheses"] = list(dict.fromkeys(
            cap for e in examples for cap in e.get("caption_hypotheses", [])))[:12]
        semantic["tags"] = list(dict.fromkeys(
            tag for e in examples for tag in e.get("tags", [])))[:80]
        semantic["ocr_words"] = list(dict.fromkeys(
            word for e in examples for word in e.get("ocr_words", [])))[:80]
        semantic["models"] = [model for e in examples for model in e.get("models", [])]
        if coverage["missing_or_failed"] or any(e.get("status") != "completed" for e in examples):
            semantic_code = 2
        full_receipt = {
            "schema": SCHEMA + ".semantic-evidence.v2",
            "preset_id": row["id"], "coverage": coverage,
            "frame_bindings": [{k: val for k, val in entry.items() if k != "path"} for entry in selected],
            "batches": batch_receipts,
        }
        compressed = gzip.compress(stable_json(full_receipt), compresslevel=8, mtime=0)
        write_atomic(out / "reports" / f"{key}.pixelcue.json.gz", compressed)
    else:
        semantic_code = 2
    messages = []
    if capture_code:
        messages.append(f"capture exit {capture_code}")
    if numeric_code:
        messages.append(f"numeric analysis exit {numeric_code}")
    if semantic_code:
        messages.append(f"semantic analysis exit {semantic_code}")
    failed_conditions = [c for c in captured.get("conditions", [])
                         if c.get("state") != "captured"]
    for condition in failed_conditions[:8]:
        messages.append(f"{condition.get('id', 'unknown')}: {str(condition.get('error', condition.get('state')))[:150]}")
    if failed_conditions:
        print(f"  {len(failed_conditions)} rendering conditions failed; see {capture_log}", flush=True)
    required = (*args.models, *("pixelcue-tagger:" + name for name in args.keyword_models))
    published = publish_record(out, row, signature, captured, response, semantic,
                               error="; ".join(messages), required_models=required)
    progress("finished", log=folder / "semantic.log", result=published["status"],
             captured_frames=sum(p["frames"] for p in sheets))
    return published


def commit_if_complete(out: Path, index: dict) -> None:
    # All presets must be ATTEMPTED and have audited reports. Some upstream
    # shader dialects are unsupported: an honest failure receipt is still
    # a catalogue result, not a reason to misrepresent or drop the entry.
    if index["counts"]["pending"]:
        raise ValueError("Refusing to commit an incomplete catalogue with pending presets")
    relative = out.relative_to(ROOT).as_posix()
    if Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.strip()).resolve() != ROOT.resolve():
        raise ValueError("Expected the FrameRudiments repository root")
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.splitlines()
    if any(not (name == relative or name.startswith(relative + "/")) for name in staged):
        raise ValueError("Unrelated staged files exist: commit those separately before analysis publication")
    subprocess.run(["git", "add", "-A", "--", relative], cwd=ROOT, check=True)
    subprocess.run(["git", "diff", "--cached", "--check"], cwd=ROOT, check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False)
    if changed.returncode == 1:
        subprocess.run(["git", "commit", "-m", "docs: publish complete visualiser preset analysis atlas"],
                       cwd=ROOT, check=True)


def run(args) -> int:
    out = ensure_workspace(Path(args.output))
    env = model_environment()
    catalogue = Path(args.catalogue).resolve()
    if not catalogue.is_file():
        raise ValueError(f"Install the pinned preset library first: {catalogue}")
    with exclusive_run(out / "_work"):
        if args.suite and getattr(args, "artwork_directory", None):
            raise ValueError("Use --suite for real songs or --artwork-directory for synthetic-audio cover tests, not both")
        if args.suite:
            suite = Path(args.suite).resolve(strict=True)
        else:
            font = find_font(args.font)
            if getattr(args, "artwork_directory", None):
                suite = prepare_artwork_suite(out / "_work", font, Path(args.artwork_directory))
            else:
                suite = prepare_synthetic_suite(out / "_work", font)
        library = get_preset_catalogue(catalogue)
        if args.include_builtin:
            library = including_builtin_presets(library)
        expanded_path = out / "_work" / "full-catalogue.json"
        write_json(expanded_path, library)
        settings = experiment_settings(expanded_path, suite, args)
        upgrade_experiment(out, settings, enabled=getattr(args, "upgrade_coverage", False))
        signature = validate_experiment(out, settings)
        rows = existing_rows(out, signature)
        presets = library["presets"]
        by_name = {r["id"]: r for r in presets}
        if set(rows) - by_name.keys():
            raise ValueError("Existing output includes presets outside the selected catalogue")
        # New invocations reshuffle only remaining work. Completed receipts and
        # partial receipts (unless explicitly retrying) are never reprocessed.
        remaining = []
        for item in presets:
            prior = rows.get(item["id"])
            if not prior or prior.get("interrupted") or (args.retry_failed and prior["status"] != "complete"):
                remaining.append(item)
        seed = (getattr(args, "order_seed", None)
                if getattr(args, "order_seed", None) is not None else secrets.randbits(64))
        queue = alternating_random_order(remaining, seed)
        if queue:
            receipt = record_schedule(out, queue, signature, seed)
            print(f"Random alternating order: {len(queue)} pending, seed={seed}; "
                  f"receipt={receipt.relative_to(out)}", flush=True)
        visited = 0
        for item in queue:
            if args.limit and visited >= args.limit:
                break
            visited += 1
            started = time.monotonic()
            print(f"[pending {visited}/{len(queue)}; total {len(rows)}/{len(presets)}] "
                  f"{item['engine']}: {item['name'][:90]}", flush=True)
            try:
                result = run_preset(args, item, signature, out, expanded_path, suite, env)
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, KeyError) as exc:
                write_current_progress(out, item, "failed", elapsed=time.monotonic() - started,
                                       error=f"{type(exc).__name__}: {str(exc)[:500]}")
                result = _report_failed(out, item, signature, f"{type(exc).__name__}: {exc}")
            rows[item["id"]] = result
            # Each preset JSON/Markdown report is saved atomically. Avoid
            # re-writing an O(N)-sized HTML gallery 14,000+ times (O(N²) IO).
            if visited % getattr(args, "checkpoint_every", 25) == 0:
                update_index(out, list(rows.values()), total=len(presets), signature=signature)
            duration = time.monotonic() - started
            complete = sum(x["status"] == "complete" for x in rows.values())
            partial = len(rows) - complete
            print(f"  {result['status']} ({duration:.1f}s); "
                  f"{complete} complete, {partial} partial, {len(presets)-len(rows)} pending", flush=True)
        index = update_index(out, list(rows.values()), total=len(presets), signature=signature)
        if args.commit and not index["counts"]["pending"]:
            verify(argparse.Namespace(output=out, require_success=False))
            commit_if_complete(out, index)
        print(f"Atlas: {out / 'index.html'}", flush=True)
        print(f"Counts: {index['counts']}", flush=True)
        return 0 if index["counts"]["pending"] == index["counts"]["partial"] == 0 else 2


def find_font(given: str | None) -> Path:
    paths = ([Path(given)] if given else []) + [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for path in paths:
        if path.is_file():
            return path.resolve()
    raise ValueError("Pass --font with the path to a real TTF or OTF font")


def status(args) -> int:
    out = assert_local_directory(Path(args.output))
    running = out / "_work" / "current-job.json"
    current = read_json(running) if running.is_file() else None
    if not (out / "index.json").is_file():
        print(json.dumps({"indexed": False, "current_job": current}, indent=2))
        return 2
    index = read_json(out / "index.json")
    print(json.dumps({"total": index["total"], "processed": index["processed"],
                      "counts": index["counts"], "gallery": str(out / "index.html"),
                      "current_job": current}, indent=2))
    return 0 if index["counts"]["pending"] == index["counts"]["partial"] == 0 else 2


def verify(args) -> int:
    out = assert_local_directory(Path(args.output))
    index = read_json(out / "index.json")
    configuration = read_json(out / "experiment.json")
    if index["experiment_signature"] != configuration["signature"]:
        raise ValueError("Index does not match the recorded experiment")
    if (index["total"] != len(index["entries"]) or index["counts"]["pending"]):
        raise ValueError("Preset atlas is incomplete: pending entries remain")
    if getattr(args, "require_success", False) and index["counts"]["partial"]:
        raise ValueError("Some presets have partial/unavailable results; inspect receipts")
    for row in index["entries"]:
        if row["key"] != name_key(row["id"]):
            raise ValueError(f"Preset report identity/key mismatch: {row['id']}")
        path = out / "reports" / f"{row['key']}.json"
        if row["status"] not in {"complete", "partial"} or file_digest(path) != row["report_sha256"]:
            raise ValueError(f"Missing or altered preset report: {row['id']}")
        report = read_json(path)
        receipt_sha = report.get("semantic_receipt_sha256")
        if receipt_sha and (not (out / "reports" / f"{row['key']}.pixelcue.json.gz").is_file() or
                            file_digest(out / "reports" / f"{row['key']}.pixelcue.json.gz") != receipt_sha):
            raise ValueError(f"Missing or altered semantic receipt: {row['id']}")
        if not (out / "reports" / f"{row['key']}.md").is_file():
            raise ValueError(f"Missing textual report: {row['id']}")
        if row["preview"] and (not (out / row["preview"]).is_file() or
                               file_digest(out / row["preview"]) != row.get("preview_sha256")):
            raise ValueError(f"Missing or altered preview: {row['id']}")
        for sheet in report.get("contact_sheets", []):
            if (not (out / sheet["path"]).is_file() or
                    file_digest(out / sheet["path"]) != sheet["sha256"]):
                raise ValueError(f"Missing or altered contact sheet: {row['id']}")
    print(f"Verified all {index['total']} preset reports; "
          f"{index['counts']['complete']} complete, {index['counts']['partial']} partial; "
          "review failures and licences before committing.")
    return 0


def ensure_library(args) -> int:
    from framerudiments.preset_library import PACKS, install_library, load_library
    from urllib.request import Request, urlopen

    env = model_environment()
    os.environ.update({key: env[key] for key in (
        "PIXELCUE_MODELS_DIR", "HF_HOME", "HF_HUB_CACHE", "TORCH_HOME", "XDG_CACHE_HOME",
        "UV_CACHE_DIR", "PIP_CACHE_DIR", "TESSDATA_PREFIX", "FRAMERUDIMENTS_BROWSER_PROFILE_ROOT")})
    library_dir = Path(args.catalogue).resolve().parent
    catalogue = library_dir / "catalogue.json"
    if catalogue.is_file():
        data = load_library(catalogue)
        print(f"Installed preset library: {len(data['presets'])} deduplicated presets")
        return 0
    cache = MODELS / "preset-archives"
    cache.mkdir(parents=True, exist_ok=True)
    for name, pin in PACKS.items():
        filename = "butterchurn-presets-2.4.7.tgz" if name == "butterchurn" else name + ".zip"
        target = cache / filename
        if target.is_file() and file_digest(target) == pin["sha256"]:
            continue
        part = target.with_suffix(target.suffix + ".part")
        current = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={current}-"} if current else {}
        request = Request(pin["url"], headers=headers)
        print(f"Fetching pinned preset archive {name} (resume byte {current})", flush=True)
        with urlopen(request, timeout=180) as response:
            append = current > 0 and response.status == 206
            if not append:
                current = 0
            with part.open("ab" if append else "wb") as file:
                while chunk := response.read(1024 * 1024):
                    file.write(chunk)
                    current += len(chunk)
                    if current > 64 * 1024 * 1024:
                        raise ValueError(f"Preset archive exceeds 64 MiB: {name}")
        if file_digest(part) != pin["sha256"]:
            raise ValueError(f"Pinned SHA-256 mismatch for {name}; partial file preserved in models/")
        os.replace(part, target)
    if library_dir.exists():
        archived = library_dir.with_name(f"{library_dir.name}-interrupted-{int(time.time())}")
        library_dir.rename(archived)
        print(f"Retained interrupted preset installation at {archived}")
    data = install_library(library_dir, cache=cache)
    print(f"Installed pinned preset library: {len(data['presets']) if isinstance(data, dict) else 'complete'}")
    return 0


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install-library", "run", "status", "verify"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--catalogue", type=Path, default=MODELS / "preset-library" / "catalogue.json")
    parser.add_argument("--suite", type=Path, help="Pinned preset-suite.v1 of multiple real songs and covers")
    parser.add_argument("--artwork-directory", type=Path,
                        help="Explicit local album-cover folder, tested with disclosed synthetic audio/lyrics")
    parser.add_argument("--upgrade-coverage", action="store_true",
                        help="Explicitly archive an earlier pilot under ignored _work/ and restart full coverage")
    parser.add_argument("--font", help="TTF/OTF for the synthetic demo when --suite is omitted")
    parser.add_argument("--render-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--analysis-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--intervals", type=float, nargs="+", default=list(DEFAULT_INTERVALS))
    parser.add_argument("--images", nargs="+", choices=FULL_IMAGES, default=list(DEFAULT_IMAGES))
    parser.add_argument("--audio-modes", nargs="+", choices=DEFAULT_AUDIO, default=list(DEFAULT_AUDIO))
    parser.add_argument("--models", nargs="+", choices=DEFAULT_MODELS, default=list(DEFAULT_MODELS))
    parser.add_argument("--keyword-models", nargs="*", choices=(
        "joycaption", "smolvlm-256m", "smolvlm-500m", "smolvlm2-256m", "smolvlm2-500m"),
        default=list(DEFAULT_VLM))
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), default="cpu")
    parser.add_argument("--max-edge", type=int, default=256)
    parser.add_argument("--semantic-edge", type=int, default=384)
    parser.add_argument("--semantic-images", type=int, default=0,
                        help="0 (default) analyses every output frame; positive values explicitly sample")
    parser.add_argument("--include-builtin", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit", type=int, help="Process at most N pending presets in this invocation")
    parser.add_argument("--order-seed", type=int, default=None,
                        help="Optional reproducible random schedule seed (fresh 64-bit seed by default)")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--commit", action="store_true", help="Commit only after ALL presets have been attempted and receipts verified")
    parser.add_argument("--require-success", action="store_true", help="For verify: require no partial or failed presets")
    parser.add_argument("--checkpoint-every", type=int, default=25, help="Index checkpoint interval (each preset report is still atomic)")
    parser.add_argument("--capture-timeout", type=int, default=1800)
    parser.add_argument("--analysis-timeout", type=int, default=900)
    parser.add_argument("--semantic-timeout", type=int, default=1200)
    args = parser.parse_args(argv)
    args.models = list(dict.fromkeys(args.models))
    args.keyword_models = list(dict.fromkeys(args.keyword_models))
    if args.command == "install-library":
        return ensure_library(args)
    if args.command == "status":
        return status(args)
    if args.command == "verify":
        return verify(args)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.order_seed is not None and not 0 <= args.order_seed < 2**64:
        parser.error("--order-seed must be an unsigned 64-bit integer")
    if not 1 <= args.checkpoint_every <= 1000:
        parser.error("--checkpoint-every must be 1..1000")
    if not 0 <= args.semantic_images <= 10000:
        parser.error("--semantic-images must be 0 (all) or 1..10000")
    if not 32 <= args.max_edge <= 2048 or not 32 <= args.semantic_edge <= 2048:
        parser.error("Analysis edge must be 32..2048")
    if any(not 1 <= t <= 86400 for t in (args.capture_timeout, args.analysis_timeout, args.semantic_timeout)):
        parser.error("Timeouts must be 1..86400 seconds")
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(cli())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Full preset analysis: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
