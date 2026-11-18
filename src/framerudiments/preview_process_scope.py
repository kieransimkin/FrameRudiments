"""Owned S6 worker process trees; containment, not a security sandbox.

The child enters its scope before importing native engines and then waits for
parent admission. POSIX adapters must retain the inherited session/process group
(the S6 Butterchurn launcher disables Puppeteer's detached browser launch).
Windows uses a non-breakaway Job Object. A child must remain alive after its
last reply until ``terminate`` kills the scope; the owner must not poll/reap the
child first. No name-based process search, taskkill or unrelated-PID cleanup is used.
"""
import ctypes
from math import isfinite
import os
from pathlib import Path
import signal
from time import monotonic, sleep


class ProcessScopeError(RuntimeError):
    """The worker's ownership or complete termination could not be verified."""


def child_enter_scope():
    """Called first in a spawned child, before receiving permission to render."""
    if os.name == 'nt':
        return 'windows_job'
    if os.name != 'posix' or not all(hasattr(os, name) for name in ('setsid', 'waitid', 'WNOWAIT')):
        raise ProcessScopeError('No supported owned process scope on this platform')
    os.setsid()
    pid = os.getpid()
    if os.getpgid(pid) != pid or os.getsid(pid) != pid:
        raise ProcessScopeError('Spawned worker did not become its own session leader')
    return 'posix_session'


def _unreaped_child(pid):
    # WNOWAIT proves this is still our child without releasing its PID. An
    # already-dead, unreaped root still pins the process-group identity.
    try:
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except (ChildProcessError, OSError) as exc:
        raise ProcessScopeError('Worker ownership lost: root was reaped or is not our child') from exc


def _linux_live_group(pgid):
    """Return live members, ignoring non-executing orphan zombies on Linux.

    Container init may defer orphan reaping. Such zombies own no executable
    resources; counting killpg(0) alone would misreport them as running workers.
    """
    live = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            raw = (entry / 'stat').read_text()
            fields = raw[raw.rindex(')') + 2:].split()
            if int(fields[2]) == pgid and fields[0] not in {'Z', 'X'}:
                live.append(int(entry.name))
        except (FileNotFoundError, ProcessLookupError):
            continue  # A member exited while the process table was inspected.
        except (OSError, ValueError, IndexError) as exc:
            raise ProcessScopeError('Cannot verify worker process-group termination') from exc
    return live


class ParentProcessScope:
    """Admit a waiting child and kill its complete owned scope before reaping.

    ``mode`` is the value returned in the child's startup handshake. A failed
    constructor never authorizes native work: the caller must terminate/reap
    the still-waiting direct child. ``terminate`` is bounded and idempotent.
    It also joins the direct child, but does not close the Process object.
    """
    def __init__(self, process, *, mode):
        self.process = process
        self.pid = process.pid
        self.mode = mode
        self._signalled = self._closed = False
        self._job = None
        if type(self.pid) is not int or self.pid <= 1 or self.pid == os.getpid():
            raise ProcessScopeError('Invalid spawned worker identity')
        if os.name == 'nt':
            if mode != 'windows_job':
                raise ProcessScopeError('Invalid Windows worker scope handshake')
            # Process.sentinel is the exact retained Windows process handle,
            # avoiding a PID lookup that could attach a replacement process.
            self._job = _WindowsJob(process.sentinel, self.pid)
        elif os.name == 'posix' and mode == 'posix_session':
            _unreaped_child(self.pid)
            if os.getpgid(self.pid) != self.pid or os.getsid(self.pid) != self.pid:
                raise ProcessScopeError('Worker has not entered its owned POSIX session')
        else:
            raise ProcessScopeError('Unsupported worker scope handshake')

    def terminate(self, timeout_seconds=2):
        if type(timeout_seconds) not in (int, float) or not isfinite(timeout_seconds) or not 0 < timeout_seconds <= 30:
            raise ValueError('scope termination timeout must be finite and in (0, 30]')
        if self._closed:
            return
        deadline = monotonic() + timeout_seconds
        if self._job is not None:
            self._job.terminate(deadline)
            self.process.join(timeout=max(0, deadline - monotonic()))
            self._closed = True
            return
        if not self._signalled:
            _unreaped_child(self.pid)
            if os.getpgid(self.pid) != self.pid or os.getsid(self.pid) != self.pid:
                raise ProcessScopeError('Refusing to signal a changed worker session')
            os.killpg(self.pid, signal.SIGKILL)
            self._signalled = True
        # Reap only after the entire group has been signalled. Polling the root
        # before killpg could release its PID and invalidate the ownership pin.
        self.process.join(timeout=max(0, deadline - monotonic()))
        while True:
            if Path('/proc/self/stat').is_file():
                alive = bool(_linux_live_group(self.pid))
            else:
                try:
                    os.killpg(self.pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
            if not alive:
                self._closed = True
                return
            if monotonic() >= deadline:
                raise ProcessScopeError('Owned worker descendants did not terminate within the cleanup budget')
            sleep(min(.01, max(0, deadline - monotonic())))


# Fixed-width Windows ABI types also permit contract tests on non-Windows hosts.
_DWORD = ctypes.c_uint32
_HANDLE = ctypes.c_void_p


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', _DWORD), ('min_working_set', ctypes.c_size_t),
                ('max_working_set', ctypes.c_size_t), ('active_limit', _DWORD),
                ('affinity', ctypes.c_size_t), ('priority', _DWORD), ('scheduling', _DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', _IOCounters),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in
                ('user_time', 'kernel_time', 'period_user_time', 'period_kernel_time')] + [
                (name, _DWORD) for name in ('faults', 'total', 'active', 'terminated')]


def _windows_api():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'GetCurrentProcess': ([], _HANDLE),
        'CreateJobObjectW': ([_HANDLE, ctypes.c_wchar_p], _HANDLE),
        'SetInformationJobObject': ([_HANDLE, ctypes.c_int, _HANDLE, _DWORD], ctypes.c_int),
        'QueryInformationJobObject': ([_HANDLE, ctypes.c_int, _HANDLE, _DWORD, _HANDLE], ctypes.c_int),
        'DuplicateHandle': ([_HANDLE, _HANDLE, _HANDLE, ctypes.POINTER(_HANDLE), _DWORD, ctypes.c_int, _DWORD], ctypes.c_int),
        'GetProcessId': ([_HANDLE], _DWORD),
        'AssignProcessToJobObject': ([_HANDLE, _HANDLE], ctypes.c_int),
        'TerminateJobObject': ([_HANDLE, ctypes.c_uint], ctypes.c_int),
        'CloseHandle': ([_HANDLE], ctypes.c_int),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _win_check(value, operation):
    if not value:
        code = getattr(ctypes, 'get_last_error', lambda: 0)()
        raise ProcessScopeError(f'{operation} failed (WinError {code})')
    return value


class _WindowsJob:
    """One owned job; no breakaway permissions and no inherited job handle."""
    def __init__(self, process_handle, pid, *, api=None):
        self.api = api if api is not None else _windows_api()
        self.job = self.process = None
        self.terminated = False
        try:
            self.job = _win_check(self.api.CreateJobObjectW(None, None), 'CreateJobObjectW')
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            _win_check(self.api.SetInformationJobObject(self.job, 9, ctypes.byref(limits),
                                                       ctypes.sizeof(limits)), 'SetInformationJobObject')
            duplicated = _HANDLE()
            current = self.api.GetCurrentProcess()
            _win_check(self.api.DuplicateHandle(current, process_handle, current, ctypes.byref(duplicated),
                                                0, False, 2), 'DuplicateHandle')
            self.process = duplicated.value
            if self.api.GetProcessId(self.process) != pid:
                raise ProcessScopeError('Windows process handle does not match worker identity')
            _win_check(self.api.AssignProcessToJobObject(self.job, self.process), 'AssignProcessToJobObject')
        except BaseException:
            self._close()
            raise

    def _close(self):
        failures = []
        for attribute in ('job', 'process'):
            handle = getattr(self, attribute)
            if handle is not None:
                if self.api.CloseHandle(handle):
                    setattr(self, attribute, None)
                else:
                    failures.append(attribute)
        if failures:
            raise ProcessScopeError('Windows scope handle cleanup failed: ' + ', '.join(failures))

    def terminate(self, deadline):
        if self.job is None:
            return
        if not self.terminated:
            _win_check(self.api.TerminateJobObject(self.job, 1), 'TerminateJobObject')
            self.terminated = True
        while True:
            counters = _Accounting()
            _win_check(self.api.QueryInformationJobObject(self.job, 1, ctypes.byref(counters),
                                                         ctypes.sizeof(counters), None),
                       'QueryInformationJobObject')
            if counters.active == 0:
                self._close()
                return
            if monotonic() >= deadline:
                raise ProcessScopeError('Owned Windows job did not terminate within the cleanup budget')
            sleep(min(.01, max(0, deadline - monotonic())))
