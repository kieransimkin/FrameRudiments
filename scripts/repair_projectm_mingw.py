"""Repair missing projectM MinGW runtime DLLs from its recorded local compiler.

No downloads, global PATH changes, elevation, rebuild, or replacement of existing
files. Runtime candidates are tied to a matching projectM build and checked
before copying. A fresh render-Python process must load the installed library.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import uuid

from projectm_dll_diagnostics import PROJECTM_COMMIT, read_pe, runtime_files

SCHEMA = "framerudiments.projectm-mingw-repair.v1"
RUNTIME_NAME = re.compile(
    r"(?:libgcc_s_(?:seh|sjlj|dw2)-1|libstdc\+\+-6|libwinpthread-1|"
    r"libssp-0|libquadmath-0|libgomp-1|libatomic-1)\.dll", re.I
)
PINS = {0x8664: "x86_64", 0x014C: "i686", 0xAA64: "aarch64"}


class RepairError(RuntimeError):
    """Stop without guessing a compiler, replacing files, or claiming success."""


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def read_text(path: Path) -> str:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise RepairError(f"Build metadata exceeds 8 MiB: {path}")
    return path.read_text(encoding="utf-8-sig", errors="strict")


def cache_values(path: Path) -> dict[str, str]:
    values = {}
    for line in read_text(path).splitlines():
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*):[^=]+=(.*)", line)
        if match:
            values[match[1]] = match[2].strip().strip('"')
    return values


def library_for(repository: Path) -> tuple[Path, Path]:
    root = repository.resolve(strict=True)
    project = tomllib.loads(read_text(root / "pyproject.toml"))
    if project.get("project", {}).get("name", "").casefold() != "framerudiments":
        raise RepairError("--repository is not a FrameRudiments checkout")
    models = (root / "models").resolve(strict=True)
    runtime = (models / "runtimes/projectm").resolve(strict=True)
    install = (runtime / "install").resolve(strict=True)
    if not models.is_relative_to(root) or models == root or not install.is_relative_to(models):
        raise RepairError("Runtime/model directories must remain inside the FrameRudiments checkout")
    candidates = []
    for path in install.rglob("*"):
        if path.name.casefold() in {"libprojectm-4.dll", "projectm-4.dll"} and path.is_file():
            library = path.resolve(strict=True)
            if not library.is_relative_to(install):
                raise RepairError("The projectM DLL resolves outside its install directory")
            read_pe(library)
            receipt = json.loads(read_text(library.parent / "framerudiments-projectm.json"))
            if receipt.get("source_commit") != PROJECTM_COMMIT or receipt.get("library_sha256") != checksum(library):
                raise RepairError("projectM source pin or library hash does not match its existing receipt")
            candidates.append(library)
    if len(candidates) != 1:
        raise RepairError(f"Expected one verified projectM DLL, found {len(candidates)}")
    return runtime, candidates[0]


def compiler_for(runtime: Path, library: Path) -> dict:
    """Prefer byte-matched build artefacts, including archived generator builds.

    If the original build output was removed, accept only the current build's
    explicit install manifest. Competing compiler paths are never guessed.
    """
    candidates, notes = [], []
    builds = [runtime / "build", *sorted(runtime.glob("build-previous-*"))]
    wanted = checksum(library)
    for build in builds:
        cache = build / "CMakeCache.txt"
        if not cache.is_file():
            continue
        build = build.resolve(strict=True)
        if not build.is_relative_to(runtime.resolve(strict=True)):
            raise RepairError("A build directory escapes the local projectM runtime")
        values = cache_values(cache)
        raw = values.get("CMAKE_CXX_COMPILER", "")
        if not raw:
            continue
        compiler = Path(raw)
        if not compiler.is_absolute():
            notes.append(f"{cache}: compiler path is not absolute")
            continue
        metadata = []
        for path in (build / "CMakeFiles").glob("*/CMakeCXXCompiler.cmake"):
            # Read metadata as text; never execute CMake scripts from the cache.
            pairs = dict(re.findall(r'^set\((CMAKE_CXX_COMPILER(?:_ID|_VERSION)?)\s+"([^"\r\n]*)"\)',
                                    read_text(path), re.M))
            if pairs.get("CMAKE_CXX_COMPILER") == raw:
                metadata.append(pairs)
        ids = {m.get("CMAKE_CXX_COMPILER_ID") for m in metadata}
        versions = {m.get("CMAKE_CXX_COMPILER_VERSION") for m in metadata}
        if ids != {"GNU"} or len(versions) != 1 or None in versions:
            notes.append(f"{cache}: not one recorded GNU compiler/version")
            continue
        matching = []
        saw_build_dll = False
        for index, path in enumerate(build.rglob("*")):
            if index > 100000:
                raise RepairError("Build tree exceeds the bounded discovery limit")
            if path.name.casefold() == library.name.casefold() and path.is_file():
                saw_build_dll = True
                path = path.resolve(strict=True)
                if path.is_relative_to(build) and path.stat().st_size == library.stat().st_size and checksum(path) == wanted:
                    matching.append(str(path))
        manifest = build / "install_manifest.txt"
        listed = (manifest.is_file() and any(
            Path(line.strip()).resolve() == library for line in read_text(manifest).splitlines() if line.strip()
        ))
        if not matching and not (not saw_build_dll and build == (runtime / "build").resolve() and listed):
            notes.append(f"{cache}: no matching built DLL or active install manifest")
            continue
        candidates.append({"path": str(compiler), "recorded_version": next(iter(versions)),
                           "cache": str(cache), "cache_sha256": checksum(cache),
                           "matching_build_outputs": matching,
                           "binding": "byte-matched build DLL" if matching else "active install manifest"})
    exact = [r for r in candidates if r["matching_build_outputs"]]
    candidates = exact or candidates
    identities = {(r["path"], r["recorded_version"]) for r in candidates}
    if len(identities) != 1:
        raise RepairError("Cannot uniquely identify the GNU compiler for the installed DLL. "
                          "No runtime files were copied. " + "; ".join(notes)[-2500:])
    result = candidates[0]
    compiler = Path(result["path"]).resolve(strict=True)
    if not compiler.is_file() or compiler.suffix.casefold() != ".exe" or compiler.parent.name.casefold() != "bin":
        raise RepairError("The recorded compiler must exist as an executable in its toolchain's bin directory")
    result["path"] = str(compiler)
    result["prefix"] = str(compiler.parent.parent)
    return result


def query_compiler(compiler: Path, arguments: list[str]) -> str:
    env = dict(os.environ)
    for key in ("GCC_EXEC_PREFIX", "COMPILER_PATH", "LIBRARY_PATH", "CPATH", "CPLUS_INCLUDE_PATH"):
        env.pop(key, None)
    result = subprocess.run([str(compiler), *arguments], cwd=compiler.parent, env=env,
                            capture_output=True, text=True, errors="replace", timeout=30, check=False)
    lines = result.stdout.strip().splitlines()
    if result.returncode or len(lines) != 1:
        raise RepairError(f"Recorded compiler query failed ({result.returncode}): {arguments}; {result.stderr[-600:]}")
    return lines[0].strip()


def checked_compiler(info: dict, machine: int, *, query=query_compiler) -> dict:
    compiler = Path(info["path"])
    target = query(compiler, ["-dumpmachine"])
    version = query(compiler, ["-dumpfullversion", "-dumpversion"])
    if machine not in PINS or not target.startswith(PINS[machine] + "-") or "mingw" not in target.casefold():
        raise RepairError(f"Recorded compiler target {target!r} does not match projectM's PE architecture")
    if version != info["recorded_version"]:
        raise RepairError(f"Compiler changed since the build ({info['recorded_version']} -> {version}). "
                          "Restore the original toolchain or rebuild projectM; do not mix runtime DLLs.")
    return dict(info, target=target, version=version, executable_sha256=checksum(compiler))


def find_runtime(name: str, info: dict, *, query=query_compiler) -> Path:
    if not RUNTIME_NAME.fullmatch(name):
        raise RepairError(f"Not an allowed GNU runtime name: {name}")
    compiler, prefix = Path(info["path"]), Path(info["prefix"]).resolve(strict=True)
    result = query(compiler, ["-print-file-name=" + name])
    candidates = []
    if result.casefold() != name.casefold():
        candidate = Path(result)
        if not candidate.is_absolute():
            candidate = compiler.parent / candidate
        candidates.append(candidate)
    # GCC distributions do not all return DLLs for -print-file-name. The only
    # fallback is the recorded compiler's own bin, never arbitrary PATH entries.
    candidates.extend(p for p in compiler.parent.iterdir() if p.name.casefold() == name.casefold())
    found = []
    for path in candidates:
        if not path.is_file():
            continue
        path = path.resolve(strict=True)
        if not path.is_relative_to(prefix):
            raise RepairError(f"Compiler runtime resolves outside its own installation: {name}")
        if path.name.casefold() != name.casefold():
            raise RepairError(f"Compiler returned a differently named runtime: {name}")
        read_pe(path)
        if path not in found:
            found.append(path)
    if not found:
        raise RepairError(f"{name} is absent from the recorded compiler installation: {compiler.parent}. "
                          "No unrelated DLL downloads or PATH search will be attempted.")
    if len({checksum(path) for path in found}) != 1:
        raise RepairError(f"Conflicting copies of {name} exist within the recorded compiler installation")
    return found[0]


def make_plan(library: Path, install: Path, info: dict, *, query=query_compiler) -> dict:
    metadata = read_pe(library)
    _, installed = runtime_files(install, library)
    queue, visited, rows = [(library, "required")], set(), []
    while queue:
        parent, inherited_kind = queue.pop(0)
        if (parent, inherited_kind) in visited:
            continue
        visited.add((parent, inherited_kind))
        if len(visited) > 64:
            raise RepairError("Dependency traversal exceeds 64 modules")
        for imported in read_pe(parent)["imports"]:
            name = imported["name"]
            if not RUNTIME_NAME.fullmatch(name):
                continue  # OS and non-GNU dependencies are tested by the real loader, never copied.
            kind = "delay" if inherited_kind == "delay" else imported["kind"]
            existing = next((r for r in rows if r["name"].casefold() == name.casefold()), None)
            if existing is not None:
                if existing["kind"] == "delay" and kind == "required":
                    existing["kind"] = "required"
                    queue.append((Path(existing["source"]), "required"))
                continue
            source = find_runtime(name, info, query=query)
            child = read_pe(source)
            if child["machine"] != metadata["machine"]:
                raise RepairError(f"Wrong architecture for {name}: {child['architecture']}")
            expected = checksum(source)
            current = installed.get(name.casefold())
            if current is not None and checksum(current) != expected:
                raise RepairError(f"Existing {name} differs from the recorded compiler. Refusing to overwrite it.")
            target = current or library.parent / name
            rows.append({"name": name, "source": str(source), "destination": str(target),
                         "sha256": expected, "architecture": child["architecture"],
                         "kind": kind, "action": "preserve" if current else "copy"})
            queue.append((source, kind))
    if not rows:
        raise RepairError("No supported MinGW runtime imports found; this is not a MinGW repair candidate")
    return {"schema": SCHEMA, "library": str(library), "library_sha256": checksum(library),
            "install": str(install), "compiler": info, "files": rows}


def native_probe(plan: dict, extra_dirs: list[str]) -> dict:
    """Executed ONLY in a disposable render-Python process on Windows."""
    handles = []
    try:
        if os.name != "nt":
            raise RepairError("Native verification requires Windows")
        dll = Path(plan["library"])
        if checksum(dll) != plan["library_sha256"]:
            raise RepairError("projectM changed since the repair plan was made")
        if read_pe(Path(sys.executable))["machine"] != read_pe(dll)["machine"]:
            raise RepairError("Render Python architecture does not match projectM")
        directories, _ = runtime_files(Path(plan["install"]), dll)
        for path in [*directories, *(Path(p) for p in extra_dirs)]:
            handles.append(os.add_dll_directory(str(path.resolve(strict=True))))
        native = ctypes.CDLL(str(dll), winmode=0x1100)
        fn = native.projectm_get_version_components
        fn.argtypes, fn.restype = [ctypes.POINTER(ctypes.c_int)] * 3, None
        parts = [ctypes.c_int() for _ in range(3)]
        fn(*(ctypes.byref(p) for p in parts))
        version = [p.value for p in parts]
        if version != [4, 2, 0]:
            raise RepairError(f"Unexpected projectM API version: {version}")
        for name in ("projectm_create", "projectm_destroy", "projectm_set_frame_time",
                     "projectm_pcm_add_float", "projectm_opengl_render_frame_fbo"):
            getattr(native, name)
        # Check where Windows actually loaded each required runtime. A passing
        # version call must not hide a different DLL loaded from an unrelated app.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_handle = kernel.GetModuleHandleW
        get_handle.argtypes, get_handle.restype = [ctypes.c_wchar_p], ctypes.c_void_p
        get_name = kernel.GetModuleFileNameW
        get_name.argtypes, get_name.restype = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32], ctypes.c_uint32
        locations = []
        for row in plan["files"]:
            module = get_handle(row["name"])
            if not module and row["kind"] == "delay":
                continue
            if not module:
                raise RepairError(f"Required runtime is not loaded: {row['name']}")
            buffer = ctypes.create_unicode_buffer(32768)
            length = get_name(module, buffer, len(buffer))
            if not 0 < length < len(buffer):
                raise RepairError(f"Could not verify loaded location of {row['name']}")
            loaded = Path(buffer.value).resolve(strict=True)
            allowed = [Path(row["destination"]).resolve(), *(Path(d) / row["name"] for d in extra_dirs)]
            if loaded not in allowed or checksum(loaded) != row["sha256"]:
                raise RepairError(f"Windows loaded an unexpected copy of {row['name']}: {loaded}")
            locations.append({"name": row["name"], "path": str(loaded), "sha256": row["sha256"]})
        return {"state": "loadable", "version": version, "loaded_runtimes": locations,
                "scope": "DLL loading/API verification only; no OpenGL rendering was attempted"}
    except (OSError, ValueError, RuntimeError, AttributeError) as exc:
        return {"state": "load-failed", "error": str(exc), "winerror": getattr(exc, "winerror", None)}
    finally:
        for handle in reversed(handles):
            handle.close()


def subprocess_probe(plan_file: Path, extra: list[str], report_file: Path) -> dict:
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--probe", str(plan_file),
                             "--report", str(report_file), "--extra-dirs", *extra],
                            capture_output=True, text=True, errors="replace", timeout=60, check=False)
    if not report_file.is_file():
        raise RepairError(f"Native check ended without a report ({result.returncode}): {result.stderr[-1600:]}")
    report = json.loads(read_text(report_file))
    if result.returncode or report.get("state") != "loadable":
        raise RepairError("Native check failed: " + report.get("error", result.stderr[-1600:]))
    return report


def apply_plan(plan: dict, work: Path, *, probe=subprocess_probe) -> dict:
    """Stage and test first; never overwrite; roll back only files we added."""
    created = []
    staging = work / "staging"
    staging.mkdir()
    try:
        for row in plan["files"]:
            source = Path(row["source"])
            if checksum(source) != row["sha256"]:
                raise RepairError(f"Compiler runtime changed: {row['name']}")
            if row["action"] == "copy":
                shutil.copyfile(source, staging / row["name"])
                if checksum(staging / row["name"]) != row["sha256"]:
                    raise RepairError(f"Staged runtime checksum mismatch: {row['name']}")
        plan_file = work / "plan.json"
        save(plan_file, plan)
        probe(plan_file, [str(staging.resolve())], work / "staged-check.json")
        for row in plan["files"]:
            if row["action"] != "copy":
                continue
            target = Path(row["destination"])
            # Exclusive creation cannot overwrite an existing file, including a
            # concurrent install. Record immediately so partial writes roll back.
            with target.open("xb") as output:
                created.append((target, row["sha256"]))
                with (staging / row["name"]).open("rb") as source:
                    shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            if checksum(target) != row["sha256"]:
                raise RepairError(f"Installed runtime checksum mismatch: {row['name']}")
        # This second process has NO access to the staging search path.
        final = probe(plan_file, [], work / "installed-check.json")
        if checksum(Path(plan["library"])) != plan["library_sha256"]:
            raise RepairError("Original projectM library unexpectedly changed")
        return {"state": "verified", "copied": [str(p) for p, _ in created], "native": final}
    except BaseException:
        rollback = []
        for path, expected in reversed(created):
            try:
                if path.is_file() and checksum(path) == expected:
                    path.unlink()
                    rollback.append({"path": str(path), "state": "removed-new-file"})
                else:
                    rollback.append({"path": str(path), "state": "left-for-inspection-partial-or-changed"})
            except OSError as exc:
                rollback.append({"path": str(path), "state": "rollback-failed", "error": str(exc)})
        save(work / "rollback.json", {"files": rollback})
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--apply", action="store_true", help="Copy verified missing runtime DLLs, then recheck")
    parser.add_argument("--probe", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--extra-dirs", nargs="*", default=[], help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe:
        if args.report is None:
            parser.error("Internal probe requires a report path")
        report = native_probe(json.loads(read_text(args.probe)), args.extra_dirs)
        save(args.report, report)
        return 0 if report["state"] == "loadable" else 2
    if args.repository is None:
        parser.error("--repository is required")
    work = None
    try:
        if os.name != "nt":
            raise RepairError("Run with FrameRudiments' Windows render Python, not WSL/Linux")
        runtime, library = library_for(args.repository)
        expected_python = (args.repository / "models/.venv-render/Scripts/python.exe").resolve(strict=True)
        if Path(sys.executable).resolve() != expected_python:
            raise RepairError(f"Use the project's render Python: {expected_python}")
        if read_pe(expected_python)["machine"] != read_pe(library)["machine"]:
            raise RepairError("The render Python and projectM architectures differ")
        diagnostics = (runtime / "diagnostics").resolve()
        if not diagnostics.is_relative_to(runtime):
            raise RepairError("Diagnostic output escapes the project-local runtime")
        work = diagnostics / ("mingw-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        work.mkdir(parents=True)
        if not work.resolve().is_relative_to(runtime):
            raise RepairError("Diagnostic output escapes the project-local runtime")
        info = checked_compiler(compiler_for(runtime, library), read_pe(library)["machine"])
        print(f"Recorded GNU compiler: {info['path']} ({info['version']}, {info['target']})", flush=True)
        print(f"Build binding: {info['binding']}", flush=True)
        plan = make_plan(library, runtime / "install", info)
        for row in plan["files"]:
            print(f"  {row['action']}: {row['name']} <- {row['source']}", flush=True)
        if not args.apply:
            save(work / "plan.json", plan)
            print(f"Plan only. No runtime files copied. Rerun with --apply.\nReport: {work}")
            return 0
        result = apply_plan(plan, work)
        save(work / "result.json", result)
        print(f"\nMinGW repair VERIFIED: projectM 4.2.0 loads with project-local runtime DLLs.\n"
              f"Added {len(result['copied'])} DLL(s). Original projectM bytes are unchanged.\n"
              f"Reports: {work}\nThis verifies DLL loading, not OpenGL rendering.", flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        if work is not None and work.is_dir():
            save(work / "error.json", {"state": "failed", "error": str(exc)})
        print(f"MinGW repair stopped: {exc}\nReports: {work or 'not created'}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
