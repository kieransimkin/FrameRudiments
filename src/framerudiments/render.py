"""Shared render runner, cleanup, evidence and representative encoded stills."""

from pathlib import Path
import subprocess
import json
import hashlib
import shutil
import traceback
import gc
from datetime import datetime, timezone
from importlib.metadata import version

from PIL import Image

from . import encoding, __version__
from .chunking import plan_chunks, should_chunk
from .compositor import Compositor
from .io import sha256, write_json
from .project import Project
from .progress import ProgressReporter
from .subtitles import export_subtitles
from .visualisers import EffectChain
from .work_cache import WorkingCache, stage_project_inputs


def _exception_details(exc):
    """Return useful structured evidence even when an exception has no message."""
    type_name = type(exc).__name__
    module = type(exc).__module__
    try:
        try:
            gc.collect()
        except BaseException:
            # Evidence collection must still work when the allocator is exhausted.
            pass
        chain = []
        seen = set()
        current = exc
        while current is not None and id(current) not in seen and len(chain) < 8:
            seen.add(id(current))
            text = str(current).strip()
            chain.append(
                dict(
                    type=type(current).__name__,
                    module=type(current).__module__,
                    message=text or type(current).__name__,
                    repr=repr(current),
                    memory_error=isinstance(current, MemoryError),
                )
            )
            current = current.__cause__ or current.__context__
        return dict(
            schema="framerudiments.exception.v1",
            type=type_name,
            module=module,
            message=str(exc).strip() or type_name,
            repr=repr(exc),
            memory_error=any(item["memory_error"] for item in chain),
            chain=chain,
            traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            evidence_complete=True,
        )
    except BaseException as evidence_error:
        return dict(
            schema="framerudiments.exception.v1",
            type=type_name,
            module=module,
            message=type_name,
            repr=type_name,
            memory_error=isinstance(exc, MemoryError),
            chain=[],
            traceback="",
            evidence_complete=False,
            evidence_error=type(evidence_error).__name__,
        )


def _candidate_artifacts(output, classification):
    output = Path(output)
    paths = sorted(output.glob("*.mp4"))
    if (output / "chunks").is_dir():
        paths.extend(sorted((output / "chunks").glob("*.partial.mp4")))
    return [
        dict(path=str(path), bytes=path.stat().st_size, classification=classification)
        for path in paths
        if path.is_file()
    ]


def _write_candidate_health(
    output,
    *,
    state,
    affected_by_error,
    phase,
    error=None,
    frame=None,
    finalized_prefix=None,
):
    """Write the machine-readable review gate for every encoded candidate."""
    value = dict(
        schema="framerudiments.candidate-health.v1",
        checked_at=datetime.now(timezone.utc).isoformat(),
        state=state,
        affected_by_error=affected_by_error,
        phase=phase,
        frame=frame,
        technical_qa="passed" if state == "technical-QA passed" else "not-passed",
        review_allowed=state == "technical-QA passed",
        publication_allowed=False,
        artifacts=_candidate_artifacts(
            output,
            "technical-QA-passed" if state == "technical-QA passed" else "unapproved-candidate",
        ),
        finalized_prefix=finalized_prefix,
        error=error,
        guidance=(
            "Candidate passed automated technical QA; attended visual/listening approval is still required."
            if state == "technical-QA passed"
            else "Do not review, approve, upload or publish listed candidate artifacts; inspect the error and resume or rerender."
        ),
    )
    _write_json_atomic(Path(output) / "candidate-health.json", value)
    return value


def _record_failure(output, failure, *, health=None):
    """Persist evidence without replacing the original render exception.

    Low-memory and storage failures can also prevent evidence writes. Missing
    candidate-health.json is therefore a closed gate, while the original
    exception remains visible to the caller and scheduler log.
    """
    output = Path(output)
    try:
        _write_json_atomic(output / "failure.json", failure)
    except BaseException:
        pass
    if health is not None:
        try:
            _write_candidate_health(output, **health)
        except BaseException:
            pass


def checkpoints(project):
    count = project.frame_count
    return sorted(
        {
            0,
            count - 1,
            count // 4,
            count // 2,
            3 * count // 4,
            *(
                max(0, min(count - 1, event["frame"] + delta))
                for event in project.lyrics + project.cues
                for delta in (-1, 0, 1)
            ),
        }
    )


def extract_encoded_checkpoints(ffmpeg, video, directory, frames, *, batch_size=32):
    """Bound FFmpeg expression complexity; preserve every exact encoded frame.

    Long lyric/cue ledgers can exceed the select parser's expression limit.
    Do not weaken QA or label that parser error as physical RAM exhaustion.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 32:
        raise ValueError("Checkpoint batch size must be between 1 and 32")
    selected = sorted(set(frames))
    if any(type(n) is not int or n < 0 for n in selected):
        raise ValueError("Checkpoint frames must be nonnegative integers")
    directory = Path(directory)
    for start in range(0, len(selected), batch_size):
        batch = selected[start : start + batch_size]
        selection = "+".join(f"eq(n\\,{n})" for n in batch)
        subprocess.run(
            [
                str(ffmpeg),
                "-n",
                "-v",
                "error",
                "-threads",
                "1",
                "-i",
                str(video),
                "-filter_threads",
                "1",
                "-vf",
                f"select={selection}",
                "-fps_mode",
                "passthrough",
                "-threads",
                "1",
                "-start_number",
                str(start),
                "-frames:v",
                str(len(batch)),
                str(directory / "checkpoint-%08d.png"),
            ],
            check=True,
            capture_output=True,
        )
        for i, frame in enumerate(batch, start):
            (directory / f"checkpoint-{i:08}.png").rename(directory / f"encoded-{frame:08}.png")


def _prepare(project, output, ffmpeg, ffprobe, scene, renderers, *, resume=False, input_cache=None):
    project = Project.load(project) if not isinstance(project, Project) else project
    ffmpeg, ffprobe = encoding.binaries(ffmpeg, ffprobe)
    project.verify_inputs()
    canonical_plan = project.summary()
    cache_evidence = stage_project_inputs(project, input_cache) if input_cache is not None else None
    source_probe = encoding.validate_audio(project, ffprobe)
    # Cached references must be installed before any compositor dependency is
    # opened, otherwise fonts and imagery can still read from final storage.
    compositor = Compositor(project, scene=scene, renderers=renderers)
    output = Path(output).resolve()
    if resume:
        if not output.is_dir() or not (output / "plan.json").is_file():
            raise ValueError("Chunked resume needs an existing prepared output directory")
        existing = json.loads((output / "plan.json").read_text(encoding="utf-8"))
        if existing != canonical_plan:
            raise ValueError("Chunked resume project differs from the prepared plan")
    if not resume:
        output.mkdir(parents=True, exist_ok=False)
        (output / "qa").mkdir()
        write_json(output / "plan.json", canonical_plan)
        write_json(output / "source-probe.json", source_probe)
        export_subtitles(project.lyrics, project.fps, project.frame_count, output, project.width, project.height)
        if project.words:
            from .word_timing import word_export_ledger

            export_subtitles(
                word_export_ledger(project.words, project.lyrics),
                project.fps,
                project.frame_count,
                output,
                project.width,
                project.height,
                basename="word-captions",
            )
    if cache_evidence is not None:
        write_json(output / "input-cache.json", cache_evidence)
    return project, output, ffmpeg, ffprobe, compositor


def preflight(
    project,
    output,
    *,
    ffmpeg=None,
    ffprobe=None,
    scene=None,
    renderers=None,
    max_frames=None,
    progress_interval_seconds=10.0,
):
    p = Project.load(project) if not isinstance(project, Project) else project
    if max_frames is None:
        frame_count = p.frame_count
    elif type(max_frames) is not int or not 1 <= max_frames <= p.frame_count:
        raise ValueError(f"Preflight max_frames must be an integer in [1,{p.frame_count}]")
    else:
        frame_count = max_frames
    p, out, ffmpeg, ffprobe, compositor = _prepare(p, output, ffmpeg, ffprobe, scene, renderers)
    selected = {frame for frame in checkpoints(p) if frame < frame_count}
    selected.add(frame_count - 1)
    scope = dict(
        mode="full" if frame_count == p.frame_count else "bounded-prefix",
        frames_rendered=frame_count,
        total_frames=p.frame_count,
        complete=frame_count == p.frame_count,
        sequential=True,
        note=(
            "All project frames were composed."
            if frame_count == p.frame_count
            else "Only the declared sequential prefix was composed; later frames remain unvalidated."
        ),
    )
    effects = None
    progress = None
    active_frame = None
    try:
        progress = ProgressReporter(
            out,
            frame_count,
            mode="preflight",
            interval_seconds=progress_interval_seconds,
            project_total_frames=p.frame_count,
        ).start("composing preflight frames")
        effects = (
            EffectChain(
                p,
                compositor.effect_specs,
                ffmpeg,
                out,
                routing=compositor.visualiser_routing,
                hooks=compositor.visualiser_hooks,
                context_at=compositor.context,
            )
            if compositor.effect_specs
            else None
        )
        with (out / "rudiment-analysis.jsonl").open("w", encoding="utf-8") as analysis:
            for n in range(frame_count):
                active_frame = n
                progress.active(n)
                image, context = compositor.frame(n, apply_quality=False)
                if effects:
                    if n in selected:
                        image.save(out / "qa" / f"composed-{n:08}.png")
                    image = effects.process(
                        image,
                        context,
                        isolated_layers=compositor._route_parts
                        if compositor.visualiser_routing is not None or compositor._isolate_stage_injections
                        else None,
                    )
                # Exactly one final AA pass, after optional GPU effects.
                image = compositor.output_quality.apply(image)
                _analysis(analysis, compositor, context)
                if n in selected:
                    image.save(out / "qa" / f"source-{n:08}.png")
                progress.advance(n + 1)
        if effects:
            effects.close()
        compositor.close()
        p.verify_inputs()
        result = dict(
            p.summary(),
            preflight_scope=scope,
            composition=compositor.evidence(),
            full_frame_effects=effects.evidence() if effects else None,
            analysis_sidecar=_sidecar(out),
            state=(
                "full preflight passed; no encoded video"
                if scope["complete"]
                else "bounded-prefix preflight passed; later frames unvalidated; no encoded video"
            ),
        )
        progress.phase("writing preflight manifest", completed_frames=frame_count)
        write_json(out / "manifest.json", result)
        progress.finish()
        return result
    except BaseException as exc:
        details = _exception_details(exc)
        _record_failure(
            out,
            dict(state="preflight failed", error=details["message"], exception=details),
        )
        if progress:
            try:
                progress.fail(details["message"], phase="preflight failed", frame=active_frame)
            except BaseException:
                pass
        raise
    finally:
        if progress:
            progress.close()
        if effects:
            effects.close()
        compositor.close()


def render(
    project,
    output,
    *,
    ffmpeg=None,
    ffprobe=None,
    scene=None,
    renderers=None,
    preset="fast",
    crf=20,
    chunked=None,
    chunk_seconds=30.0,
    resume=False,
    min_free_bytes=536870912,
    progress_interval_seconds=10.0,
    work_cache=None,
    reserve_bytes=None,
):
    """Render a project, chunking videos longer than 30 seconds by default."""
    cache = None
    cache_active = False
    active_output = Path(output).resolve()
    try:
        prepared = Project.load(project) if not isinstance(project, Project) else project
        use_chunks = should_chunk(prepared) if chunked is None else chunked
        if type(use_chunks) is not bool:
            raise ValueError("chunked must be true, false or null")
        if resume and not use_chunks:
            raise ValueError("Resume is available only for chunked renders")
        if work_cache is None and reserve_bytes is not None:
            raise ValueError("reserve_bytes requires work_cache")
        if work_cache is not None:
            if reserve_bytes is None:
                raise ValueError("work_cache requires a complete peak reserve_bytes estimate")
            cache = WorkingCache(
                work_cache,
                output,
                reserve_bytes=reserve_bytes,
                min_free_bytes=min_free_bytes,
                status_interval=progress_interval_seconds,
            )
            active_output = cache.prepare(resume=resume)
            cache_active = True
            cache.begin_render()
        if use_chunks:
            result = _render_chunked(
                prepared,
                active_output,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                scene=scene,
                renderers=renderers,
                preset=preset,
                crf=crf,
                chunk_seconds=chunk_seconds,
                resume=resume,
                min_free_bytes=min_free_bytes,
                progress_interval_seconds=progress_interval_seconds,
                input_cache=cache.inputs if cache else None,
            )
        else:
            result = _render_single(
                prepared,
                active_output,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                scene=scene,
                renderers=renderers,
                preset=preset,
                crf=crf,
                progress_interval_seconds=progress_interval_seconds,
                input_cache=cache.inputs if cache else None,
            )
        if cache:
            result = cache.promote(result)
            cache.finish()
        return result
    except BaseException as exc:
        # _prepare failures happen before the mode-specific handlers. Fill that
        # gap, but never replace their more precise evidence.
        out = active_output
        if out.is_dir() and not (out / "failure.json").is_file():
            details = _exception_details(exc)
            _record_failure(
                out,
                dict(
                    state="render setup failed; any encoded file remains unapproved",
                    error=details["message"],
                    exception=details,
                    phase="preparing render",
                    frame=None,
                ),
                health=dict(
                    state="affected-by-error",
                    affected_by_error=True,
                    phase="preparing render",
                    error=details,
                    frame=None,
                ),
            )
        if cache_active:
            cache.fail(exc)
        raise


def _render_single(
    project,
    output,
    *,
    ffmpeg=None,
    ffprobe=None,
    scene=None,
    renderers=None,
    preset="fast",
    crf=20,
    progress_interval_seconds=10.0,
    input_cache=None,
):
    p, out, ffmpeg, ffprobe, compositor = _prepare(
        project, output, ffmpeg, ffprobe, scene, renderers, input_cache=input_cache
    )
    candidate, final = out / "candidate.mp4", out / "video.mp4"
    cmd = encoding.command(p, candidate, ffmpeg, preset=preset, crf=crf)
    write_json(out / "encode-command.json", cmd)
    selected = set(checkpoints(p))
    process = None
    effects = None
    phase = "rendering frames"
    active_frame = None
    progress = None
    _write_candidate_health(
        out,
        state="rendering",
        affected_by_error=False,
        phase=phase,
    )
    try:
        progress = ProgressReporter(
            out,
            p.frame_count,
            mode="single-pass",
            interval_seconds=progress_interval_seconds,
        ).start(phase)
        effects = (
            EffectChain(
                p,
                compositor.effect_specs,
                ffmpeg,
                out,
                routing=compositor.visualiser_routing,
                hooks=compositor.visualiser_hooks,
                context_at=compositor.context,
            )
            if compositor.effect_specs
            else None
        )
        with (
            (out / "encode.log").open("wb") as log,
            (out / "rudiment-analysis.jsonl").open("w", encoding="utf-8") as analysis,
        ):
            process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            for n in range(p.frame_count):
                active_frame = n
                progress.active(n)
                image, context = compositor.frame(n, apply_quality=False)
                if effects:
                    if n in selected:
                        image.save(out / "qa" / f"composed-{n:08}.png")
                    image = effects.process(
                        image,
                        context,
                        isolated_layers=compositor._route_parts
                        if compositor.visualiser_routing is not None or compositor._isolate_stage_injections
                        else None,
                    )
                # Exactly one final AA pass, after optional GPU effects.
                image = compositor.output_quality.apply(image)
                _analysis(analysis, compositor, context)
                if n in selected:
                    image.save(out / "qa" / f"source-{n:08}.png")
                process.stdin.write(image.tobytes())
                progress.advance(n + 1)
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError(f"FFmpeg failed; see {out / 'encode.log'}")
        if effects:
            effects.close()
        compositor.close()
        p.verify_inputs()
        phase = "verifying encoded candidate"
        progress.phase(phase, completed_frames=p.frame_count)
        verified = encoding.verify(candidate, p, ffmpeg, ffprobe)
        source_audio = encoding.probe(p.audio_ref["path"], ffprobe)
        source_channels = next(s["channels"] for s in source_audio["streams"] if s["codec_type"] == "audio")
        output_channels = next(
            s["channels"] for s in verified["probe"]["streams"] if s["codec_type"] == "audio"
        )
        if source_channels != output_channels:
            raise ValueError("Encoder changed channel count")
        phase = "promoting verified candidate"
        progress.phase(phase)
        candidate.rename(final)
        phase = "extracting encoded checkpoints"
        progress.phase(phase)
        extract_encoded_checkpoints(ffmpeg, final, out / "qa", selected)
        with Image.open(out / "qa" / "encoded-00000000.png") as first:
            first.save(out / "poster.png")
            thumbnail = first.copy()
            thumbnail.thumbnail((360, 640))
            thumbnail.save(out / "phone-preview.png")
        result = dict(
            p.summary(),
            render_mode="single-pass",
            input_cache=getattr(p, "input_cache_evidence", None),
            composition=compositor.evidence(),
            full_frame_effects=effects.evidence() if effects else None,
            analysis_sidecar=_sidecar(out),
            verification=verified,
            software={
                "framerudiments": __version__,
                "Pillow": version("Pillow"),
                "dancerudiments": version("dancerudiments"),
                "ffmpeg": subprocess.check_output([ffmpeg, "-version"], text=True).splitlines()[0],
            },
            output={"path": str(final), "sha256": sha256(final), "bytes": final.stat().st_size},
            state="technical-QA passed; creative and attended listening review pending",
        )
        phase = "writing final manifest"
        progress.phase(phase)
        write_json(out / "manifest.json", result)
        _write_candidate_health(
            out,
            state="technical-QA passed",
            affected_by_error=False,
            phase="complete",
            frame=p.frame_count - 1,
        )
        progress.finish(finalized_frames=p.frame_count)
        return result
    except BaseException as exc:
        if process and process.poll() is None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        details = _exception_details(exc)
        failure = dict(
            state="failed; any encoded file remains unapproved",
            error=details["message"],
            exception=details,
            phase=phase,
            frame=active_frame,
        )
        _record_failure(
            out,
            failure,
            health=dict(
                state="affected-by-error",
                affected_by_error=True,
                phase=phase,
                error=details,
                frame=active_frame,
            ),
        )
        if progress:
            try:
                progress.fail(details["message"], frame=active_frame)
            except BaseException:
                pass
        raise
    finally:
        if progress:
            progress.close()
        if effects:
            effects.close()
        compositor.close()


def _write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _chunk_paths(directory, index):
    stem = f"chunk-{index:04d}"
    return (
        directory / f"{stem}.mp4",
        directory / f"{stem}.partial.mp4",
        directory / f"{stem}.json",
        directory / f"{stem}.log",
    )


def _completed_prefix(output, plan):
    directory = output / "chunks"
    completed = []
    missing_seen = False
    for chunk in plan["chunks"]:
        final, partial, receipt_path, _ = _chunk_paths(directory, chunk["index"])
        exists = final.is_file() or receipt_path.is_file()
        if missing_seen and exists:
            raise ValueError("Chunk receipts must form one contiguous completed prefix")
        if not final.is_file() or not receipt_path.is_file():
            missing_seen = True
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("schema") != "framerudiments.chunk-receipt.v1"
            or receipt.get("index") != chunk["index"]
            or receipt.get("start_frame") != chunk["start_frame"]
            or receipt.get("end_frame_exclusive") != chunk["end_frame_exclusive"]
            or receipt.get("frames") != chunk["frames"]
            or receipt.get("sha256") != sha256(final)
            or receipt.get("bytes") != final.stat().st_size
            or receipt.get("verification", {}).get("full_decode") != "passed"
        ):
            raise ValueError(f"Completed chunk {chunk['index']} failed its retained receipt")
        if partial.exists():
            raise ValueError(f"Completed chunk {chunk['index']} also has a stale partial file")
        completed.append(chunk)
    return completed


def _archive_partial(output, chunk):
    final, partial, receipt, log = _chunk_paths(output / "chunks", chunk["index"])
    existing = [
        path
        for path in (
            final,
            partial,
            receipt,
            log,
            output / "analysis" / f"chunk-{chunk['index']:04d}.jsonl",
        )
        if path.exists()
    ]
    if not existing:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = output / "failed-chunks" / f"chunk-{chunk['index']:04d}-{stamp}"
    archive.mkdir(parents=True)
    for path in existing:
        path.replace(archive / path.name)


def _concat_file(path, chunks):
    rows = []
    for chunk in chunks:
        video, _, _, _ = _chunk_paths(path.parent / "chunks", chunk["index"])
        escaped = video.resolve().as_posix().replace("'", "'\\''")
        rows.append(f"file '{escaped}'")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _decoded_frame_hash(ffmpeg, video, frame):
    result = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-threads",
            "1",
            "-i",
            str(video),
            "-vf",
            f"select=eq(n\\,{frame})",
            "-fps_mode",
            "passthrough",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    if not result.stdout:
        raise ValueError(f"Could not decode requested seam frame {frame}")
    return hashlib.sha256(result.stdout).hexdigest()


def _verify_seams(ffmpeg, final, output, plan):
    results = []
    for previous, following in zip(plan["chunks"], plan["chunks"][1:]):
        boundary = following["start_frame"]
        previous_video, _, _, _ = _chunk_paths(output / "chunks", previous["index"])
        following_video, _, _, _ = _chunk_paths(output / "chunks", following["index"])
        pairs = (
            (boundary - 1, previous_video, previous["frames"] - 1, "before"),
            (boundary, following_video, 0, "after"),
        )
        for final_frame, chunk_video, chunk_frame, side in pairs:
            final_hash = _decoded_frame_hash(ffmpeg, final, final_frame)
            chunk_hash = _decoded_frame_hash(ffmpeg, chunk_video, chunk_frame)
            if final_hash != chunk_hash:
                raise ValueError(f"Stream-copy seam changed decoded pixels at frame {final_frame}")
            results.append(
                dict(
                    boundary_frame=boundary,
                    side=side,
                    final_frame=final_frame,
                    chunk_index=previous["index"] if side == "before" else following["index"],
                    decoded_rgb_sha256=final_hash,
                    matched=True,
                )
            )
    return dict(
        schema="framerudiments.chunk-seams.v1",
        boundaries=len(plan["chunks"]) - 1,
        decoded_frames_checked=len(results),
        all_matched=True,
        checks=results,
    )


def _render_chunked(
    project,
    output,
    *,
    ffmpeg=None,
    ffprobe=None,
    scene=None,
    renderers=None,
    preset="fast",
    crf=20,
    chunk_seconds=30.0,
    resume=False,
    min_free_bytes=536870912,
    progress_interval_seconds=10.0,
    input_cache=None,
):
    if type(min_free_bytes) is not int or min_free_bytes < 0:
        raise ValueError("min_free_bytes must be a nonnegative integer")
    p, out, ffmpeg, ffprobe, compositor = _prepare(
        project,
        output,
        ffmpeg,
        ffprobe,
        scene,
        renderers,
        resume=resume,
        input_cache=input_cache,
    )
    plan = plan_chunks(p, target_seconds=chunk_seconds)
    plan_path = out / "chunk-plan.json"
    if resume:
        if not plan_path.is_file() or json.loads(plan_path.read_text(encoding="utf-8")) != plan:
            raise ValueError("Chunked resume settings differ from the retained plan")
    else:
        (out / "chunks").mkdir()
        (out / "analysis").mkdir()
        _write_json_atomic(plan_path, plan)
    completed = _completed_prefix(out, plan)
    if len(completed) == len(plan["chunks"]) and (out / "video.mp4").is_file() and (out / "manifest.json").is_file():
        result = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        if result.get("output", {}).get("sha256") != sha256(out / "video.mp4"):
            raise ValueError("Completed chunked render hash differs from its manifest")
        _write_candidate_health(
            out,
            state="technical-QA passed",
            affected_by_error=False,
            phase="complete",
            frame=p.frame_count - 1,
            finalized_prefix=dict(
                completed_chunks=len(completed),
                total_chunks=len(plan["chunks"]),
                completed_frames=p.frame_count,
                total_frames=p.frame_count,
            ),
        )
        progress = ProgressReporter(
            out,
            p.frame_count,
            mode="chunked-sequential",
            interval_seconds=progress_interval_seconds,
        ).start(
            "checking completed render",
            completed_frames=p.frame_count,
            finalized_frames=p.frame_count,
        )
        progress.finish(finalized_frames=p.frame_count)
        return result
    first_missing = len(completed)
    if resume and (out / "failure.json").is_file():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (out / "failure.json").replace(out / f"failure-before-resume-{stamp}.json")
    if resume and (out / "candidate-health.json").is_file():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        (out / "candidate-health.json").replace(out / f"candidate-health-before-resume-{stamp}.json")
    if first_missing < len(plan["chunks"]):
        _archive_partial(out, plan["chunks"][first_missing])
    selected = set(checkpoints(p))
    effects = None
    process = None
    log = None
    analysis = None
    active_chunk = None
    active_frame = None
    phase = "replaying finalized prefix and rendering chunks"
    replayed = completed[-1]["end_frame_exclusive"] if completed else 0
    prefix = dict(
        completed_chunks=len(completed),
        total_chunks=len(plan["chunks"]),
        completed_frames=replayed,
        total_frames=p.frame_count,
    )
    progress = None
    _write_candidate_health(
        out,
        state="rendering",
        affected_by_error=False,
        phase=phase,
        finalized_prefix=prefix,
    )
    try:
        progress = ProgressReporter(
            out,
            p.frame_count,
            mode="chunked-sequential",
            interval_seconds=progress_interval_seconds,
        ).start(
            "replaying finalized prefix" if replayed else "rendering chunks",
            finalized_frames=replayed,
        )
        effects = (
            EffectChain(
                p,
                compositor.effect_specs,
                ffmpeg,
                out,
                routing=compositor.visualiser_routing,
                hooks=compositor.visualiser_hooks,
            )
            if compositor.effect_specs
            else None
        )
        for n in range(p.frame_count):
            active_frame = n
            progress.active(n)
            if replayed and n == replayed:
                progress.phase("rendering chunks", completed_frames=n, finalized_frames=replayed)
            chunk = next(
                item
                for item in plan["chunks"]
                if item["start_frame"] <= n < item["end_frame_exclusive"]
            )
            if n == chunk["start_frame"] and chunk["index"] >= first_missing:
                if shutil.disk_usage(out).free < min_free_bytes:
                    raise RuntimeError(
                        f"Free space guard stopped before chunk {chunk['index']}; "
                        f"need at least {min_free_bytes} bytes free"
                    )
                active_chunk = chunk
                final_chunk, partial, _, log_path = _chunk_paths(out / "chunks", chunk["index"])
                if final_chunk.exists() or partial.exists():
                    raise ValueError(f"Chunk {chunk['index']} output already exists without a valid receipt")
                cmd = encoding.video_chunk_command(p, partial, ffmpeg, preset=preset, crf=crf)
                _write_json_atomic(out / "chunks" / f"chunk-{chunk['index']:04d}-command.json", cmd)
                log = log_path.open("wb")
                analysis = (out / "analysis" / f"chunk-{chunk['index']:04d}.jsonl").open(
                    "w", encoding="utf-8"
                )
                process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            image, context = compositor.frame(n, apply_quality=False)
            if effects:
                if n in selected and not (out / "qa" / f"composed-{n:08}.png").exists():
                    image.save(out / "qa" / f"composed-{n:08}.png")
                image = effects.process(
                    image,
                    context,
                    isolated_layers=compositor._route_parts
                    if compositor.visualiser_routing is not None or compositor._isolate_stage_injections
                    else None,
                )
            image = compositor.output_quality.apply(image)
            if chunk["index"] >= first_missing:
                _analysis(analysis, compositor, context)
                if n in selected and not (out / "qa" / f"source-{n:08}.png").exists():
                    image.save(out / "qa" / f"source-{n:08}.png")
                process.stdin.write(image.tobytes())
            if n + 1 == chunk["end_frame_exclusive"] and chunk["index"] >= first_missing:
                process.stdin.close()
                if process.wait() != 0:
                    raise RuntimeError(f"FFmpeg failed; see {log_path}")
                process = None
                log.close()
                log = None
                analysis.close()
                analysis = None
                verification = encoding.verify_video(partial, p, chunk["frames"], ffmpeg, ffprobe)
                final_chunk, _, receipt_path, _ = _chunk_paths(out / "chunks", chunk["index"])
                partial.replace(final_chunk)
                receipt = dict(
                    schema="framerudiments.chunk-receipt.v1",
                    index=chunk["index"],
                    start_frame=chunk["start_frame"],
                    end_frame_exclusive=chunk["end_frame_exclusive"],
                    frames=chunk["frames"],
                    sha256=sha256(final_chunk),
                    bytes=final_chunk.stat().st_size,
                    verification=verification,
                    state="finalized and fully decoded; retained for stream-copy assembly",
                )
                _write_json_atomic(receipt_path, receipt)
                completed.append(chunk)
                _write_json_atomic(
                    out / "chunk-progress.json",
                    dict(
                        schema="framerudiments.chunk-progress.v1",
                        completed_chunks=len(completed),
                        total_chunks=len(plan["chunks"]),
                        completed_frames=chunk["end_frame_exclusive"],
                        total_frames=p.frame_count,
                        replayed_frames=replayed,
                    ),
                )
                progress.phase(
                    "rendering chunks",
                    completed_frames=n + 1,
                    finalized_frames=chunk["end_frame_exclusive"],
                )
                active_chunk = None
            progress.advance(n + 1)
        if effects:
            effects.close()
        compositor.close()
        p.verify_inputs()
        chunks = _completed_prefix(out, plan)
        if len(chunks) != len(plan["chunks"]):
            raise RuntimeError("Chunk render ended without a complete contiguous receipt set")
        concat = out / "chunks.txt"
        _concat_file(concat, chunks)
        required = sum(_chunk_paths(out / "chunks", item["index"])[0].stat().st_size for item in chunks)
        if shutil.disk_usage(out).free < required + min_free_bytes:
            raise RuntimeError(
                "Final assembly free-space guard stopped before mux; completed chunks are resumable"
            )
        phase = "assembling encoded candidate"
        progress.phase(phase, completed_frames=p.frame_count, finalized_frames=p.frame_count)
        candidate, final = out / "candidate.mp4", out / "video.mp4"
        if candidate.exists():
            candidate.replace(out / f"candidate-failed-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.mp4")
        mux = encoding.mux_chunks_command(p, concat, candidate, ffmpeg)
        _write_json_atomic(out / "assemble-command.json", mux)
        with (out / "assemble.log").open("wb") as log:
            subprocess.run(mux, check=True, stdout=subprocess.DEVNULL, stderr=log)
        phase = "verifying encoded candidate"
        progress.phase(phase)
        verified = encoding.verify(candidate, p, ffmpeg, ffprobe)
        source_audio = encoding.probe(p.audio_ref["path"], ffprobe)
        source_channels = next(s["channels"] for s in source_audio["streams"] if s["codec_type"] == "audio")
        output_channels = next(s["channels"] for s in verified["probe"]["streams"] if s["codec_type"] == "audio")
        if source_channels != output_channels:
            raise ValueError("Encoder changed channel count")
        phase = "promoting verified candidate"
        progress.phase(phase)
        candidate.replace(final)
        phase = "verifying decoded seams"
        progress.phase(phase)
        seams = _verify_seams(ffmpeg, final, out, plan)
        _write_json_atomic(out / "seam-verification.json", seams)
        phase = "extracting encoded checkpoints"
        progress.phase(phase)
        extract_encoded_checkpoints(ffmpeg, final, out / "qa", selected)
        with Image.open(out / "qa" / "encoded-00000000.png") as first:
            first.save(out / "poster.png")
            thumbnail = first.copy()
            thumbnail.thumbnail((360, 640))
            thumbnail.save(out / "phone-preview.png")
        analysis_sidecars = []
        for chunk in chunks:
            path = out / "analysis" / f"chunk-{chunk['index']:04d}.jsonl"
            analysis_sidecars.append(dict(index=chunk["index"], path=str(path), sha256=sha256(path), bytes=path.stat().st_size))
        result = dict(
            p.summary(),
            render_mode="chunked-sequential",
            input_cache=getattr(p, "input_cache_evidence", None),
            chunk_plan=plan,
            chunk_receipts=[
                json.loads(_chunk_paths(out / "chunks", item["index"])[2].read_text(encoding="utf-8"))
                for item in chunks
            ],
            replayed_frames=replayed,
            composition=compositor.evidence(),
            full_frame_effects=effects.evidence() if effects else None,
            analysis_sidecars=analysis_sidecars,
            seam_verification=seams,
            verification=verified,
            software={
                "framerudiments": __version__,
                "Pillow": version("Pillow"),
                "dancerudiments": version("dancerudiments"),
                "ffmpeg": subprocess.check_output([ffmpeg, "-version"], text=True).splitlines()[0],
            },
            output={"path": str(final), "sha256": sha256(final), "bytes": final.stat().st_size},
            state="technical-QA passed; creative and attended listening review pending",
        )
        phase = "writing final manifest"
        progress.phase(phase)
        _write_json_atomic(out / "manifest.json", result)
        _write_candidate_health(
            out,
            state="technical-QA passed",
            affected_by_error=False,
            phase="complete",
            frame=p.frame_count - 1,
            finalized_prefix=dict(
                completed_chunks=len(chunks),
                total_chunks=len(plan["chunks"]),
                completed_frames=p.frame_count,
                total_frames=p.frame_count,
            ),
        )
        progress.finish(finalized_frames=p.frame_count)
        return result
    except BaseException as exc:
        if process and process.poll() is None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if analysis and not analysis.closed:
            analysis.close()
        if log and not log.closed:
            log.close()
        details = _exception_details(exc)
        prefix = dict(
            completed_chunks=len(completed),
            total_chunks=len(plan["chunks"]),
            completed_frames=completed[-1]["end_frame_exclusive"] if completed else 0,
            total_frames=p.frame_count,
        )
        _record_failure(
            out,
            dict(
                state="chunked render interrupted; finalized prefix remains resumable",
                error=details["message"],
                exception=details,
                phase=phase,
                frame=active_frame,
                active_chunk=active_chunk,
                completed_chunks=len(completed),
                total_chunks=len(plan["chunks"]),
                resume=True,
            ),
            health=dict(
                state="affected-by-error",
                affected_by_error=True,
                phase=phase,
                error=details,
                frame=active_frame,
                finalized_prefix=prefix,
            ),
        )
        if progress:
            try:
                progress.fail(details["message"], frame=active_frame)
            except BaseException:
                pass
        raise
    finally:
        if progress:
            progress.close()
        if effects:
            effects.close()
        compositor.close()


def _analysis(handle, compositor, context):
    reports = compositor.frame_reports()
    post = compositor.post_effect_reports()
    routed = compositor.visualiser_routing.last_report if compositor.visualiser_routing is not None else None
    if reports or post or routed:
        entry = dict(
            schema="framerudiments.rudiment-analysis-frame.v1",
            frame=context.frame,
            source_seconds=context.source_seconds,
            output_seconds=context.output_seconds,
            layers=reports,
        )
        if post:
            entry["post_effects"] = post
        if routed:
            entry["visualiser_routing"] = routed
        handle.write(json.dumps(entry, allow_nan=False) + "\n")


def _sidecar(output):
    path = output / "rudiment-analysis.jsonl"
    return dict(path=str(path), sha256=sha256(path), bytes=path.stat().st_size)
