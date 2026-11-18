"""S6 spawned transport tests with explicit synthetic, scope-owned child pixels.

These exercise real spawn/IPC/files/cancellation and cleanup, not native engine
fidelity. Native feedback parity is qualified separately with the real engines.
"""
from hashlib import sha256
from io import BytesIO
import json
import multiprocessing
import os
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

from PIL import Image
import pytest

from framerudiments.preview_frame_cache import MAX_PNG_BYTES
from framerudiments.preview_process_scope import child_enter_scope
from framerudiments.preview_refinement_process import RefinementCancelled, RefinementSpec, RefinementWorkerError
from framerudiments.preview_replay import StatefulSeekUnavailable
from framerudiments.preview_stateful_process import SpawnStatefulRenderer, _receive, _send


def status(frame, phase):
    return dict(enabled=True, mode='verified_prefix_replay', phase=phase, requested_frame=frame,
                verified_prefix_frames=frame + 1 if phase == 'ready' else 0,
                live_next_frame=frame + 1, current_request_frames=2 * (frame + 1),
                replayed_frames=2 * (frame + 1), max_prefix_frames=1800,
                max_request_frames=3600, last_reason=None, tolerance_rgba8=0,
                engine_identity_sha256='e'*64)


def scoped_worker(connection, spec, directory):
    """No cache handle, native renderer, user paths or external process discovery."""
    mode = child_enter_scope()
    _send(connection, {'kind': 'scope-ready', 'mode': mode, 'pid': os.getpid()})
    assert _receive(connection) == {'kind': 'scope-owned'}
    behavior = json.loads(spec.draft_json)['behavior']
    try:
        while True:
            command = _receive(connection)
            if command is None:
                break
            frame = command['frame']
            _send(connection, {'kind': 'progress', 'frame': frame, 'stateful': status(frame, 'verifying')})
            if behavior in ('cancel', 'timeout') and frame > 0:
                while True:
                    sleep(.05)  # Simulated native call which never cooperates.
            if behavior in ('replay_mismatch', 'runtime_identity_changed', 'input_identity_changed'):
                _send(connection, {'ok': False, 'frame': frame, 'reason': behavior})
                continue
            data = BytesIO()
            image = Image.new('RGBA', (16, 16), (frame, 17, 92, 255))
            image.save(data, 'PNG')
            png = data.getvalue()
            result = Path(directory)/'result.png'
            result.write_bytes(png)
            reply = dict(ok=True, frame=frame, bytes=len(png), sha256=sha256(png).hexdigest(),
                         stateful=status(frame, 'ready'), setup_ms=3,
                         receipt=dict(frame=frame, pixels_sha256=sha256(image.tobytes()).hexdigest(),
                                      prefix_sha256='f'*64, engine_identity_sha256='e'*64))
            if behavior == 'wrong_hash':
                reply['sha256'] = '0'*64
            elif behavior == 'oversized':
                reply['bytes'] = MAX_PNG_BYTES + 1
            elif behavior == 'wrong_file_size':
                reply['bytes'] += 1
            elif behavior == 'wrong_frame':
                reply['frame'] += 1
            elif behavior == 'unverified':
                reply['stateful']['phase'] = 'verifying'
            elif behavior == 'unverified_pixels':
                reply['receipt']['pixels_sha256'] = '0'*64
            elif behavior == 'incomplete_prefix':
                reply['stateful']['verified_prefix_frames'] = frame
            elif behavior == 'wrong_engine':
                reply['receipt']['engine_identity_sha256'] = '0'*64
            _send(connection, reply)
    finally:
        try:
            _send(connection, {'kind': 'closed'})
            # The parent owns the group identity until it terminates/reaps us.
            while True:
                _receive(connection)
        except (EOFError, BrokenPipeError, OSError):
            connection.close()


def renderer(tmp_path, behavior='good'):
    spec = RefinementSpec.from_snapshot(tmp_path/'synthetic-project.json', {},
                                       {'behavior': behavior}, {}, tmp_path/'workers')
    return SpawnStatefulRenderer(spec, timeout_seconds=10, worker_target=scoped_worker)


def assert_clean(worker, parent):
    assert worker.process is None and worker.connection is None and worker.directory is None
    assert not worker.cleanup_error
    assert worker.last_pid not in {child.pid for child in multiprocessing.active_children()}
    assert not list(parent.iterdir())


def test_real_spawn_reports_progress_and_verifies_png_metadata_before_return(tmp_path):
    worker = renderer(tmp_path)
    reports = []
    try:
        png = worker.render_cancellable(7, 'Q5', lambda: False, reports.append)
        with Image.open(BytesIO(png)) as image:
            assert image.mode == 'RGBA' and image.getpixel((0, 0)) == (7, 17, 92, 255)
        assert [row['phase'] for row in reports] == ['verifying', 'ready']
        assert all(row['requested_frame'] == 7 and row['production_equivalent'] is False for row in reports)
        assert worker.last_setup_ms >= 3
        first_pid = worker.last_pid
        assert worker(4, lambda: False).startswith(b'\x89PNG')  # S4 callback convention also works.
        assert worker.last_pid == first_pid
    finally:
        worker.close()
    assert_clean(worker, tmp_path/'workers')


def test_new_intent_cancels_uncooperative_replay_and_reaps_before_replacement(tmp_path):
    worker = renderer(tmp_path, 'cancel')
    worker(0)
    first_pid = worker.last_pid
    cancel = Event()
    started = monotonic()
    try:
        with pytest.raises(RefinementCancelled):
            worker.render_cancellable(4, 'Q5', cancel.is_set,
                                      lambda row: cancel.set() if row['phase'] == 'verifying' else None)
        assert monotonic() - started < 4
        assert worker.stateful_status['phase'] == 'cancelled'
        assert_clean(worker, tmp_path/'workers')
        assert worker(0).startswith(b'\x89PNG')
        assert worker.last_pid != first_pid
    finally:
        worker.close()
    assert_clean(worker, tmp_path/'workers')


def test_timeout_does_not_wait_for_a_native_call_to_finish(tmp_path):
    worker = renderer(tmp_path, 'timeout')
    worker(0)  # Time the native request, independently of platform spawn startup.
    worker.timeout = .05
    started = monotonic()
    try:
        with pytest.raises(StatefulSeekUnavailable) as error:
            worker(1)
        assert error.value.reason_code == 'worker_timeout'
        assert monotonic() - started < 4
        assert worker.stateful_status['phase'] == 'unavailable'
    finally:
        worker.close()
    assert_clean(worker, tmp_path/'workers')


@pytest.mark.parametrize('reason', ['replay_mismatch', 'runtime_identity_changed', 'input_identity_changed'])
def test_failed_replay_identity_stays_unavailable_after_child_cleanup(tmp_path, reason):
    worker = renderer(tmp_path, reason)
    try:
        with pytest.raises(StatefulSeekUnavailable) as error:
            worker(1)
        assert error.value.reason_code == reason
        first_pid = worker.last_pid
        assert_clean(worker, tmp_path/'workers')
        with pytest.raises(StatefulSeekUnavailable) as error:
            worker(0)
        assert error.value.reason_code == reason
        assert worker.last_pid == first_pid  # No fresh process may silently requalify the failed session.
    finally:
        worker.close()


@pytest.mark.parametrize('behavior', ['wrong_hash', 'oversized', 'wrong_file_size', 'wrong_frame', 'unverified',
                                      'unverified_pixels', 'incomplete_prefix', 'wrong_engine'])
def test_invalid_or_unverified_worker_replies_cannot_return_pixels(tmp_path, behavior):
    worker = renderer(tmp_path, behavior)
    try:
        with pytest.raises(RefinementWorkerError):
            worker(3)
        assert worker.stateful_status['phase'] == 'unavailable'
    finally:
        worker.close()
    assert_clean(worker, tmp_path/'workers')


def test_prefix_limit_is_visible_and_can_recover_at_an_earlier_frame(tmp_path):
    worker = renderer(tmp_path)
    with pytest.raises(StatefulSeekUnavailable) as error:
        worker(1800)
    assert error.value.reason_code == 'prefix_limit_exceeded'
    assert worker.last_pid is None and not (tmp_path/'workers').exists()
    try:
        assert worker(0).startswith(b'\x89PNG')
    finally:
        worker.close()
    assert_clean(worker, tmp_path/'workers')


def test_ready_progress_cannot_return_pixels_after_it_supersedes_the_request(tmp_path):
    worker = renderer(tmp_path)
    cancel = Event()
    try:
        with pytest.raises(RefinementCancelled):
            worker.render_cancellable(2, 'Q5', cancel.is_set,
                                      lambda row: cancel.set() if row['phase'] == 'ready' else None)
    finally:
        worker.close()
    assert_clean(worker, tmp_path/'workers')


def test_process_adapter_keeps_one_owner_thread(tmp_path):
    worker = renderer(tmp_path)
    worker(0)
    errors = []
    def foreign():
        try:
            worker.close()
        except RuntimeError as error:
            errors.append(str(error))
    thread = Thread(target=foreign)
    thread.start()
    thread.join(timeout=2)
    try:
        assert errors and 'owning thread' in errors[0]
        assert worker(1).startswith(b'\x89PNG')
    finally:
        worker.close()
    assert_clean(worker, tmp_path/'workers')
