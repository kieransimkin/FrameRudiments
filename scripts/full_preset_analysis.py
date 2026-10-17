"""Resumable full-catalogue preset experiment and PrismMoves/PixelCue publication.

This is the *engine*, normally launched via Run-FullPresetAnalysis.ps1.
No models, analysis cache or temporary engine profiles are written outside the
FrameRudiments checkout. Only curated results under visualiser-analysis/ are
eligible for Git; downloaded models stay in ignored ./models/.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
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
DEFAULT_IMAGES = ("cover-lyrics", "grid-rings")
FULL_IMAGES = ("cover-lyrics", "colour-bars", "grid-rings", "text-card")
DEFAULT_AUDIO = ("programme", "silence")


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


def run_logged(command: list[str], log: Path, *, environment: dict[str, str], timeout: int) -> int:
    """One independently killable subprocess; returns 124 for a timed-out job."""
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
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            # The owning subprocess and its descendants must not survive a
            # timeout or Ctrl+C and continue changing captured files.
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=output, stderr=subprocess.STDOUT, check=False)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=30)
            if isinstance(exc, KeyboardInterrupt):
                output.write(b"\nINTERRUPTED by operator; child process tree stopped\n")
                raise
            output.write(f"\nTIMEOUT after {timeout} seconds\n".encode())
            return 124


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
        "include_builtin": args.include_builtin,
        "render_python": str(Path(args.render_python).resolve()),
        "analysis_python": str(Path(args.analysis_python).resolve()),
        "runner_schema": 2,
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
    status = "complete" if captured and quantitative and models_ok else "partial"
    preview_name = f"previews/{key}.webp"
    preview_sha256 = None
    sample_image = semantic_sources(captures, 1)
    if sample_image:
        from PIL import Image
        with Image.open(sample_image[0]) as original:
            img = original.convert("RGB")
            img.thumbnail((192, 192), Image.Resampling.LANCZOS)
            path = out / preview_name
            image_tmp = path.with_name(path.stem + ".tmp.webp")
            img.save(image_tmp, "WEBP", quality=73, method=5)
            os.replace(image_tmp, path)
            preview_sha256 = file_digest(path)
    else:
        preview_name = None
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
    text = [f"# {safe_title}", "", f"- Identity: `{preset['id']}`", f"- Engine: {preset['engine']}",
            f"- Status: **{status}**", "", "## Caption hypothesis", main_caption,
            "", "## Prompt-relative visual characteristics", *(f"- {p}" for p in prompts),
            "", "## Tag hypotheses", ", ".join((semantic or {}).get("tags", [])) or "No tags available",
            "", "## OCR hypotheses", " ".join((semantic or {}).get("ocr_words", [])) or "No OCR result",
            "", "## Controlled visual response", "" ]
    if response:
        for axis, stats in (response.get("measured_response_ranges") or {}).items():
            if stats:
                text.append(f"- {axis}: median {stats['median']:.4f} (n={stats['n']}); "
                            f"range {stats['min']:.4f}–{stats['max']:.4f}")
    else:
        text.append("Not available (renderer capture or analysis failed).")
    text += ["", "## Limitations", *(f"- {x}" for x in report["limits"])]
    if error:
        text += ["", "## Error receipt", f"`{error[:300]}`"]
    write_atomic(out / "reports" / f"{key}.md", ("\n".join(text) + "\n").encode("utf-8"))
    # Tags remain model hypotheses, but they are genuinely useful for finding
    # presets with similar motifs/materials in the full searchable catalogue.
    tags = list(dict.fromkeys(str(tag)[:80] for tag in (semantic or {}).get("tags", [])
                              if str(tag).strip()))[:48]
    row = {"id": preset["id"], "name": preset["name"], "engine": preset["engine"],
           "source_pack": preset.get("source_pack"), "key": key, "status": status,
           "preview": preview_name, "preview_sha256": preview_sha256,
           "caption": main_caption[:400], "tags": tags,
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
        terms = " ".join((row["name"], row["engine"], row["id"], *row.get("tags", []))).lower()
        visible_tags = ", ".join(row.get("tags", [])[:12])
        cards.append(f'<article data-search="{html.escape(terms, quote=True)}">'
                     f'{image}<h2>{html.escape(row["name"])}</h2><p>{html.escape(row["engine"])} · '
                     f'{html.escape(row["status"])}</p><p>{html.escape(row["caption"])}</p>'
                     f'<p class="tags">Tags (model hypotheses): {html.escape(visible_tags)}</p>'
                     f'<a href="reports/{row["key"]}.md">Evidence</a></article>')
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>FrameRudiments full visualisation preset atlas</title>
<style>body{font:15px system-ui;background:#0d1220;color:#eaf3fa;margin:24px auto;max-width:1480px;padding:0 24px}input{padding:12px;width:min(100%,600px)}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(216px,1fr));gap:16px;margin-top:20px}article{border:1px solid #33465d;background:#172335;border-radius:9px;padding:13px;overflow:hidden}img{width:100%;aspect-ratio:4/3;object-fit:contain;background:#0b1424}h2{font-size:16px}p{line-height:1.35}a{color:#9bcdff}.missing{height:150px;color:#b9a8a8}</style>
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
        incomplete = (not (out / "reports" / f"{key}.md").is_file() or
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
    capture_code = run_logged(command, capture_log, environment=environment, timeout=args.capture_timeout)
    if not existing_capture.is_file():
        return _report_failed(out, row, signature,
                              f"Capture subprocess exit {capture_code}; see ignored _work/jobs/{key}/capture.log")
    try:
        captured = read_json(existing_capture)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return _report_failed(out, row, signature, f"Invalid capture manifest: {exc}")
    if captured.get("preset", {}).get("id") != row["id"]:
        raise ValueError("Capture manifest identity mismatch")
    response = None
    numeric_code = -1
    numeric_path = existing_capture.parent / "identity" / "response.json"
    if captured.get("complete"):
        command = [str(args.analysis_python), str(ROOT / "src" / "framerudiments" / "profile_analysis.py"),
                   str(existing_capture), "--max-edge", str(args.max_edge), "--models-dir", str(MODELS)]
        numeric_code = run_logged(command, folder / "numerical.log", environment=environment,
                                  timeout=args.analysis_timeout)
        if numeric_code and (numeric_path.parent).exists():
            # An interrupted PrismMoves batch can leave a directory without
            # checksums. Preserve it, then retry from a clean analysis folder.
            archived = folder / f"identity-interrupted-{int(time.time())}"
            numeric_path.parent.rename(archived)
            numeric_code = run_logged(command, folder / "numerical.log", environment=environment,
                                      timeout=args.analysis_timeout)
        if numeric_path.is_file():
            try:
                response = read_json(numeric_path)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
    semantic = None
    semantic_code = -1
    sources = semantic_sources(captured, args.semantic_images)
    if sources:
        semantic_root = folder / "semantic"
        semantic_root.mkdir(exist_ok=True)
        # Never overwrite a failed PrismMoves receipt; each attempt remains reviewable.
        prior = sorted(semantic_root.glob("attempt-*"))
        candidate = prior[-1] if prior else None
        if candidate is not None and (candidate / "analysis.json").is_file():
            try:
                old = read_json(candidate / "analysis.json")
                if old.get("items") and all(x.get("status") == "completed" and all(
                        m.get("status") == "completed" for m in x.get("analysis", {}).get("models", []))
                        for x in old["items"]):
                    semantic = old
                    semantic_code = 0
            except (ValueError, OSError, json.JSONDecodeError):
                pass
        if semantic is None:
            destination = semantic_root / f"attempt-{len(prior):04d}"
            command = [str(args.analysis_python), "-m", "prismmoves", *map(str, sources),
                       "--out", str(destination), "--models-dir", str(MODELS),
                       "--max-edge", str(args.semantic_edge), "--device", args.device,
                       "--role", "visualisation preset", "--lineage", row["id"],
                       "--models", *args.models]
            if args.keyword_models:
                command.extend(["--pixelcue-models", *args.keyword_models, "--allow-download"])
            semantic_code = run_logged(command, folder / "semantic.log", environment=environment,
                                       timeout=args.semantic_timeout)
            if (destination / "analysis.json").is_file():
                try:
                    semantic = read_json(destination / "analysis.json")
                except (ValueError, OSError, json.JSONDecodeError):
                    pass
    # Publish from an actual analysed output; multiple model outputs stay attributable.
    evidence = None
    if semantic and semantic.get("items"):
        items = [short_semantic(item) for item in semantic["items"]]
        evidence = dict(items[0], examples=items)
        evidence["models"] = [m for item in items for m in item["models"]]
        # Keep full Prism/PixelCue model receipts, embeddings and measurements compressed.
        compressed = gzip.compress(stable_json(semantic), compresslevel=8, mtime=0)
        write_atomic(out / "reports" / f"{key}.pixelcue.json.gz", compressed)
    messages = []
    if capture_code:
        messages.append(f"capture exit {capture_code}")
    if numeric_code:
        messages.append(f"numeric analysis exit {numeric_code}")
    if semantic_code:
        messages.append(f"semantic analysis exit {semantic_code}")
    required = (*args.models, *("pixelcue-tagger:" + name for name in args.keyword_models))
    return publish_record(out, row, signature, captured, response, evidence,
                          error="; ".join(messages), required_models=required)


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
        if args.suite:
            suite = Path(args.suite).resolve(strict=True)
        else:
            font = find_font(args.font)
            suite = prepare_synthetic_suite(out / "_work", font)
        library = get_preset_catalogue(catalogue)
        if args.include_builtin:
            library = including_builtin_presets(library)
        expanded_path = out / "_work" / "full-catalogue.json"
        write_json(expanded_path, library)
        settings = experiment_settings(expanded_path, suite, args)
        signature = validate_experiment(out, settings)
        rows = existing_rows(out, signature)
        presets = library["presets"]
        by_name = {r["id"]: r for r in presets}
        if set(rows) - by_name.keys():
            raise ValueError("Existing output includes presets outside the selected catalogue")
        # Stable ordering makes interruptions, restarts and index reconstruction repeatable.
        queue = sorted(presets, key=lambda r: (r["engine"], r["id"]))
        visited = 0
        for item in queue:
            prior = rows.get(item["id"])
            if prior and not prior.get("interrupted") and (prior["status"] == "complete" or not args.retry_failed):
                continue
            if args.limit and visited >= args.limit:
                break
            visited += 1
            started = time.monotonic()
            print(f"[{len(rows)+1}/{len(queue)}] {item['engine']}: {item['name'][:90]}", flush=True)
            try:
                result = run_preset(args, item, signature, out, expanded_path, suite, env)
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, KeyError) as exc:
                result = _report_failed(out, item, signature, f"{type(exc).__name__}: {exc}")
            rows[item["id"]] = result
            # Each preset JSON/Markdown report is saved atomically. Avoid
            # re-writing an O(N)-sized HTML gallery 14,000+ times (O(N²) IO).
            if visited % getattr(args, "checkpoint_every", 25) == 0:
                update_index(out, list(rows.values()), total=len(queue), signature=signature)
            duration = time.monotonic() - started
            complete = sum(x["status"] == "complete" for x in rows.values())
            partial = len(rows) - complete
            print(f"  {result['status']} ({duration:.1f}s); "
                  f"{complete} complete, {partial} partial, {len(queue)-len(rows)} pending", flush=True)
        index = update_index(out, list(rows.values()), total=len(queue), signature=signature)
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
    if not (out / "index.json").is_file():
        print("No indexed analysis yet")
        return 2
    index = read_json(out / "index.json")
    print(json.dumps({"total": index["total"], "processed": index["processed"],
                      "counts": index["counts"], "gallery": str(out / "index.html")}, indent=2))
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
    parser.add_argument("--suite", type=Path, help="Optional pinned preset-suite.v1 with authored examples")
    parser.add_argument("--font", help="TTF/OTF for the synthetic demo when --suite is omitted")
    parser.add_argument("--render-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--analysis-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--intervals", type=float, nargs="+", default=[0, 0.5, 1.5])
    parser.add_argument("--images", nargs="+", choices=FULL_IMAGES, default=list(DEFAULT_IMAGES))
    parser.add_argument("--audio-modes", nargs="+", choices=DEFAULT_AUDIO, default=list(DEFAULT_AUDIO))
    parser.add_argument("--models", nargs="+", choices=DEFAULT_MODELS, default=list(DEFAULT_MODELS))
    parser.add_argument("--keyword-models", nargs="*", choices=(
        "joycaption", "smolvlm-256m", "smolvlm-500m", "smolvlm2-256m", "smolvlm2-500m"),
        default=list(DEFAULT_VLM))
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), default="cpu")
    parser.add_argument("--max-edge", type=int, default=256)
    parser.add_argument("--semantic-edge", type=int, default=384)
    parser.add_argument("--semantic-images", type=int, default=1)
    parser.add_argument("--include-builtin", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit", type=int, help="Process at most N pending presets in this invocation")
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
    if not 1 <= args.checkpoint_every <= 1000:
        parser.error("--checkpoint-every must be 1..1000")
    if not 1 <= args.semantic_images <= 8:
        parser.error("--semantic-images must be 1..8")
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
