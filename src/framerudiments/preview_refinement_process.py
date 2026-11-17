"""S4 isolated Q5 renderer: spawn, bounded replies, cancellation and owned cleanup.

Only trusted package code is started. No cache/index or parent GL context is
passed to the child. The parent alone admits pixels using generation-fenced CAS.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import multiprocessing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import get_ident
from time import monotonic

from .preview_frame_cache import MAX_PNG_BYTES
from .preview_stream_contract import canonical_digest


class RefinementCancelled(RuntimeError):
    """Superseded cursor or foreground work; not a renderer failure."""


class RefinementWorkerError(RuntimeError):
    """Isolated renderer failed, timed out or produced an invalid reply."""


@dataclass(frozen=True)
class RefinementSpec:
    project_file: str
    original_sha256: str
    draft_json: str
    sidecars_json: str
    temp_parent: str

    @classmethod
    def from_snapshot(cls, path, original, draft, sidecars, temp_parent):
        # Serialized immutable copies: no parent's renderer, lock or mutable data.
        return cls(str(path), canonical_digest(original),
                   json.dumps(draft, ensure_ascii=False, allow_nan=False),
                   json.dumps(sidecars, ensure_ascii=False, allow_nan=False), str(temp_parent))


def _q5_worker(connection, spec, directory):
    """Runs on the child main thread; native contexts never cross ownership."""
    renderer = None
    try:
        from .preview_native_renderer import NativePreviewRenderer
        draft, sidecars = json.loads(spec.draft_json), json.loads(spec.sidecars_json)
        draft_hash = canonical_digest(draft)

        def unchanged_project():
            original = json.loads(Path(spec.project_file).read_text(encoding='utf-8-sig'))
            if canonical_digest(original) != spec.original_sha256:
                raise ValueError('Project changed since stream admission')

        def validate(original, candidate):
            if (canonical_digest(original) != spec.original_sha256
                    or canonical_digest(candidate) != draft_hash):
                raise ValueError('Refinement snapshot changed')

        renderer = NativePreviewRenderer(spec.project_file, draft, sidecars, validate_draft=validate,
                                         temp_parent=Path(directory)/'native')
        while True:
            frame = connection.recv()
            if frame is None:
                break
            if type(frame) is not int or frame < 0:
                raise ValueError('Invalid refinement frame')
            unchanged_project()
            png = renderer(frame, 'Q5')  # Always authored settings; no adaptive state.
            unchanged_project()
            if not isinstance(png, bytes) or not 0 < len(png) <= MAX_PNG_BYTES:
                raise ValueError('Invalid Q5 result size')
            temporary = Path(directory)/'result.tmp'
            temporary.write_bytes(png)
            temporary.replace(Path(directory)/'result.png')
            connection.send({'ok': True, 'frame': frame, 'bytes': len(png),
                             'sha256': sha256(png).hexdigest()})
    except (EOFError, BrokenPipeError):
        pass
    except BaseException as exc:
        try:
            # Do not expose file names, lyrics or source contents in diagnostics.
            connection.send({'ok': False, 'error_type': type(exc).__name__})
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        try:
            if renderer is not None:
                renderer.close()
        finally:
            connection.close()


class SpawnQ5Renderer:
    """One supervised child; instantiated/used/closed on the supervisor thread.

    Metadata uses a small pipe; PNGs use an owned directory, avoiding an
    uninterruptible large pipe receive. Terminated children cannot publish.
    A cancelled process is joined before its directory is removed or reused.
    """
    def __init__(self, spec, *, timeout_seconds=120, worker_target=_q5_worker):
        if not isinstance(spec, RefinementSpec):
            raise ValueError('RefinementSpec required')
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 600:
            raise ValueError('Refinement timeout must be 0..600 seconds')
        self.spec, self.timeout, self.target = spec, timeout_seconds, worker_target
        self.owner = get_ident()
        self.process = self.connection = self.directory = None
        self.last_pid = None

    def _owner(self):
        if get_ident() != self.owner:
            raise RuntimeError('Refinement process must stay on its supervisor thread')

    def _start(self):
        parent = Path(self.spec.temp_parent)
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='probe-', dir=parent) as probe:
            path = Path(probe)/'test'
            path.write_bytes(b'q5-temp-probe')
            if path.read_bytes() != b'q5-temp-probe':
                raise OSError('Refinement temp preflight failed')
            path.replace(Path(probe)/'renamed')
        self.directory = TemporaryDirectory(prefix='refinement-', dir=parent)
        # Explicit spawn works on Windows and never forks a parent GL context.
        context = multiprocessing.get_context('spawn')
        self.connection, child = context.Pipe()
        self.process = context.Process(target=self.target, args=(child, self.spec, self.directory.name),
                                       name='framerudiments-q5', daemon=True)
        try:
            self.process.start()
            self.last_pid = self.process.pid
        except BaseException:
            self.close()
            raise
        finally:
            child.close()

    def __call__(self, frame, cancelled):
        self._owner()
        if cancelled():
            raise RefinementCancelled('foreground priority')
        if self.process is None:
            self._start()
        deadline = monotonic() + self.timeout
        try:
            self.connection.send(frame)
            while True:
                if cancelled():
                    raise RefinementCancelled('superseded refinement')
                if monotonic() >= deadline:
                    raise RefinementWorkerError('Q5 worker timeout')
                if self.connection.poll(.025):
                    reply = self.connection.recv()
                    break
                if not self.process.is_alive():
                    raise RefinementWorkerError('Q5 worker exited')
            if (not isinstance(reply, dict) or reply.get('ok') is not True
                    or reply.get('frame') != frame or type(reply.get('bytes')) is not int
                    or not 0 < reply['bytes'] <= MAX_PNG_BYTES):
                raise RefinementWorkerError('Q5 worker rejected the frame')
            path = Path(self.directory.name)/'result.png'
            if path.is_symlink() or not path.is_file() or path.stat().st_size != reply['bytes']:
                raise RefinementWorkerError('Q5 result file is invalid')
            with path.open('rb') as source:
                png = source.read(MAX_PNG_BYTES + 1)
            path.unlink()
            if len(png) != reply['bytes'] or sha256(png).hexdigest() != reply.get('sha256'):
                raise RefinementWorkerError('Q5 result checksum mismatch')
            if cancelled():
                raise RefinementCancelled('superseded refinement')
            return png
        except BaseException:
            self.close()
            raise

    def close(self):
        self._owner()
        process = self.process
        if process is not None:
            if process.pid is not None:
                # Idle children get a chance to close contexts. A busy child is
                # forcibly reaped within bounded waits (never an unrelated PID).
                try:
                    self.connection.send(None)
                except (OSError, EOFError, BrokenPipeError):
                    pass
                process.join(.15)
                if process.is_alive():
                    process.terminate()
                    process.join(.5)
                if process.is_alive():
                    process.kill()
                    process.join(.5)
                if process.is_alive():
                    raise RefinementWorkerError('Q5 worker did not stop; cleanup is blocked')
            process.close()
            self.process = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None
