"""Windows native projectM dependency preflight; no GPU or download required."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "check_projectm_runtime.py"
SPEC = importlib.util.spec_from_file_location("check_projectm_runtime", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_preflight_never_escapes_pinned_install_directory(tmp_path):
    install = tmp_path / "projectm" / "install"
    other = tmp_path / "other"
    install.mkdir(parents=True)
    other.mkdir()
    dll = other / "libprojectM-4.dll"
    dll.write_bytes(b"external")
    with pytest.raises(ValueError, match="own install directory"):
        MODULE.runtime_dll_directories(install, dll)


def test_preflight_includes_sibling_dependency_folders_and_closes_handles(tmp_path):
    install = tmp_path / "projectm" / "install"
    main = install / "bin" / "libprojectM-4.dll"
    dep = install / "lib" / "projectm-eval.dll"
    for file in (main, dep):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"fixture")
    opened, closed, loaded = [], [], []
    def register(path):
        opened.append(path)
        return SimpleNamespace(close=lambda: closed.append(path))
    def load(path):
        loaded.append(path)
        return object()
    _, search = MODULE.load_projectm(main, install, add_directory=register, loader=load)
    assert search[0] == main.parent.resolve()
    assert dep.parent.resolve() in search
    assert loaded == [str(main.resolve())]
    assert len(opened) == len(closed) == 2


def test_preflight_load_failure_explains_native_dependency_issue(tmp_path):
    folder = tmp_path / "install"
    folder.mkdir()
    dll = folder / "libprojectM-4.dll"
    dll.write_bytes(b"fixture")
    def fail(_):
        raise OSError("Missing dependent DLL")
    with pytest.raises(RuntimeError, match="Redistributable.*dumpbin"):
        MODULE.load_projectm(dll, folder, add_directory=lambda _: SimpleNamespace(close=lambda: None),
                             loader=fail)


def test_launcher_preflights_both_regular_and_skip_setup_paths():
    launcher = (SOURCE.parent / "Run-FullPresetAnalysis.ps1").read_text(encoding="utf-8")
    assert "check_projectm_runtime.py" in launcher
    assert launcher.index("check_projectm_runtime.py") < launcher.index("if ($InstallOnly)")
    assert "FRAMERUDIMENTS_PROJECTM_DLL_DIRS" in launcher
    assert "Get-ChildItem -Path $projectmInstall -Filter '*.dll'" in launcher
