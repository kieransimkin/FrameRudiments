"""Inspect a pinned projectM Windows DLL without external diagnostic packages.

The PE reader never executes files. The explicit Windows probe loads only the
verified projectM library and DLL names declared in its import tables, with
process-local search directories confined to its install tree. Nothing is
installed, copied, downloaded or deleted by this module.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys

PROJECTM_COMMIT = "349b7cc2db312d47cef6be24c6856ced6643a385"
MACHINES = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64"}
MAX_DLL_BYTES = 256 * 1024 * 1024
SCHEMA = "framerudiments.projectm-dll-diagnostic.v1"


class PEError(ValueError):
    """Malformed or unsupported Windows executable metadata."""


def pe_metadata(data: bytes) -> dict:
    """Read architecture and normal/delay DLL imports from bounded PE32/PE32+."""
    def unpack(fmt: str, offset: int):
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(data):
            raise PEError("Truncated PE header or table")
        return struct.unpack_from(fmt, data, offset)

    if len(data) < 64 or data[:2] != b"MZ":
        raise PEError("Not a Windows PE executable (missing MZ header)")
    pe = unpack("<I", 60)[0]
    if pe < 64 or data[pe:pe + 4] != b"PE\0\0":
        raise PEError("Invalid PE signature")
    machine, section_count = unpack("<HH", pe + 4)
    optional_size = unpack("<H", pe + 20)[0]
    optional = pe + 24
    magic = unpack("<H", optional)[0]
    if magic not in (0x10B, 0x20B):
        raise PEError("Unsupported PE optional header")
    directory_offset = 96 if magic == 0x10B else 112
    if optional_size < directory_offset or optional + optional_size > len(data):
        raise PEError("Truncated PE optional header")
    directory_count = unpack("<I", optional + directory_offset - 4)[0]
    if directory_count > (optional_size - directory_offset) // 8:
        raise PEError("PE data directories exceed the optional header")
    image_base = unpack("<I" if magic == 0x10B else "<Q", optional + (28 if magic == 0x10B else 24))[0]
    header_size = unpack("<I", optional + 60)[0]
    if section_count > 96:
        raise PEError("Unreasonable PE section count")
    sections = []
    for index in range(section_count):
        section = optional + optional_size + 40 * index
        unpack("<40s", section)
        virtual_size, address, raw_size, raw_offset = unpack("<IIII", section + 8)
        if raw_offset + raw_size > len(data):
            raise PEError("PE section extends beyond file")
        sections.append((address, max(virtual_size, raw_size), raw_offset, raw_size))

    def offset_of(rva: int, size: int = 1) -> int:
        if rva < 0 or size < 0:
            raise PEError("Invalid PE relative address")
        if rva + size <= min(header_size, len(data)):
            return rva
        for address, virtual_size, raw_offset, raw_size in sections:
            delta = rva - address
            if 0 <= delta < virtual_size and delta + size <= raw_size:
                return raw_offset + delta
        raise PEError("PE relative address has no bounded file data")

    def dll_name(rva: int) -> str:
        value = bytearray()
        for index in range(260):
            byte = data[offset_of(rva + index)]
            if byte == 0:
                try:
                    name = value.decode("ascii")
                except UnicodeDecodeError as exc:
                    raise PEError("Non-ASCII DLL import name") from exc
                if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.[dD][lL][lL]", name) or ".." in name:
                    raise PEError("Unsafe or invalid DLL import name")
                return name
            value.append(byte)
        raise PEError("Unterminated DLL import name")

    imports = []
    for number, kind, stride in ((1, "required", 20), (13, "delay", 32)):
        if directory_count <= number:
            continue
        rva, size = unpack("<II", optional + directory_offset + 8 * number)
        if not rva and not size:
            continue
        if not rva or size < stride or size // stride > 4096:
            raise PEError("Invalid import-directory size")
        terminated = False
        for index in range(size // stride):
            fields = unpack("<" + "I" * (stride // 4), offset_of(rva + stride * index, stride))
            if not any(fields):
                terminated = True
                break
            name_rva = fields[3] if kind == "required" else fields[1]
            if kind == "delay" and not fields[0] & 1:
                name_rva -= image_base
            imports.append({"name": dll_name(name_rva), "kind": kind})
        if not terminated:
            raise PEError("Unterminated import-descriptor table")
    return {"machine": machine, "architecture": MACHINES.get(machine, f"unknown-0x{machine:04x}"),
            "format": "PE32" if magic == 0x10B else "PE32+", "imports": imports}


def read_pe(path: Path) -> dict:
    if not 0 < path.stat().st_size <= MAX_DLL_BYTES:
        raise PEError("Executable is empty or exceeds the 256 MiB diagnostic limit")
    return pe_metadata(path.read_bytes())


def runtime_class(name: str) -> str:
    name = name.casefold()
    if re.fullmatch(r"(?:msvcp140(?:_[0-9]+|_atomic_wait|_codecvt_ids)?|vcruntime140(?:_[0-9]+)?|concrt140)d\.dll", name) or name == "ucrtbased.dll":
        return "msvc-debug"
    if re.fullmatch(r"(?:msvcp140(?:_[0-9]+|_atomic_wait|_codecvt_ids)?|vcruntime140(?:_[0-9]+)?|concrt140)\.dll", name):
        return "vc14-release"
    if name.startswith(("libgcc_s_", "libstdc++-", "libwinpthread-")):
        return "mingw"
    if name.startswith(("api-ms-win-", "ext-ms-win-")):
        return "windows-api-set"
    return "other"


def runtime_files(root: Path, library: Path) -> tuple[list[Path], dict[str, Path]]:
    root, library = root.resolve(strict=True), library.resolve(strict=True)
    if not root.is_dir() or not library.is_file() or not library.is_relative_to(root):
        raise ValueError("projectM library must reside in its own install directory")
    files = {}
    folders = {library.parent}
    count = 0
    for candidate in root.rglob("*"):
        count += 1
        if count > 20000:
            raise ValueError("Runtime install tree exceeds the diagnostic file limit")
        if candidate.suffix.casefold() != ".dll" or not candidate.is_file():
            continue
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("A runtime DLL resolves outside the install tree")
        name = path.name.casefold()
        if name in files and files[name] != path:
            raise ValueError(f"Ambiguous duplicate runtime DLL: {path.name}")
        files[name] = path
        folders.add(path.parent)
    return [library.parent, *sorted(folders - {library.parent})], files


def error_record(exc: OSError) -> dict:
    return {"winerror": getattr(exc, "winerror", None), "errno": exc.errno,
            "message": str(exc)[:2000]}


def repair_decision(report: dict) -> tuple[bool, str]:
    """Recommend a VC14 update only for concrete, compatible import evidence."""
    if report.get("state") == "loadable":
        return False, "The native library already loads; no runtime installation is needed."
    if report.get("state") != "load-failed":
        return False, "Resolve the integrity, architecture or diagnostic error before any installation."
    required = [row for row in report.get("dependencies", []) if row.get("kind") == "required"]
    if any(row.get("classification") == "msvc-debug" for row in required):
        return False, "A Debug CRT is imported. Rebuild projectM in Release; a release redistributable is not a fix."
    failed = [row for row in required if row.get("state") == "load-failed"]
    if any(row.get("classification") != "vc14-release" for row in failed):
        return False, "A non-VC14 dependency failed. Inspect the named import and its original Windows error."
    code = report.get("load_error", {}).get("winerror")
    if failed and code in (126, 127):
        return True, "Required VC14 runtime imports failed to load; install/update the matching Microsoft runtime, then recheck."
    if code == 127 and any(row.get("classification") == "vc14-release" for row in required):
        return True, "An entry point is missing and projectM imports VC14. An outdated CRT is a candidate, not a confirmed cause; recheck after updating."
    return False, "The evidence does not identify a VC14 repair. See the preserved Windows error and import report."


def diagnose(library: Path, install_root: Path, *, windows: bool | None = None,
             loader=None, add_directory=None, executable: Path | None = None) -> dict:
    report = {"schema": SCHEMA, "state": "diagnostic-error", "library": str(library),
              "dependencies": [], "vc14_repair_candidate": False}
    handles = []
    try:
        library = library.resolve(strict=True)
        directories, local_files = runtime_files(install_root, library)
        metadata = read_pe(library)  # Check size/format before reading for the receipt hash.
        checksum = hashlib.sha256(library.read_bytes()).hexdigest()
        receipt = json.loads((library.parent / "framerudiments-projectm.json").read_text(encoding="utf-8"))
        if receipt.get("source_commit") != PROJECTM_COMMIT or receipt.get("library_sha256") != checksum:
            report["state"] = "integrity-error"
            raise ValueError("projectM source pin or library SHA-256 does not match its receipt")
        report.update(library=str(library), library_sha256=checksum, source_commit=PROJECTM_COMMIT,
                      architecture=metadata["architecture"], search_directories=[str(d) for d in directories])
        windows = os.name == "nt" if windows is None else windows
        if not windows:
            report.update(state="inspection-only", imports=metadata["imports"],
                          note="PE metadata read only; Windows DLL loading was not tested.")
            return report
        process_metadata = read_pe(Path(sys.executable) if executable is None else executable)
        report["python_architecture"] = process_metadata["architecture"]
        if metadata["machine"] != process_metadata["machine"] or metadata["machine"] not in MACHINES:
            report.update(state="architecture-error", note="projectM and the render Python need the same supported machine architecture.")
            return report
        loader = ctypes.CDLL if loader is None else loader
        add_directory = os.add_dll_directory if add_directory is None else add_directory
        for directory in directories:
            handles.append(add_directory(str(directory)))
        try:
            # DLL_LOAD_DIR | DEFAULT_DIRS: never enable ambient PATH/CWD lookup.
            lib = loader(str(library), winmode=0x1100)
            version_fn = lib.projectm_get_version_components
            version_fn.argtypes = [ctypes.POINTER(ctypes.c_int)] * 3
            version_fn.restype = None
            version = [ctypes.c_int() for _ in range(3)]
            version_fn(*(ctypes.byref(item) for item in version))
            report["native_version"] = [item.value for item in version]
            report["state"] = "loadable" if report["native_version"] == [4, 2, 0] else "api-error"
            return report
        except AttributeError as exc:
            report.update(state="api-error", note=str(exc))
            return report
        except OSError as exc:
            report.update(state="load-failed", load_error=error_record(exc))
        queue = [(library, metadata, "required")]
        visited = set()
        loaded_dependencies = []  # Retain handles until the disposable process exits.
        while queue:
            parent, current, inherited_kind = queue.pop(0)
            if (parent, inherited_kind) in visited:
                continue
            visited.add((parent, inherited_kind))
            if len(visited) > 128:
                raise ValueError("Dependency traversal exceeded 128 local modules")
            for item in current["imports"]:
                name = item["name"]
                kind = "delay" if inherited_kind == "delay" else item["kind"]
                row = {"name": name, "importer": parent.name, "kind": kind,
                       "classification": runtime_class(name)}
                candidate = local_files.get(name.casefold())
                if candidate:
                    child = read_pe(candidate)
                    row["path"] = str(candidate)
                    row["architecture"] = child["architecture"]
                    if child["machine"] != metadata["machine"]:
                        row["state"] = "architecture-error"
                        report["state"] = "architecture-error"
                    queue.append((candidate, child, kind))
                if kind == "delay":
                    row.setdefault("state", "not-probed-delay-load")
                elif row.get("state") != "architecture-error":
                    try:
                        loaded_dependencies.append(loader(str(candidate) if candidate else name,
                                                          winmode=0x1100 if candidate else 0x1000))
                        row["state"] = "loadable"
                    except OSError as exc:
                        row.update(state="load-failed", error=error_record(exc))
                report["dependencies"].append(row)
        report["vc14_repair_candidate"], report["recommendation"] = repair_decision(report)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        report["diagnostic_error"] = str(exc)[:2000]
        if report["state"] not in ("integrity-error", "architecture-error"):
            report["state"] = "diagnostic-error"
        report["vc14_repair_candidate"] = False
    finally:
        for handle in reversed(handles):
            handle.close()
    return report


def format_report(report: dict) -> str:
    lines = [f"projectM native check: {report['state']}"]
    if report.get("architecture"):
        lines.append(f"DLL architecture: {report['architecture']}; render Python: {report.get('python_architecture', 'not probed')}")
    if report.get("load_error"):
        err = report["load_error"]
        lines.append(f"Original Windows error {err['winerror']}: {err['message']}")
    for row in report.get("dependencies", []):
        if row["state"] not in ("loadable", "not-probed-delay-load"):
            lines.append(f"  {row['importer']} -> {row['name']}: {row['state']} ({row['classification']})")
            if row.get("error"):
                lines.append(f"    {row['error']['message']}")
    for key in ("recommendation", "diagnostic_error", "note"):
        if report.get(key):
            lines.append(report[key])
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args(argv)
    report = diagnose(args.library, args.install_root)
    if args.json_report:
        # Refuse to overwrite a report or any other existing file.
        with args.json_report.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(format_report(report))
    return 0 if report["state"] == "loadable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
