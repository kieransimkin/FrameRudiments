"""Keep Windows pytest runs away from stale per-user temp ACLs.

A broken %TEMP%/pytest-of-<username> directory can cause hundreds of
unrelated tmp_path fixtures to fail with WinError 5. Never delete or chmod
that shared user directory: allocate a unique, owned base for each run.
"""

import os
from pathlib import Path
import tempfile


def _assign_windows_basetemp(config, local_app_data):
    """Set a fresh per-invocation base, unless the caller explicitly set one.

    The chosen directory is *not* created here. pytest owns it and can remove
    it safely when initializing its temp-path factory. Distinct UUID-like
    names from mkdtemp prevent collisions between parallel pytest processes.
    """
    if config.getoption("basetemp", default=None):
        return None
    root = Path(local_app_data) / "FrameRudiments" / "pytest-tmp"
    root.mkdir(parents=True, exist_ok=True)
    # mkdtemp atomically reserves a name; pytest clears/recreates only this
    # newly reserved subdirectory, never its parent or another process's run.
    base = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    config.option.basetemp = str(base)
    return base


def pytest_configure(config):
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            _assign_windows_basetemp(config, local_app_data)
