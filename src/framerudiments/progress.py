"""Durable, low-overhead progress heartbeats for long render phases."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import threading
import time


class ProgressReporter:
    """Write an atomic progress snapshot and a flushed human-readable heartbeat.

    The heartbeat thread is deliberately independent of frame completion so an
    expensive compositor frame still produces regular liveness evidence.
    """

    def __init__(
        self,
        output,
        total_frames,
        *,
        mode,
        interval_seconds=10.0,
        project_total_frames=None,
        stream=None,
    ):
        if type(total_frames) is not int or total_frames < 1:
            raise ValueError("Progress total_frames must be a positive integer")
        if type(interval_seconds) not in (int, float) or not 0.1 <= interval_seconds <= 3600:
            raise ValueError("Progress interval must be between 0.1 and 3600 seconds")
        if project_total_frames is None:
            project_total_frames = total_frames
        if type(project_total_frames) is not int or project_total_frames < total_frames:
            raise ValueError("Progress project_total_frames must cover total_frames")
        self.path = Path(output) / "render-progress.json"
        self.total_frames = total_frames
        self.project_total_frames = project_total_frames
        self.mode = mode
        self.interval_seconds = float(interval_seconds)
        self.stream = sys.stderr if stream is None else stream
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._thread_error = None
        self._started_at = None
        self._started_monotonic = None
        self._phase = "preparing"
        self._state = "running"
        self._completed_frames = 0
        self._active_frame = None
        self._finalized_frames = 0
        self._error = None

    def start(self, phase, *, completed_frames=0, finalized_frames=0):
        if self._thread is not None:
            raise RuntimeError("Progress reporter has already started")
        self._validate_count(completed_frames, "completed_frames")
        self._validate_count(finalized_frames, "finalized_frames")
        self._started_at = datetime.now(timezone.utc)
        self._started_monotonic = time.monotonic()
        self._phase = phase
        self._completed_frames = completed_frames
        self._finalized_frames = finalized_frames
        self._emit()
        self._thread = threading.Thread(
            target=self._heartbeat,
            name="framerudiments-progress",
            daemon=True,
        )
        self._thread.start()
        return self

    def active(self, frame):
        if type(frame) is not int or not 0 <= frame < self.total_frames:
            raise ValueError("Progress active frame is outside the render range")
        self._raise_thread_error()
        with self._lock:
            self._active_frame = frame

    def advance(self, completed_frames):
        self._validate_count(completed_frames, "completed_frames")
        self._raise_thread_error()
        with self._lock:
            if completed_frames < self._completed_frames:
                raise ValueError("Progress completed_frames cannot move backwards")
            self._completed_frames = completed_frames

    def phase(self, phase, *, completed_frames=None, finalized_frames=None):
        self._raise_thread_error()
        with self._lock:
            self._phase = phase
            if completed_frames is not None:
                self._validate_count(completed_frames, "completed_frames")
                self._completed_frames = completed_frames
            if finalized_frames is not None:
                self._validate_count(finalized_frames, "finalized_frames")
                self._finalized_frames = finalized_frames
            self._emit_locked()

    def finish(self, phase="complete", *, finalized_frames=None):
        self._stop_thread()
        self._raise_thread_error()
        with self._lock:
            self._state = "completed"
            self._phase = phase
            self._completed_frames = self.total_frames
            if finalized_frames is not None:
                self._validate_count(finalized_frames, "finalized_frames")
                self._finalized_frames = finalized_frames
            self._active_frame = None
            self._emit_locked()

    def fail(self, error, *, phase=None, frame=None):
        self._stop_thread()
        with self._lock:
            self._state = "failed"
            if phase is not None:
                self._phase = phase
            if frame is not None:
                self._active_frame = frame
            self._error = str(error).strip() or type(error).__name__
            self._emit_locked()

    def close(self):
        """Stop a still-running heartbeat without changing its last state."""
        self._stop_thread()

    def _heartbeat(self):
        while not self._stop.wait(self.interval_seconds):
            try:
                self._emit()
            except BaseException as exc:
                self._thread_error = exc
                self._stop.set()
                return

    def _stop_thread(self):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=min(5.0, self.interval_seconds + 1.0))
        self._thread = None

    def _raise_thread_error(self):
        if self._thread_error is not None:
            raise RuntimeError(f"Progress heartbeat failed: {self._thread_error}") from self._thread_error

    def _validate_count(self, value, name):
        if type(value) is not int or not 0 <= value <= self.total_frames:
            raise ValueError(f"Progress {name} must be in [0,{self.total_frames}]")

    def _emit(self):
        with self._lock:
            self._emit_locked()

    def _emit_locked(self):
        value = self._snapshot()
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for attempt in range(5):
            try:
                temporary.replace(self.path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                # A Windows monitor can briefly hold the previous snapshot open.
                time.sleep(0.05)
        eta = value["estimated_remaining_seconds"]
        eta_text = "unknown" if eta is None else f"{eta:.1f}s"
        print(
            "FrameRudiments progress: "
            f"{value['completed_frames']}/{value['total_frames']} "
            f"({value['percent']:.1f}%) phase={value['phase']} "
            f"elapsed={value['elapsed_seconds']:.1f}s eta={eta_text}",
            file=self.stream,
            flush=True,
        )

    def _snapshot(self):
        now = datetime.now(timezone.utc)
        elapsed = max(0.0, time.monotonic() - self._started_monotonic)
        completed = self._completed_frames
        average = elapsed / completed if completed else None
        remaining = average * (self.total_frames - completed) if average is not None else None
        completion = now + timedelta(seconds=remaining) if remaining is not None else None
        return dict(
            schema="framerudiments.render-progress.v1",
            state=self._state,
            mode=self.mode,
            phase=self._phase,
            completed_frames=completed,
            total_frames=self.total_frames,
            project_total_frames=self.project_total_frames,
            active_frame=self._active_frame,
            finalized_frames=self._finalized_frames,
            percent=round(completed * 100 / self.total_frames, 3),
            started_at=self._started_at.isoformat(),
            updated_at=now.isoformat(),
            elapsed_seconds=round(elapsed, 3),
            average_seconds_per_frame=round(average, 6) if average is not None else None,
            estimated_remaining_seconds=round(remaining, 3) if remaining is not None else None,
            estimated_completion_at=completion.isoformat() if completion is not None else None,
            error=self._error,
        )
