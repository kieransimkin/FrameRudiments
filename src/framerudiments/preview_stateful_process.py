"""S6 isolated prefix replay with owned descendant cleanup and bounded replies.

The child never receives a cache handle. Only the coordinator may admit pixels.
The scoped worker stays alive until its owner terminates the scope, including
after a graceful native close, so a POSIX process-group ID cannot be reused.
"""
from hashlib import sha256
from io import BytesIO
import json
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import get_ident
from time import monotonic

from PIL import Image

from .preview_frame_cache import MAX_PNG_BYTES, _verified_png
from .preview_refinement_process import RefinementCancelled, RefinementSpec, RefinementWorkerError
from .preview_stream_contract import canonical_digest, _integer

MAX_REPLY_BYTES = 65536
MAX_PREFIX_FRAMES = 1800
MAX_REQUEST_FRAMES = 3600


def _send(connection, value):
    payload = json.dumps(value, allow_nan=False, separators=(',', ':')).encode()
    if len(payload) > MAX_REPLY_BYTES:
        raise ValueError('Replay control reply exceeds limit')
    connection.send_bytes(payload)


def _receive(connection):
    return json.loads(connection.recv_bytes(MAX_REPLY_BYTES))


def _stateful_worker(connection, spec, directory):
    from .preview_process_scope import child_enter_scope

    renderer = None
    stopping = False
    last_report = 0.0
    last_phase = None
    try:
        mode = child_enter_scope()
        _send(connection, {'kind': 'scope-ready', 'mode': mode, 'pid': os.getpid()})
        # The parent attaches the Windows Job before this handshake completes.
        # No native process, FFmpeg or browser may start before it.
        if _receive(connection) != {'kind': 'scope-owned'}:
            raise ValueError('Replay process scope was not acknowledged')
        os.environ['FRAMERUDIMENTS_PREVIEW_PROCESS_SCOPE'] = '1'
        # Forced termination must not strand a profile outside the owned tree.
        os.environ['FRAMERUDIMENTS_BROWSER_PROFILE_ROOT'] = str(Path(directory)/'profiles')
        from .preview_replay import ReplayCancelled, StatefulSeekUnavailable
        from .preview_stateful_renderer import StatefulPreviewRenderer

        draft, sidecars = json.loads(spec.draft_json), json.loads(spec.sidecars_json)
        draft_hash = canonical_digest(draft)

        def unchanged_project():
            original = json.loads(Path(spec.project_file).read_text(encoding='utf-8-sig'))
            if canonical_digest(original) != spec.original_sha256:
                raise StatefulSeekUnavailable('input_identity_changed')

        def validate(original, candidate):
            if (canonical_digest(original) != spec.original_sha256
                    or canonical_digest(candidate) != draft_hash):
                raise StatefulSeekUnavailable('input_identity_changed')

        renderer = StatefulPreviewRenderer(spec.project_file, draft, sidecars,
                    validate_draft=validate, verifier=unchanged_project,
                    temp_parent=Path(directory)/'native')
        while True:
            command = _receive(connection)
            if command is None:
                break
            if not isinstance(command, dict) or set(command) != {'frame'}:
                raise ValueError('Invalid replay request')
            frame = _integer(command['frame'], 'replay frame', 0, MAX_PREFIX_FRAMES - 1)

            def cancelled():
                nonlocal stopping
                if connection.poll():
                    # The parent sends only shutdown while this job is active.
                    if _receive(connection) is not None:
                        raise ValueError('Unexpected active replay command')
                    stopping = True
                return stopping

            def progress(status):
                nonlocal last_report, last_phase
                now = monotonic()
                phase = status.get('phase')
                if now - last_report >= .1 or phase != last_phase:
                    _send(connection, {'kind': 'progress', 'frame': frame, 'stateful': status})
                    last_report, last_phase = now, phase

            try:
                unchanged_project()
                png = renderer(frame, 'Q5', cancelled=cancelled, progress=progress)
                unchanged_project()
                if cancelled():
                    raise ReplayCancelled()
                if not isinstance(png, bytes) or not 0 < len(png) <= MAX_PNG_BYTES:
                    raise StatefulSeekUnavailable('invalid_replay_result')
                temporary = Path(directory)/'result.tmp'
                temporary.write_bytes(png)
                temporary.replace(Path(directory)/'result.png')
                _send(connection, {'ok': True, 'frame': frame, 'bytes': len(png),
                    'sha256': sha256(png).hexdigest(), 'stateful': renderer.stateful_status,
                    'receipt': renderer.verified_receipt(frame),
                    'setup_ms': renderer.last_setup_ms})
            except ReplayCancelled:
                _send(connection, {'ok': False, 'frame': frame, 'reason': 'cancelled'})
            except StatefulSeekUnavailable as exc:
                _send(connection, {'ok': False, 'frame': frame, 'reason': exc.reason_code,
                                   'stateful': renderer.stateful_status})
            except Exception:
                # Native messages can contain source paths or preset text.
                _send(connection, {'ok': False, 'frame': frame, 'reason': 'native_runtime_unavailable'})
            if stopping:
                break
    except (EOFError, BrokenPipeError, OSError):
        pass
    except BaseException:
        try:
            _send(connection, {'ok': False, 'reason': 'worker_failed'})
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        try:
            if renderer is not None:
                renderer.close()
            _send(connection, {'kind': 'closed'})
        except BaseException:
            try:
                _send(connection, {'kind': 'cleanup-failed'})
            except (EOFError, BrokenPipeError, OSError):
                pass
        # Keep the scope leader alive until its parent kills the owned scope.
        # A broken parent pipe must not leave native descendants running.
        try:
            while True:
                _receive(connection)
        except (EOFError, BrokenPipeError, OSError):
            if os.name == 'posix':
                import signal
                if os.getpgrp() == os.getpid():
                    os.killpg(os.getpid(), signal.SIGKILL)
        finally:
            connection.close()


class SpawnStatefulRenderer:
    """Lazy owner-thread adapter for both foreground and stopped Q5 work."""
    stateful_replay = True

    def __init__(self, spec, *, timeout_seconds=120, worker_target=_stateful_worker):
        if not isinstance(spec, RefinementSpec):
            raise ValueError('RefinementSpec required')
        if type(timeout_seconds) not in (float, int) or not 0 < timeout_seconds <= 600:
            raise ValueError('Replay timeout must be 0..600 seconds')
        self.spec, self.timeout, self.target = spec, timeout_seconds, worker_target
        self.owner = None
        self.process = self.connection = self.directory = self.scope = None
        self.last_pid = None
        self.last_setup_ms = 0
        self.cleanup_error = False
        self._unavailable = None
        self._engine_identity = None
        self.last_receipt = None
        self.stateful_status = dict(enabled=True, mode='verified_prefix_replay', phase='idle',
                requested_frame=None, verified_prefix_frames=0, current_request_frames=0,
                replayed_frames=0, live_next_frame=0, max_prefix_frames=MAX_PREFIX_FRAMES,
                max_request_frames=MAX_REQUEST_FRAMES, last_reason=None,
                checkpoint_restore=False, production_equivalent=False)

    def _owner(self):
        current = get_ident()
        if self.owner is None:
            self.owner = current
        elif self.owner != current:
            raise RuntimeError('Replay process must remain on its owning thread')

    def _start(self, cancelled, deadline):
        from .preview_process_scope import ParentProcessScope

        parent = Path(self.spec.temp_parent)
        parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix='probe-', dir=parent) as probe:
            path = Path(probe)/'test'
            path.write_bytes(b'stateful-temp-probe')
            if path.read_bytes() != b'stateful-temp-probe':
                raise OSError('Replay temporary directory preflight failed')
            path.replace(Path(probe)/'renamed')
        self.directory = TemporaryDirectory(prefix='replay-', dir=parent)
        context = multiprocessing.get_context('spawn')
        self.connection, child = context.Pipe()
        self.process = context.Process(target=self.target, args=(child, self.spec, self.directory.name),
                                       name='framerudiments-stateful', daemon=True)
        try:
            self.process.start()
            self.last_pid = self.process.pid
        finally:
            child.close()
        handshake_deadline = min(deadline, monotonic() + 10)
        while not self.connection.poll(.025):
            if cancelled():
                raise RefinementCancelled('superseded replay startup')
            if monotonic() >= handshake_deadline:
                raise RefinementWorkerError('Replay worker startup timeout')
        ready = _receive(self.connection)
        if (not isinstance(ready, dict) or ready.get('kind') != 'scope-ready'
                or ready.get('pid') != self.process.pid):
            raise RefinementWorkerError('Replay process scope handshake failed')
        self.scope = ParentProcessScope(self.process, mode=ready.get('mode'))
        _send(self.connection, {'kind': 'scope-owned'})

    def _status(self, reply, frame):
        status = reply.get('stateful')
        if not isinstance(status, dict) or len(json.dumps(status)) > 8192:
            raise RefinementWorkerError('Invalid replay status')
        if (status.get('mode') != 'verified_prefix_replay' or status.get('enabled') is not True
                or status.get('phase') not in {'idle', 'replaying', 'verifying', 'ready', 'unavailable', 'cancelled'}):
            raise RefinementWorkerError('Invalid replay phase')
        for name, maximum in (('verified_prefix_frames', MAX_PREFIX_FRAMES),
                              ('live_next_frame', MAX_PREFIX_FRAMES),
                              ('current_request_frames', MAX_REQUEST_FRAMES)):
            value = status.get(name)
            if type(value) is not int or not 0 <= value <= maximum:
                raise RefinementWorkerError('Invalid replay accounting')
        if status.get('phase') == 'ready' and (
                status['verified_prefix_frames'] < frame + 1 or status['live_next_frame'] != frame + 1
                or status.get('tolerance_rgba8') != 0):
            raise RefinementWorkerError('Incomplete replay verification')
        self.stateful_status = {**status, 'requested_frame': frame,
                               'production_equivalent': False, 'checkpoint_restore': False}

    def render_cancellable(self, frame, profile_id, cancelled, progress=None):
        from .preview_replay import StatefulSeekUnavailable

        self._owner()
        _integer(frame, 'absolute frame', 0, 10_000_000)
        if profile_id != 'Q5':
            raise StatefulSeekUnavailable('profile_unsupported')
        if self.cleanup_error:
            raise RefinementWorkerError('Replay cleanup requires a clean restart')
        self.last_setup_ms = 0
        self.last_receipt = None
        self.stateful_status = {**self.stateful_status, 'requested_frame': frame,
                               'phase': 'replaying', 'last_reason': None, 'current_request_frames': 0}
        reason = self._unavailable or ('prefix_limit_exceeded' if frame >= MAX_PREFIX_FRAMES else None)
        if reason:
            self.stateful_status.update(phase='unavailable', last_reason=reason)
            raise StatefulSeekUnavailable(reason)
        deadline = monotonic() + self.timeout
        try:
            if cancelled():
                raise RefinementCancelled('superseded replay')
            if self.process is None:
                started = monotonic()
                self._start(cancelled, deadline)
                self.last_setup_ms += (monotonic() - started)*1000
            _send(self.connection, {'frame': frame})
            while True:
                if cancelled():
                    raise RefinementCancelled('superseded replay')
                if monotonic() >= deadline:
                    raise StatefulSeekUnavailable('worker_timeout')
                if not self.connection.poll(.025):
                    continue
                reply = _receive(self.connection)
                if not isinstance(reply, dict) or reply.get('frame') != frame:
                    raise RefinementWorkerError('Replay worker returned an invalid frame')
                if reply.get('kind') == 'progress':
                    self._status(reply, frame)
                    if progress:
                        progress(dict(self.stateful_status))
                    continue
                if reply.get('ok') is not True:
                    reason = reply.get('reason', 'worker_failed')
                    if not isinstance(reason, str) or not reason.isascii() or not reason.replace('_', '').isalnum():
                        reason = 'worker_failed'
                    if len(reason) > 64:
                        reason = 'worker_failed'
                    if reason in {'replay_mismatch', 'runtime_identity_changed', 'input_identity_changed'}:
                        self._unavailable = reason
                    raise StatefulSeekUnavailable(reason)
                if type(reply.get('bytes')) is not int or not 0 < reply['bytes'] <= MAX_PNG_BYTES:
                    raise RefinementWorkerError('Invalid replay PNG size')
                self._status(reply, frame)
                if self.stateful_status['phase'] != 'ready':
                    raise RefinementWorkerError('Unverified replay result')
                path = Path(self.directory.name)/'result.png'
                if path.is_symlink() or not path.is_file() or path.stat().st_size != reply['bytes']:
                    raise RefinementWorkerError('Invalid replay result file')
                with path.open('rb') as source:
                    png = source.read(MAX_PNG_BYTES + 1)
                path.unlink()
                if len(png) != reply['bytes'] or sha256(png).hexdigest() != reply.get('sha256'):
                    raise RefinementWorkerError('Replay PNG checksum mismatch')
                receipt = reply.get('receipt')
                if not isinstance(receipt, dict) or receipt.get('frame') != frame:
                    raise RefinementWorkerError('Missing verified frame receipt')
                for name in ('pixels_sha256', 'prefix_sha256', 'engine_identity_sha256'):
                    digest = receipt.get(name)
                    if (not isinstance(digest, str) or len(digest) != 64
                            or any(c not in '0123456789abcdef' for c in digest)):
                        raise RefinementWorkerError('Invalid replay receipt digest')
                engine = receipt['engine_identity_sha256']
                if engine != self.stateful_status.get('engine_identity_sha256'):
                    raise RefinementWorkerError('Replay status and receipt disagree')
                if self._engine_identity is not None and engine != self._engine_identity:
                    self._unavailable = 'runtime_identity_changed'
                    raise StatefulSeekUnavailable('runtime_identity_changed')
                with Image.open(BytesIO(png)) as image:
                    width, height = image.size
                _, pixels = _verified_png(png, width, height)
                if pixels != receipt['pixels_sha256']:
                    raise RefinementWorkerError('Replay PNG differs from verified pixels')
                self._engine_identity = engine
                self.last_receipt = dict(receipt)
                if cancelled():
                    raise RefinementCancelled('superseded replay result')
                setup = reply.get('setup_ms', 0)
                if type(setup) in (float, int) and 0 <= setup <= self.timeout*1000:
                    self.last_setup_ms += setup
                if progress:
                    progress(dict(self.stateful_status))
                if cancelled():
                    raise RefinementCancelled('superseded verified replay')
                return png
        except RefinementCancelled:
            self.stateful_status.update(phase='cancelled', last_reason='cancelled')
            self.close()
            raise
        except Exception as exc:
            self.stateful_status.update(phase='unavailable', last_reason=
                exc.reason_code if isinstance(exc, StatefulSeekUnavailable) else 'worker_failed')
            self.close()
            raise

    def __call__(self, frame, profile_id='Q5'):
        # The S4 refinement supervisor supplies a cancellation callback as its
        # second argument; foreground uses render_cancellable explicitly.
        cancelled = profile_id if callable(profile_id) else lambda: False
        profile = 'Q5' if callable(profile_id) else profile_id
        return self.render_cancellable(frame, profile, cancelled)

    def release_compositor(self):
        self.close()

    def close(self):
        self._owner()
        try:
            process = self.process
            if process is not None and process.pid is not None:
                try:
                    _send(self.connection, None)
                    deadline = monotonic() + .25
                    while monotonic() < deadline:
                        if self.connection.poll(.025):
                            row = _receive(self.connection)
                            if isinstance(row, dict) and row.get('kind') in {'closed', 'cleanup-failed'}:
                                break
                except (OSError, EOFError, BrokenPipeError):
                    pass
                if self.scope is not None:
                    self.scope.terminate()
                else:
                    # No native work is allowed before scope ownership.
                    process.terminate()
                process.join(1)
                if process.is_alive():
                    raise RefinementWorkerError('Replay scope did not stop; replacement blocked')
            if process is not None:
                process.close()
            self.process = self.scope = None
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            if self.directory is not None:
                self.directory.cleanup()
                self.directory = None
        except BaseException:
            self.cleanup_error = True
            raise
