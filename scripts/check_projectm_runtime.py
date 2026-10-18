"""Preflight the pinned projectM DLL before starting a long analysis sweep.

The loader's search roots are confined to the already installed, project-local
projectM runtime. The DLL is not copied, replaced or downloaded. The preflight
runs in a disposable Python process so it never shares an OpenGL context.
"""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import sys


def runtime_dll_directories(install_root: Path, library: Path) -> list[Path]:
    """Search only the model-local install and directories containing its DLLs."""
    root = install_root.resolve(strict=True)
    dll = library.resolve(strict=True)
    if not dll.is_file() or not dll.is_relative_to(root):
        raise ValueError("projectM library must reside in its own install directory")
    dirs = {dll.parent}
    for candidate in root.rglob("*.dll"):
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.is_relative_to(root):
            dirs.add(resolved.parent)
    return [dll.parent, *sorted(dirs - {dll.parent})]


def load_projectm(library: Path, install_root: Path, *, add_directory=None, loader=None):
    """Check DLL loading with Windows' safe per-process dependency search paths."""
    dll = library.resolve(strict=True)
    directories = runtime_dll_directories(install_root, dll)
    if add_directory is None:
        add_directory = getattr(os, "add_dll_directory", None)
    if loader is None:
        loader = ctypes.CDLL
    handles = []
    try:
        if os.name == "nt" and add_directory is None:
            raise RuntimeError("Python cannot register Windows DLL search directories")
        if add_directory is not None:
            for directory in directories:
                handles.append(add_directory(str(directory)))
        try:
            library_handle = loader(str(dll))
            return library_handle, directories
        except OSError as exc:
            raise RuntimeError(
                "projectM DLL exists but Windows cannot load it or a dependent DLL. "
                "Check that the Visual C++ Redistributable for your compiler is installed, "
                "and inspect imports with 'dumpbin /DEPENDENTS <projectM DLL>' or "
                "Dependencies.exe. The pinned projectM source and model weights are untouched. "
                f"Checked {len(directories)} project-local DLL directories: "
                + ", ".join(str(path) for path in directories)
            ) from exc
    finally:
        for handle in reversed(handles):
            close = getattr(handle, "close", None)
            if callable(close):
                close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        _, folders = load_projectm(arguments.library, arguments.install_root)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"projectM preflight FAILED: {exc}", file=sys.stderr)
        return 2
    print(f"projectM DLL load passed; {len(folders)} project-local DLL search directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
