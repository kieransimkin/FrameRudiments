"""Synthetic repair tests: no native PE fixtures are executed."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

from test_projectm_dll_diagnostics import image, installed

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("repair_projectm_mingw", SCRIPTS / "repair_projectm_mingw.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NAMES = ["libgcc_s_seh-1.dll", "libwinpthread-1.dll", "libstdc++-6.dll"]


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "My Songs" / "FrameRudiments"
    runtime = repo / "models/runtimes/projectm"
    runtime.mkdir(parents=True)
    (repo / "pyproject.toml").write_text('[project]\nname = "framerudiments"\nversion = "0.4.0"\n')
    install, dll, exe = installed(runtime, NAMES)
    compiler = tmp_path / "Compiler with spaces" / "bin/g++.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(image())
    for name in NAMES:
        deps = ["KERNEL32.dll"]
        if name == "libstdc++-6.dll":
            deps += NAMES[:2]
        (compiler.parent / name).write_bytes(image(deps))
    build = runtime / "build"
    (build / "CMakeFiles/4.2.0").mkdir(parents=True)
    (build / "CMakeCache.txt").write_text(f"CMAKE_CXX_COMPILER:FILEPATH={compiler}\n")
    (build / "CMakeFiles/4.2.0/CMakeCXXCompiler.cmake").write_text(
        f'set(CMAKE_CXX_COMPILER "{compiler}")\nset(CMAKE_CXX_COMPILER_ID "GNU")\n'
        'set(CMAKE_CXX_COMPILER_VERSION "14.2.0")\n')
    (build / "libprojectM-4.dll").write_bytes(dll.read_bytes())
    (build / "install_manifest.txt").write_text(str(dll) + "\n")
    work = runtime / "diagnostics/test"
    work.mkdir(parents=True)
    info = {"path": str(compiler), "prefix": str(compiler.parent.parent), "recorded_version": "14.2.0"}
    return repo, runtime, install, dll, compiler, build, work, info


def query(compiler, args):
    if args == ["-dumpmachine"]:
        return "x86_64-w64-mingw32"
    if args == ["-dumpfullversion", "-dumpversion"]:
        return "14.2.0"
    return str(compiler.parent / args[0].split("=", 1)[1])


def plan_for(setup):
    return m.make_plan(setup[3], setup[2], setup[-1], query=query)


def good_probe(plan_file, extra_dirs, report_file):
    plan = json.loads(plan_file.read_text())
    for row in plan["files"]:
        path = (Path(extra_dirs[0]) / row["name"] if extra_dirs and row["action"] == "copy"
                else Path(row["destination"]))
        assert path.is_file() and m.checksum(path) == row["sha256"]
    return {"state": "loadable", "version": [4, 2, 0]}


def test_library_receipt_and_repo_with_spaces(setup):
    assert m.library_for(setup[0]) == (setup[1], setup[3])


def test_changed_main_dll_refused(setup):
    setup[3].write_bytes(image(NAMES + ["USER32.dll"]))
    with pytest.raises(m.RepairError, match="hash"):
        m.library_for(setup[0])


def test_foreign_repository_refused(setup):
    (setup[0] / "pyproject.toml").write_text('[project]\nname = "another-project"\n')
    with pytest.raises(m.RepairError, match="not a FrameRudiments"):
        m.library_for(setup[0])


def test_exact_recorded_compiler_selected(setup):
    result = m.compiler_for(setup[1], setup[3])
    assert result["path"] == str(setup[4])
    assert result["binding"] == "byte-matched build DLL"
    assert result["recorded_version"] == "14.2.0"


def test_archived_build_supported(setup):
    setup[5].rename(setup[1] / "build-previous-old")
    assert "build-previous-old" in m.compiler_for(setup[1], setup[3])["cache"]


def test_active_manifest_fallback_when_build_output_removed(setup):
    (setup[5] / "libprojectM-4.dll").unlink()
    assert m.compiler_for(setup[1], setup[3])["binding"] == "active install manifest"


def test_archived_manifest_alone_insufficient(setup):
    (setup[5] / "libprojectM-4.dll").unlink()
    setup[5].rename(setup[1] / "build-previous-old")
    with pytest.raises(m.RepairError, match="uniquely"):
        m.compiler_for(setup[1], setup[3])


def test_missing_build_metadata_refused(setup):
    (setup[5] / "CMakeCache.txt").unlink()
    with pytest.raises(m.RepairError, match="uniquely"):
        m.compiler_for(setup[1], setup[3])


def test_msvc_metadata_not_used_for_mingw(setup):
    file = setup[5] / "CMakeFiles/4.2.0/CMakeCXXCompiler.cmake"
    file.write_text(file.read_text().replace('"GNU"', '"MSVC"'))
    with pytest.raises(m.RepairError, match="uniquely"):
        m.compiler_for(setup[1], setup[3])


def test_ambiguous_compiler_never_guessed(setup):
    other = setup[1] / "build-previous-other"
    shutil.copytree(setup[5], other)
    for p in [other / "CMakeCache.txt", other / "CMakeFiles/4.2.0/CMakeCXXCompiler.cmake"]:
        p.write_text(p.read_text().replace(str(setup[4]), str(setup[4].with_name("gcc.exe"))))
    with pytest.raises(m.RepairError, match="uniquely"):
        m.compiler_for(setup[1], setup[3])


def test_compiler_query_version_target_and_hash(setup):
    result = m.checked_compiler(setup[-1], 0x8664, query=query)
    assert result["target"] == "x86_64-w64-mingw32"
    assert result["executable_sha256"] == m.checksum(setup[4])


@pytest.mark.parametrize("bad_target", ["i686-w64-mingw32", "x86_64-linux-gnu", "x86_64-pc-cygwin"])
def test_compiler_target_mismatch(setup, bad_target):
    def wrong(c, a):
        return bad_target if a == ["-dumpmachine"] else query(c, a)
    with pytest.raises(m.RepairError, match="target"):
        m.checked_compiler(setup[-1], 0x8664, query=wrong)


def test_compiler_changed_since_build_refused(setup):
    info = dict(setup[-1], recorded_version="13.2.0")
    with pytest.raises(m.RepairError, match="changed since"):
        m.checked_compiler(info, 0x8664, query=query)


def test_three_exact_missing_runtime_files_planned(setup):
    plan = plan_for(setup)
    assert {r["name"] for r in plan["files"]} == set(NAMES)
    assert all(r["action"] == "copy" for r in plan["files"])
    assert all(Path(r["destination"]).parent == setup[3].parent for r in plan["files"])


def test_only_compiler_bin_fallback_when_gcc_returns_name(setup):
    assert m.find_runtime(NAMES[0], setup[-1], query=lambda c, a: NAMES[0]) == setup[4].parent / NAMES[0]


def test_own_toolchain_lib_subdirectory_supported(setup):
    path = setup[4].parent.parent / "lib" / NAMES[0]
    path.parent.mkdir()
    (setup[4].parent / NAMES[0]).rename(path)
    assert m.find_runtime(NAMES[0], setup[-1], query=lambda c, a: str(path)) == path


def test_external_runtime_result_rejected(setup, tmp_path):
    path = tmp_path / NAMES[0]
    path.write_bytes(image())
    with pytest.raises(m.RepairError, match="outside"):
        m.find_runtime(NAMES[0], setup[-1], query=lambda c, a: str(path))


def test_missing_runtime_no_arbitrary_fallback(setup):
    (setup[4].parent / NAMES[0]).unlink()
    with pytest.raises(m.RepairError, match="absent"):
        plan_for(setup)


def test_wrong_runtime_architecture_refused(setup):
    (setup[4].parent / NAMES[0]).write_bytes(image(machine=0x014C))
    with pytest.raises(m.RepairError, match="architecture"):
        plan_for(setup)


@pytest.mark.parametrize("name", ["kernel32.dll", "vcruntime140.dll", "../evil.dll", "evil.dll"])
def test_system_and_unsafe_names_never_copied(setup, name):
    with pytest.raises(m.RepairError, match="allowed"):
        m.find_runtime(name, setup[-1], query=query)


def test_transitive_runtime_dependency_is_included(setup):
    (setup[4].parent / NAMES[0]).write_bytes(image(["libssp-0.dll"]))
    (setup[4].parent / "libssp-0.dll").write_bytes(image(["KERNEL32.dll"]))
    assert {r["name"] for r in plan_for(setup)["files"]} == set(NAMES + ["libssp-0.dll"])


def test_dependency_cycles_are_bounded(setup):
    (setup[4].parent / NAMES[0]).write_bytes(image([NAMES[2]]))
    assert len(plan_for(setup)["files"]) == 3


def test_existing_identical_dll_is_preserved(setup):
    shutil.copyfile(setup[4].parent / NAMES[0], setup[3].parent / NAMES[0])
    plan = plan_for(setup)
    assert next(r for r in plan["files"] if r["name"] == NAMES[0])["action"] == "preserve"
    result = m.apply_plan(plan, setup[6], probe=good_probe)
    assert result["state"] == "verified" and len(result["copied"]) == 2


def test_existing_different_dll_is_not_overwritten(setup):
    dest = setup[3].parent / NAMES[0]
    old = image(["USER32.dll"])
    dest.write_bytes(old)
    with pytest.raises(m.RepairError, match="Refusing to overwrite"):
        plan_for(setup)
    assert dest.read_bytes() == old


def test_staged_then_fresh_installed_checks(setup):
    plan, calls = plan_for(setup), []
    original = setup[3].read_bytes()
    receipt = (setup[3].parent / "framerudiments-projectm.json").read_bytes()
    def probe(p, extra, report):
        calls.append(list(extra))
        return good_probe(p, extra, report)
    result = m.apply_plan(plan, setup[6], probe=probe)
    assert result["state"] == "verified" and len(result["copied"]) == 3
    assert calls[0] and calls[1] == []
    assert setup[3].read_bytes() == original
    assert (setup[3].parent / "framerudiments-projectm.json").read_bytes() == receipt


def test_staged_check_failure_copies_nothing(setup):
    def failed(*args):
        raise m.RepairError("simulated staged native failure")
    with pytest.raises(m.RepairError, match="staged"):
        m.apply_plan(plan_for(setup), setup[6], probe=failed)
    assert all(not (setup[3].parent / name).exists() for name in NAMES)


def test_final_native_failure_rolls_back_added_files(setup):
    def failed(p, extra, r):
        if not extra:
            raise m.RepairError("simulated final native failure")
        return good_probe(p, extra, r)
    with pytest.raises(m.RepairError, match="final"):
        m.apply_plan(plan_for(setup), setup[6], probe=failed)
    assert all(not (setup[3].parent / name).exists() for name in NAMES)
    assert len(json.loads((setup[6] / "rollback.json").read_text())["files"]) == 3


def test_rollback_preserves_original_dlls(setup):
    present = setup[3].parent / NAMES[0]
    shutil.copyfile(setup[4].parent / NAMES[0], present)
    original = present.read_bytes()
    def failed(p, extra, r):
        if not extra:
            raise m.RepairError("simulated native failure")
        return good_probe(p, extra, r)
    with pytest.raises(m.RepairError):
        m.apply_plan(plan_for(setup), setup[6], probe=failed)
    assert present.read_bytes() == original


def test_runtime_changes_after_planning_rejected(setup):
    plan = plan_for(setup)
    (setup[4].parent / NAMES[0]).write_bytes(image(["USER32.dll"]))
    with pytest.raises(m.RepairError, match="changed"):
        m.apply_plan(plan, setup[6], probe=good_probe)
    assert not (setup[3].parent / NAMES[0]).exists()


def test_concurrent_target_creation_is_never_overwritten(setup):
    concurrent = setup[3].parent / NAMES[1]
    def probe(p, extra, r):
        good_probe(p, extra, r)
        concurrent.write_bytes(b"concurrent writer")
        return {"state": "loadable"}
    with pytest.raises(FileExistsError):
        m.apply_plan(plan_for(setup), setup[6], probe=probe)
    assert concurrent.read_bytes() == b"concurrent writer"
    assert not (setup[3].parent / NAMES[0]).exists()


def test_repeat_run_has_nothing_to_copy(setup):
    m.apply_plan(plan_for(setup), setup[6], probe=good_probe)
    again = setup[6].with_name("second")
    again.mkdir()
    assert not m.apply_plan(plan_for(setup), again, probe=good_probe)["copied"]


def test_native_probe_on_linux_does_not_claim_success(setup):
    if sys.platform == "win32":
        return
    result = m.native_probe(plan_for(setup), [])
    assert result["state"] == "load-failed" and "Windows" in result["error"]


def test_subprocess_probe_nonzero_refuses_stale_success(tmp_path, monkeypatch):
    report = tmp_path / "result.json"
    report.write_text('{"state": "loadable"}')
    from types import SimpleNamespace
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="crashed"))
    with pytest.raises(m.RepairError, match="failed"):
        m.subprocess_probe(tmp_path / "plan.json", [], report)


def test_manifest_does_not_override_contradictory_build_bytes(setup):
    (setup[5] / "libprojectM-4.dll").write_bytes(image(["USER32.dll"]))
    with pytest.raises(m.RepairError, match="uniquely"):
        m.compiler_for(setup[1], setup[3])


def test_delay_only_dependency_chain_stays_optional(setup):
    setup[3].write_bytes(image(delay=[NAMES[2]]))
    (setup[4].parent / NAMES[2]).write_bytes(image(NAMES[:2]))
    assert all(row["kind"] == "delay" for row in plan_for(setup)["files"])


def test_required_path_upgrades_previously_delayed_runtime(setup):
    setup[3].write_bytes(image([NAMES[2]], delay=[NAMES[0]]))
    (setup[4].parent / NAMES[2]).write_bytes(image([NAMES[0]]))
    assert all(row["kind"] == "required" for row in plan_for(setup)["files"])


def mocked_windows(setup, monkeypatch, *, version=(4, 2, 0), wrong_location=False, missing_api=False):
    """Simulate API protocol only; do not imply Windows loader execution."""
    import ctypes
    from types import SimpleNamespace
    from test_projectm_dll_diagnostics import fake_api
    plan = plan_for(setup)
    for row in plan["files"]:
        shutil.copyfile(row["source"], row["destination"])
    native = fake_api(version)
    for name in ("projectm_create", "projectm_destroy", "projectm_pcm_add_float", "projectm_opengl_render_frame_fbo"):
        setattr(native, name, lambda: None)
    if not missing_api:
        native.projectm_set_frame_time = lambda: None
    names = [r["name"] for r in plan["files"]]
    def get_handle(name):
        return names.index(name) + 1
    def get_name(handle, buffer, count):
        row = plan["files"][handle - 1]
        buffer.value = row["source"] if wrong_location else row["destination"]
        return len(buffer.value)
    kernel = SimpleNamespace(GetModuleHandleW=get_handle, GetModuleFileNameW=get_name)
    fake_ctypes = SimpleNamespace(**vars(ctypes))
    fake_ctypes.CDLL = lambda *a, **k: native
    fake_ctypes.WinDLL = lambda *a, **k: kernel
    closed = []
    fake_os = SimpleNamespace(name="nt", add_dll_directory=lambda path: SimpleNamespace(close=lambda: closed.append(path)))
    monkeypatch.setattr(m, "ctypes", fake_ctypes)
    monkeypatch.setattr(m, "os", fake_os)
    monkeypatch.setattr(m, "sys", SimpleNamespace(executable=str(setup[1] / "python.exe")))
    return plan, closed


def test_native_protocol_verifies_version_and_loaded_paths(setup, monkeypatch):
    plan, closed = mocked_windows(setup, monkeypatch)
    result = m.native_probe(plan, [])
    assert result["state"] == "loadable" and len(result["loaded_runtimes"]) == 3
    assert closed


def test_native_protocol_wrong_api_version_rejected(setup, monkeypatch):
    plan, closed = mocked_windows(setup, monkeypatch, version=(4, 1, 0))
    result = m.native_probe(plan, [])
    assert result["state"] == "load-failed" and "version" in result["error"]
    assert closed


def test_native_protocol_missing_api_rejected(setup, monkeypatch):
    plan, _ = mocked_windows(setup, monkeypatch, missing_api=True)
    assert m.native_probe(plan, [])["state"] == "load-failed"


def test_native_protocol_unrelated_loaded_dll_rejected(setup, monkeypatch):
    plan, _ = mocked_windows(setup, monkeypatch, wrong_location=True)
    result = m.native_probe(plan, [])
    assert result["state"] == "load-failed" and "unexpected copy" in result["error"]


def test_native_protocol_changed_main_dll_rejected(setup, monkeypatch):
    plan, _ = mocked_windows(setup, monkeypatch)
    setup[3].write_bytes(image())
    result = m.native_probe(plan, [])
    assert result["state"] == "load-failed" and "changed" in result["error"]
