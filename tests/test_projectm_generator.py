"""Offline generator-selection checks: no Visual Studio, CMake or downloads needed."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "select_projectm_generator.py"
SPEC = spec_from_file_location("select_projectm_generator", SOURCE)
SELECTOR = module_from_spec(SPEC)
SPEC.loader.exec_module(SELECTOR)

HELP_MODERN = """Generators
The following generators are available on this platform (* marks default):
  Visual Studio 18 2026        = Generates Visual Studio 2026 project files.
  Visual Studio 17 2022        = Generates Visual Studio 2022 project files.
* Ninja                        = Generates build.ninja files.
"""
HELP_LEGACY = """Generators
  Visual Studio 17 2022        = Generates Visual Studio 2022 project files.
  Ninja                        = Generates build.ninja files.
"""


def vs(version="18.0.3"):
    return {"installationVersion": version, "installationPath": "C:\\VS\\2026"}


def test_queries_cmake_actual_supported_generators():
    assert SELECTOR.available_generators(HELP_MODERN) == {
        "Visual Studio 18 2026", "Visual Studio 17 2022", "Ninja"
    }
    assert "Visual Studio 18 2026" not in SELECTOR.available_generators(HELP_LEGACY)


def test_vs2026_never_misidentified_as_vs2022():
    assert SELECTOR.select_generator(HELP_MODERN, [vs()], compiler_ready=False) == "Visual Studio 18 2026"


def test_visual_studio_2022_stays_supported():
    assert SELECTOR.select_generator(HELP_MODERN, [vs("17.12.0")], compiler_ready=False) == (
        "Visual Studio 17 2022"
    )


def test_picks_matching_installed_version_when_more_than_one():
    assert SELECTOR.select_generator(HELP_LEGACY, [vs("18.0.1"), vs("17.14.2")],
                                     compiler_ready=False) == "Visual Studio 17 2022"


def test_old_cmake_with_only_vs2026_reports_upgrade_not_wrong_generator():
    with pytest.raises(SELECTOR.ToolchainError, match="CMake 4.2"):
        SELECTOR.select_generator(HELP_LEGACY, [vs()], compiler_ready=False)


def test_ninja_needs_real_compiler_environment():
    which = lambda x: {"gcc.exe": "gcc.exe", "g++.exe": "g++.exe"}.get(x)
    assert SELECTOR.ninja_compiler_ready(which, env={})
    assert not SELECTOR.ninja_compiler_ready(lambda x: "cl.exe" if x == "cl.exe" else None,
                                             env={})
    assert SELECTOR.select_generator(HELP_MODERN, [], compiler_ready=True) == "Ninja"
    with pytest.raises(SELECTOR.ToolchainError, match="No Visual Studio"):
        SELECTOR.select_generator(HELP_MODERN, [], compiler_ready=False)


def test_installed_vs_metadata_requires_real_version_and_path():
    assert SELECTOR.visual_studio_versions([vs("18.0.3"), vs("17.5.0"),
        {"installationVersion": "18.1.2", "installationPath": ""},
        {"installationPath": "C:\\Invalid"}, "nonsense"]) == [18, 17]
    with pytest.raises(SELECTOR.ToolchainError, match="JSON installation list"):
        SELECTOR.visual_studio_versions({"installationVersion": "18.0.1"})


def test_old_failed_generator_cache_is_archived_without_deleting(tmp_path):
    runtime = tmp_path / "models" / "runtimes" / "projectm"
    build = runtime / "build"
    build.mkdir(parents=True)
    (build / "CMakeCache.txt").write_text(
        "// old configure\nCMAKE_GENERATOR:INTERNAL=Visual Studio 17 2022\n", encoding="utf-8")
    (build / "log.txt").write_text("retain failure evidence", encoding="utf-8")
    source = runtime / "source"
    source.mkdir()
    (source / "commit.txt").write_text("pinned source", encoding="utf-8")
    models = runtime.parent.parent / "clip"
    models.mkdir()
    (models / "weight.bin").write_bytes(b"preserved download")
    archive = SELECTOR.archive_incompatible_build(runtime, "Visual Studio 18 2026")
    assert archive is not None and archive.is_dir() and archive.name.startswith("build-previous-")
    assert (archive / "log.txt").read_text() == "retain failure evidence"
    assert not build.exists()
    assert (source / "commit.txt").read_text() == "pinned source"
    assert (models / "weight.bin").read_bytes() == b"preserved download"


def test_matching_generator_cache_is_preserved(tmp_path):
    runtime = tmp_path / "projectm"
    build = runtime / "build"
    build.mkdir(parents=True)
    cache = build / "CMakeCache.txt"
    cache.write_text("CMAKE_GENERATOR:INTERNAL=Visual Studio 18 2026\n")
    assert SELECTOR.archive_incompatible_build(runtime, "Visual Studio 18 2026") is None
    assert cache.is_file()


def test_symlinked_build_folder_is_refused(tmp_path):
    target = tmp_path / "external"
    target.mkdir()
    link = tmp_path / "build"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Unprivileged Windows CI hosts cannot always create symbolic links;
        # the path-safety property is covered whenever the operation is available.
        assert not link.is_symlink()
        return
    with pytest.raises(SELECTOR.ToolchainError, match="symlinked"):
        SELECTOR.archive_incompatible_build(tmp_path, "Ninja")


def test_main_emits_only_one_generator_argument(tmp_path, monkeypatch, capsys):
    module = SELECTOR
    cmake = tmp_path / "cmake.exe"
    cmake.write_bytes(b"dummy")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=HELP_MODERN, stderr=""))
    monkeypatch.setattr(module, "discovered_instances", lambda *_: [vs()])
    monkeypatch.setattr(module, "vswhere_location", lambda: Path("C:/vswhere.exe"))
    monkeypatch.setattr(module, "ninja_compiler_ready", lambda: False)
    assert module.main(["--cmake", str(cmake), "--runtime", str(tmp_path / "projectm")]) == 0
    output = capsys.readouterr()
    assert output.out == "-GVisual Studio 18 2026\n"
    assert output.err == ""


def test_launcher_calls_generator_selector_and_does_not_hardcode_vs2022():
    text = (SOURCE.parent / "Run-FullPresetAnalysis.ps1").read_text(encoding="utf-8")
    assert "select_projectm_generator.py" in text
    assert "('--cmake-arg=' + $cmakeGenerator)" in text
    assert "if ($vsInstall) { $cmakeGenerator = '-GVisual Studio 17 2022' }" not in text
