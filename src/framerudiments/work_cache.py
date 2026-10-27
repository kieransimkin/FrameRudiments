"""Fast-drive render workspaces, physical reservations and verified promotion."""

from __future__ import annotations

from datetime import datetime, timezone
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time

from .io import sha256, write_json


RESERVATION_DIRECTORY = ".framerudiments-reservations"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _job_id(final_output):
    value = os.path.normcase(str(Path(final_output).resolve()))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _allocated_file(path, size, progress=None):
    """Create a non-sparse reservation by physically writing every byte."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    block = b"\0" * min(8 * 1024 * 1024, max(1, size))
    try:
        with path.open("xb") as stream:
            remaining = size
            while remaining:
                written = min(remaining, len(block))
                stream.write(block[:written])
                remaining -= written
                if progress is not None:
                    progress(size - remaining)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if path.stat().st_size != size:
        path.unlink(missing_ok=True)
        raise OSError(f"Reservation did not allocate the requested bytes: {path}")


def _release(path):
    if path is not None:
        Path(path).unlink(missing_ok=True)


def _remove_empty(directory):
    directory = Path(directory)
    try:
        directory.rmdir()
    except (FileNotFoundError, OSError):
        pass


def _remove_tree(directory):
    directory = Path(directory)
    for attempt in range(10):
        try:
            shutil.rmtree(directory)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 9:
                raise
            gc.collect()
            time.sleep(0.05 * (attempt + 1))


def _tree_inventory(root, *, exclude=()):
    root = Path(root)
    excluded = set(exclude)
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        rows.append(dict(path=relative, bytes=path.stat().st_size, sha256=sha256(path)))
    return rows


def _tree_bytes(root):
    return sum(path.stat().st_size for path in Path(root).rglob("*") if path.is_file())


def stage_project_inputs(project, directory):
    """Copy every checksum-pinned input and switch live references to the cache."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if project.canonical_inputs is not None:
        raise RuntimeError("Project inputs have already been staged")
    canonical = [dict(item) for item in project.inputs]
    project.canonical_inputs = canonical
    entries = []
    seen = set()
    for live, source in zip(project.inputs, canonical, strict=True):
        suffix = Path(source["path"]).suffix.lower()
        target = directory / f"{source['sha256']}{suffix}"
        if not target.exists():
            temporary = target.with_name(target.name + ".partial")
            temporary.unlink(missing_ok=True)
            shutil.copy2(source["path"], temporary)
            if sha256(temporary) != source["sha256"]:
                temporary.unlink(missing_ok=True)
                raise ValueError(f"Cached input hash mismatch: {source['path']}")
            temporary.replace(target)
        if target.stat().st_size != source["bytes"] or sha256(target) != source["sha256"]:
            raise ValueError(f"Existing cached input differs from its source: {target}")
        live["path"] = str(target)
        identity = (source["path"], source["sha256"])
        if identity not in seen:
            entries.append(
                dict(
                    source_path=source["path"],
                    cached_path=str(target),
                    sha256=source["sha256"],
                    bytes=source["bytes"],
                )
            )
            seen.add(identity)
    evidence = dict(
        schema="framerudiments.input-cache.v1",
        prepared_at=_now(),
        directory=str(directory),
        files=len(entries),
        bytes=sum(item["bytes"] for item in entries),
        entries=entries,
        state="all checksum-pinned project inputs copied and hash-verified before frame rendering",
    )
    project.input_cache_evidence = evidence
    project.verify_inputs()
    return evidence


class _Lease:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None

    def acquire(self):
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(f"Working cache job is already active: {self.path.parent}") from exc

    def close(self):
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class WorkingCache:
    """Own one resumable cache job and its two-location reservations."""

    def __init__(self, cache_root, final_output, *, reserve_bytes, min_free_bytes, status_interval=10.0):
        if type(reserve_bytes) is not int or reserve_bytes < 1:
            raise ValueError("work-cache reserve_bytes must be a positive integer")
        if type(min_free_bytes) is not int or min_free_bytes < 0:
            raise ValueError("min_free_bytes must be a nonnegative integer")
        if type(status_interval) not in (int, float) or not 0.1 <= status_interval <= 3600:
            raise ValueError("work-cache status interval must be between 0.1 and 3600 seconds")
        self.cache_root = Path(cache_root).resolve()
        self.final_output = Path(final_output).resolve()
        self.reserve_bytes = reserve_bytes
        self.margin_bytes = max(1, math.ceil(reserve_bytes * 0.02))
        self.min_free_bytes = min_free_bytes
        self.status_interval = float(status_interval)
        self.job_id = _job_id(self.final_output)
        name = "".join(c if c.isalnum() or c in "-_" else "-" for c in self.final_output.name)[:48]
        self.job = self.cache_root / f"{name or 'render'}-{self.job_id}"
        if self.final_output == self.cache_root or self.final_output.is_relative_to(self.cache_root):
            raise ValueError("Final output must be outside the working-cache root")
        self.output = self.job / "render"
        self.inputs = self.job / "inputs"
        self.status_path = self.job / "work-cache.json"
        self.lease = _Lease(self.job / "cache.lock")
        cache_reservations = self.cache_root / RESERVATION_DIRECTORY
        final_reservations = self.final_output.parent / RESERVATION_DIRECTORY
        self.reservations = {
            "cache_payload": cache_reservations / f"{self.job_id}-cache-payload.reserve",
            "cache_margin": cache_reservations / f"{self.job_id}-cache-margin.reserve",
            "final_payload": final_reservations / f"{self.job_id}-final-payload.reserve",
            "final_margin": final_reservations / f"{self.job_id}-final-margin.reserve",
        }
        self._status = {}
        self._last_status = 0.0

    def prepare(self, *, resume=False):
        if self.final_output.exists():
            raise FileExistsError(f"Final output already exists: {self.final_output}")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.final_output.parent.mkdir(parents=True, exist_ok=True)
        if resume:
            if not self.job.is_dir() or not self.output.is_dir():
                raise ValueError(f"No resumable working-cache job for {self.final_output}")
            previous = json.loads(self.status_path.read_text(encoding="utf-8"))
            if previous.get("final_output") != str(self.final_output):
                raise ValueError("Working-cache metadata belongs to another final output")
        else:
            if self.job.exists():
                raise FileExistsError(f"Working cache already contains this job; inspect or use --resume: {self.job}")
            self.job.mkdir()
        self.lease.acquire()
        self._status = dict(
            schema="framerudiments.work-cache.v1",
            job_id=self.job_id,
            state="preparing",
            phase="reserving cache and final capacity",
            cache_root=str(self.cache_root),
            job_directory=str(self.job),
            cached_output=str(self.output),
            final_output=str(self.final_output),
            reserve_bytes=self.reserve_bytes,
            margin_bytes=self.margin_bytes,
            min_free_bytes=self.min_free_bytes,
            resumed=bool(resume),
            updated_at=_now(),
        )
        self._write_status(force=True)
        try:
            self._reserve_all()
        except BaseException:
            self.release_reservations()
            self._status.update(state="failed", phase="space reservation failed")
            self._write_status(force=True)
            self.lease.close()
            raise
        return self.output

    def _reserve_all(self):
        for key in ("cache_payload", "cache_margin", "final_payload", "final_margin"):
            path = self.reservations[key]
            size = self.reserve_bytes if key.endswith("payload") else self.margin_bytes
            if path.exists():
                raise FileExistsError(f"Stale or active reservation exists: {path}")
            required = size + self.min_free_bytes
            free = shutil.disk_usage(path.parent.parent).free
            if free < required:
                raise OSError(f"Insufficient space for {key}: need {required} free bytes, found {free}")
            self._status["phase"] = f"allocating {key.replace('_', ' ')}"
            self._status["reservation_completed_bytes"] = 0
            self._status["reservation_total_bytes"] = size
            self._write_status(force=True)
            _allocated_file(
                path,
                size,
                progress=lambda completed: self._reservation_progress(completed),
            )
        self._status["reservations"] = {
            key: dict(path=str(path), bytes=path.stat().st_size, physically_written=True)
            for key, path in self.reservations.items()
        }
        self._write_status(force=True)

    def _reservation_progress(self, completed):
        self._status["reservation_completed_bytes"] = completed
        self._write_status()

    def begin_render(self):
        _release(self.reservations["cache_payload"])
        self._status.update(
            state="rendering",
            phase="cached inputs and render output replace the cache payload reservation",
            progress_file=str(self.output / "render-progress.json"),
        )
        self._write_status(force=True)

    def update(self, *, state=None, phase=None, **values):
        if state is not None:
            self._status["state"] = state
        if phase is not None:
            self._status["phase"] = phase
        self._status.update(values)
        self._write_status(force=True)

    def _write_status(self, *, force=False):
        now = time.monotonic()
        if not force and now - self._last_status < self.status_interval:
            return
        self._status["updated_at"] = _now()
        write_json(self.status_path, self._status)
        print(
            f"FrameRudiments cache: state={self._status['state']} phase={self._status['phase']} "
            f"job={self.job}",
            file=__import__("sys").stderr,
            flush=True,
        )
        self._last_status = now

    def promote(self, result):
        manifest = self._rewrite_output_records(result)
        cache_bytes = _tree_bytes(self.job)
        output_bytes = _tree_bytes(self.output)
        if cache_bytes > self.reserve_bytes:
            raise OSError(
                f"Working cache used {cache_bytes} bytes, above its {self.reserve_bytes}-byte reservation"
            )
        if output_bytes > self.reserve_bytes:
            raise OSError(
                f"Final output needs {output_bytes} bytes, above its {self.reserve_bytes}-byte reservation"
            )
        source_inventory = _tree_inventory(self.output)
        total_copy_bytes = sum(item["bytes"] for item in source_inventory)
        _release(self.reservations["final_payload"])
        staging = self.final_output.parent / f".{self.final_output.name}.framerudiments-{self.job_id}.staging"
        if staging.exists():
            raise FileExistsError(f"Final staging directory already exists: {staging}")
        self.update(
            state="promoting",
            phase="copying verified cached output to final drive",
            completed_bytes=0,
            total_bytes=total_copy_bytes,
        )
        copied = 0

        def copy_file(source, destination):
            nonlocal copied
            source, destination = Path(source), Path(destination)
            with source.open("rb") as reader, destination.open("xb") as writer:
                while block := reader.read(4 * 1024 * 1024):
                    writer.write(block)
                    copied += len(block)
                    self._status["completed_bytes"] = copied
                    self._write_status()
                writer.flush()
                os.fsync(writer.fileno())
            shutil.copystat(source, destination)
            return str(destination)

        try:
            shutil.copytree(self.output, staging, copy_function=copy_file)
            target_inventory = _tree_inventory(staging)
            if target_inventory != source_inventory:
                raise OSError("Final staging inventory differs from the verified cache tree")
            promotion = dict(
                schema="framerudiments.cache-promotion.v1",
                state="verified cache and final staging trees matched before final-directory promotion",
                completed_at=_now(),
                cache_root=str(self.cache_root),
                cached_output=str(self.output),
                final_output=str(self.final_output),
                reserve_bytes=self.reserve_bytes,
                margin_bytes=self.margin_bytes,
                cached_job_bytes=cache_bytes,
                final_tree_bytes=total_copy_bytes,
                files=len(source_inventory),
                output_sha256=result["output"]["sha256"],
                source_inventory=source_inventory,
            )
            manifest["cache_promotion"] = promotion
            write_json(staging / "manifest.json", manifest)
            write_json(staging / "cache-promotion.json", promotion)
            staging.rename(self.final_output)
            final_video = self.final_output / "video.mp4"
            if sha256(final_video) != result["output"]["sha256"]:
                raise OSError("Promoted final video hash differs from the render result")
            final_manifest = json.loads((self.final_output / "manifest.json").read_text(encoding="utf-8"))
            self.update(state="completed", phase="verified final output promoted from cache")
            return final_manifest
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise

    def _rewrite_output_records(self, result):
        def relocate(value):
            path = Path(value).resolve()
            try:
                relative = path.relative_to(self.output)
            except ValueError:
                return value
            return str(self.final_output / relative)

        manifest_path = self.output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["output"]["path"] = relocate(manifest["output"]["path"])
        if manifest.get("analysis_sidecar"):
            manifest["analysis_sidecar"]["path"] = relocate(manifest["analysis_sidecar"]["path"])
        for item in manifest.get("analysis_sidecars", []):
            item["path"] = relocate(item["path"])
        manifest["cache_execution"] = dict(
            schema="framerudiments.cache-execution.v1",
            cached_output=str(self.output),
            final_output=str(self.final_output),
            reserve_bytes=self.reserve_bytes,
            margin_bytes=self.margin_bytes,
            state="render and technical QA completed in cache; verified promotion pending",
        )
        write_json(manifest_path, manifest)
        health_path = self.output / "candidate-health.json"
        health = json.loads(health_path.read_text(encoding="utf-8"))
        for item in health.get("artifacts", []):
            item["path"] = relocate(item["path"])
        write_json(health_path, health)
        result.clear()
        result.update(manifest)
        return manifest

    def finish(self):
        self.release_reservations()
        self.lease.close()
        if self.final_output.is_dir():
            _remove_tree(self.job)
        _remove_empty(self.cache_root / RESERVATION_DIRECTORY)
        _remove_empty(self.final_output.parent / RESERVATION_DIRECTORY)

    def fail(self, error):
        try:
            self._status.update(state="failed", phase="cache job preserved for inspection or resume", error=str(error))
            self._write_status(force=True)
        finally:
            self.release_reservations()
            self.lease.close()
            _remove_empty(self.cache_root / RESERVATION_DIRECTORY)
            _remove_empty(self.final_output.parent / RESERVATION_DIRECTORY)

    def release_reservations(self):
        for path in self.reservations.values():
            _release(path)


def cache_status(cache_root):
    root = Path(cache_root).resolve()
    if not root.exists():
        return dict(schema="framerudiments.cache-status.v1", root=str(root), empty=True, jobs=[], reservations=[])
    reservation_root = root / RESERVATION_DIRECTORY
    reservations = (
        [dict(path=str(path), bytes=path.stat().st_size) for path in sorted(reservation_root.glob("*")) if path.is_file()]
        if reservation_root.is_dir()
        else []
    )
    jobs = []
    for job in sorted(path for path in root.iterdir() if path.is_dir() and path.name != RESERVATION_DIRECTORY):
        status_path = job / "work-cache.json"
        status = None
        if status_path.is_file():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                status = dict(state="unreadable", phase="inspect cache metadata")
        jobs.append(dict(path=str(job), status=status))
    return dict(
        schema="framerudiments.cache-status.v1",
        checked_at=_now(),
        root=str(root),
        empty=not jobs and not reservations,
        jobs=jobs,
        reservations=reservations,
    )
