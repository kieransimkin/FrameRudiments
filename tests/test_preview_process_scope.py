"""Actual spawned descendants and Windows Job API contracts for S6 ownership.

Windows API doubles exercise error/handle contracts on every platform; actual
Job containment is exercised only by the real-process test on Windows CI.
"""
import ctypes
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep

import pytest

from framerudiments.preview_process_scope import (
    ParentProcessScope, ProcessScopeError, _Accounting, _ExtendedLimits,
    _HANDLE, _WindowsJob, _unreaped_child, child_enter_scope,
)


def _worker(pipe, marker):
    mode = child_enter_scope()
    pipe.send(mode)
    if pipe.recv() != 'admit':
        return
    child = subprocess.Popen([sys.executable, '-c',
        'from pathlib import Path; from time import sleep, monotonic; import sys\n'
        'p=Path(sys.argv[1])\n'
        'while True:\n p.write_text(str(monotonic())); sleep(.01)\n', marker])
    pipe.send(child.pid)
    while True:
        # Simulate an uncooperative native frame that never reads cancellation.
        sleep(1)


def _wait(predicate, seconds=5):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if predicate():
            return
        sleep(.01)
    raise AssertionError('owned process fixture did not become ready')


def _spawn(tmp_path):
    parent, child = multiprocessing.get_context('spawn').Pipe()
    marker = tmp_path / 'descendant.txt'
    process = multiprocessing.get_context('spawn').Process(target=_worker, args=(child, str(marker)))
    process.start()
    child.close()
    assert parent.poll(10)
    mode = parent.recv()
    return process, parent, mode, marker


@pytest.mark.parametrize('reason', ['cancel', 'deadline'])
def test_hard_cancel_and_timeout_kill_owned_grandchild(tmp_path, reason):
    process, pipe, mode, marker = _spawn(tmp_path)
    scope = None
    try:
        scope = ParentProcessScope(process, mode=mode)
        pipe.send('admit')
        assert pipe.poll(10)
        descendant = pipe.recv()
        assert descendant != process.pid
        _wait(marker.is_file)
        if reason == 'deadline':
            # A native frame ignores the request and misses its bounded deadline.
            assert not pipe.poll(.03)
        else:
            pipe.send('cancel')
        scope.terminate(timeout_seconds=3)
        assert process.exitcode is not None
        stopped = marker.read_bytes()
        sleep(.06)
        assert marker.read_bytes() == stopped  # Grandchild, not merely root, stopped.
        scope.terminate(timeout_seconds=3)  # No second signal, even after reap.
        if os.name == 'posix' and Path('/proc').is_dir():
            state = Path(f'/proc/{descendant}/stat')
            assert not state.exists() or state.read_text().split(') ', 1)[1].split()[0] in {'Z', 'X'}
    finally:
        if scope:
            scope.terminate(timeout_seconds=3)
        else:
            process.terminate()
            process.join(3)
        pipe.close()
        process.close()


def test_startup_scope_can_be_terminated_before_native_admission(tmp_path):
    process, pipe, mode, marker = _spawn(tmp_path)
    scope = ParentProcessScope(process, mode=mode)
    try:
        scope.terminate()
        assert process.exitcode is not None and not marker.exists()
    finally:
        scope.terminate()
        pipe.close()
        process.close()


def test_bad_handshake_does_not_signal_a_waiting_process(tmp_path):
    process, pipe, _mode, marker = _spawn(tmp_path)
    try:
        with pytest.raises(ProcessScopeError, match='handshake'):
            ParentProcessScope(process, mode='unverified')
        assert process.is_alive()
        assert not marker.exists()
    finally:
        process.terminate()  # Never admitted: no descendant exists.
        process.join(3)
        pipe.close()
        process.close()


def test_reaped_child_cannot_be_reused_as_a_signal_target(monkeypatch):
    def already_reaped(*_args):
        raise ChildProcessError('injected lost ownership')
    monkeypatch.setattr(os, 'waitid', already_reaped, raising=False)
    for name in ('P_PID', 'WEXITED', 'WNOHANG', 'WNOWAIT'):
        if not hasattr(os, name):
            monkeypatch.setattr(os, name, 1, raising=False)
    with pytest.raises(ProcessScopeError, match='ownership lost'):
        _unreaped_child(12345)


class WinAPI:
    """ABI/ownership double; not a claim that native Windows was executed."""
    def __init__(self, *, failure=None):
        self.failure, self.calls, self.active = failure, [], 1
    def CreateJobObjectW(self, security, name):
        assert security is None and name is None
        return 11
    def SetInformationJobObject(self, job, kind, pointer, size):
        limits = ctypes.cast(pointer, ctypes.POINTER(_ExtendedLimits)).contents
        assert job == 11 and kind == 9 and size == ctypes.sizeof(limits)
        assert limits.basic.flags == 0x2000  # Kill on close; no breakaway flag.
        return self.failure != 'limits'
    def GetCurrentProcess(self):
        return 99
    def DuplicateHandle(self, source, handle, target, pointer, rights, inherit, options):
        assert (source, handle, target, rights, inherit, options) == (99, 77, 99, 0, False, 2)
        ctypes.cast(pointer, ctypes.POINTER(_HANDLE)).contents.value = 22
        return self.failure != 'duplicate'
    def GetProcessId(self, handle):
        assert handle == 22
        return 456 if self.failure != 'identity' else 999
    def AssignProcessToJobObject(self, job, handle):
        self.calls.append(('assign', job, handle))
        return self.failure != 'assign'
    def TerminateJobObject(self, job, code):
        self.calls.append(('terminate', job, code))
        if self.failure != 'timeout':
            self.active = 0
        return self.failure != 'terminate'
    def QueryInformationJobObject(self, job, kind, pointer, size, returned):
        assert job == 11 and kind == 1 and returned is None
        counters = ctypes.cast(pointer, ctypes.POINTER(_Accounting)).contents
        assert size == ctypes.sizeof(counters)
        counters.active = self.active
        return self.failure != 'query'
    def CloseHandle(self, handle):
        self.calls.append(('close', handle))
        return True


def test_windows_job_kills_all_members_and_closes_only_owned_handles():
    api = WinAPI()
    scope = _WindowsJob(77, 456, api=api)
    scope.terminate(monotonic()+1)
    scope.terminate(monotonic()+1)
    assert api.calls == [('assign', 11, 22), ('terminate', 11, 1), ('close', 11), ('close', 22)]


@pytest.mark.parametrize('failure', ['limits', 'duplicate', 'identity', 'assign'])
def test_windows_admission_failure_never_authorizes_work_and_closes_job(failure):
    api = WinAPI(failure=failure)
    with pytest.raises(ProcessScopeError):
        _WindowsJob(77, 456, api=api)
    assert ('close', 11) in api.calls
    assert not any(call[0] == 'terminate' for call in api.calls)
    assert not any(call == ('close', 77) for call in api.calls)  # Owner's handle is untouched.


@pytest.mark.parametrize('failure', ['timeout', 'terminate', 'query'])
def test_windows_failed_cleanup_stays_visible_and_can_be_retried(failure):
    api = WinAPI(failure=failure)
    scope = _WindowsJob(77, 456, api=api)
    with pytest.raises(ProcessScopeError):
        scope.terminate(monotonic()+.01)
    assert scope.job == 11  # Do not mark unverified cleanup complete.
    api.failure = None
    api.active = 0
    scope.terminate(monotonic()+1)
    assert scope.job is None and scope.process is None
