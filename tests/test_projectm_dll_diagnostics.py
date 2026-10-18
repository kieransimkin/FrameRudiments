"""Synthetic PE/loader regression tests. Never load executable fixtures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "projectm_dll_diagnostics.py"
spec = importlib.util.spec_from_file_location("projectm_dll_diagnostics", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def image(names=(), *, machine=0x8664, delay=(), delay_va=False):
    is32 = machine == 0x014C
    optional_size = 224 if is32 else 240
    data = bytearray(4096)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", data, 0x84, machine, 1)
    struct.pack_into("<H", data, 0x94, optional_size)
    optional = 0x98
    struct.pack_into("<H", data, optional, 0x10B if is32 else 0x20B)
    image_base = 0x400000
    struct.pack_into("<I" if is32 else "<Q", data, optional + (28 if is32 else 24), image_base)
    struct.pack_into("<I", data, optional + 60, 0x200)
    directory_offset = 96 if is32 else 112
    struct.pack_into("<I", data, optional + directory_offset - 4, 16)
    section = optional + optional_size
    data[section:section + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, section + 8, 0xE00, 0x1000, 0xE00, 0x200)
    strings = 0x500
    for number, values, rva, stride in ((1, names, 0x1000, 20), (13, delay, 0x1100, 32)):
        if not values:
            continue
        struct.pack_into("<II", data, optional + directory_offset + number * 8,
                         rva, (len(values) + 1) * stride)
        for index, name in enumerate(values):
            raw = name.encode("ascii") + b"\0"
            data[strings:strings + len(raw)] = raw
            name_rva = strings + 0xE00
            at = rva - 0xE00 + index * stride
            if number == 1:
                struct.pack_into("<IIIII", data, at, 0, 0, 0, name_rva, 0)
            else:
                struct.pack_into("<IIIIIIII", data, at, 0 if delay_va else 1,
                                 name_rva + (image_base if delay_va else 0), 0, 0, 0, 0, 0, 0)
            strings += len(raw)
    return bytes(data)


def installed(tmp_path, names=(), *, machine=0x8664, delay=()):
    root = tmp_path / "install"
    folder = root / "bin"
    folder.mkdir(parents=True)
    dll = folder / "libprojectM-4.dll"
    dll.write_bytes(image(names, machine=machine, delay=delay))
    write_receipt(dll)
    exe = tmp_path / "python.exe"
    exe.write_bytes(image(machine=machine))
    return root, dll, exe


def write_receipt(dll):
    (dll.parent / "framerudiments-projectm.json").write_text(json.dumps({
        "source_commit": m.PROJECTM_COMMIT,
        "library_sha256": hashlib.sha256(dll.read_bytes()).hexdigest(),
    }))


def native_error(code=126):
    exc = OSError("The specified module could not be found")
    exc.winerror = code
    return exc


def fake_api(version=(4, 2, 0)):
    def get_version(*values):
        for value, expected in zip(values, version):
            value._obj.value = expected
    return SimpleNamespace(projectm_get_version_components=get_version)


def probe(root, dll, exe, *, failed=(), code=126, success=False, api=None):
    loaded, opened, closed = [], [], []
    def add(path):
        opened.append(path)
        return SimpleNamespace(close=lambda: closed.append(path))
    def loader(path, **kwargs):
        loaded.append((path, kwargs))
        if path == str(dll.resolve()):
            if success:
                return fake_api() if api is None else api
            raise native_error(code)
        if Path(path).name.casefold() in {s.casefold() for s in failed}:
            raise native_error(code)
        return object()
    result = m.diagnose(dll, root, windows=True, executable=exe, loader=loader, add_directory=add)
    return result, loaded, opened, closed


@pytest.mark.parametrize("machine,architecture,fmt", [(0x014C, "x86", "PE32"),
                                                       (0x8664, "x64", "PE32+"),
                                                       (0xAA64, "arm64", "PE32+")])
def test_import_tables_and_architectures(machine, architecture, fmt):
    result = m.pe_metadata(image(["KERNEL32.dll", "MSVCP140.dll"], machine=machine,
                                  delay=["optional.dll"]))
    assert result["architecture"] == architecture and result["format"] == fmt
    assert result["imports"] == [{"name": "KERNEL32.dll", "kind": "required"},
                                  {"name": "MSVCP140.dll", "kind": "required"},
                                  {"name": "optional.dll", "kind": "delay"}]


def test_pe32_delay_va_import():
    assert m.pe_metadata(image(machine=0x014C, delay=["optional.dll"], delay_va=True))["imports"][0]["name"] == "optional.dll"


@pytest.mark.parametrize("data", [b"", b"MZ", b"not a PE", image()[:64], image()[:300]])
def test_truncated_or_invalid_image_rejected(data):
    with pytest.raises(m.PEError):
        m.pe_metadata(data)


@pytest.mark.parametrize("name", ["../bad.dll", "C:\\bad.dll", "foo/bar.dll", "a.dll.exe", "", "..dll"])
def test_import_path_injection_rejected(name):
    with pytest.raises(m.PEError):
        m.pe_metadata(image([name]))


def test_invalid_directory_count_rejected():
    data = bytearray(image())
    struct.pack_into("<I", data, 0x98 + 108, 100)
    with pytest.raises(m.PEError, match="directories"):
        m.pe_metadata(data)


def test_missing_descriptor_terminator_rejected():
    data = bytearray(image(["a.dll"]))
    struct.pack_into("<I", data, 0x98 + 112 + 8 + 4, 20)
    with pytest.raises(m.PEError, match="Unterminated"):
        m.pe_metadata(data)


def test_import_outside_raw_section_rejected():
    data = bytearray(image(["a.dll"]))
    struct.pack_into("<I", data, 0x200 + 12, 0x400000)
    with pytest.raises(m.PEError, match="relative address"):
        m.pe_metadata(data)


@pytest.mark.parametrize("name,kind", [("VCRUNTIME140_1.dll", "vc14-release"),
    ("MSVCP140_ATOMIC_WAIT.dll", "vc14-release"), ("msvcp140_codecvt_ids.dll", "vc14-release"),
    ("vcruntime140d.dll", "msvc-debug"), ("ucrtbased.dll", "msvc-debug"),
    ("libgcc_s_seh-1.dll", "mingw"), ("libstdc++-6.dll", "mingw"),
    ("api-ms-win-crt-runtime-l1-1-0.dll", "windows-api-set"), ("evilmsvcp140.dll", "other")])
def test_runtime_classification(name, kind):
    assert m.runtime_class(name) == kind


def test_success_checks_version_and_closes_search_handles(tmp_path):
    root, dll, exe = installed(tmp_path)
    report, loaded, opened, closed = probe(root, dll, exe, success=True)
    assert report["state"] == "loadable" and report["native_version"] == [4, 2, 0]
    assert not report["vc14_repair_candidate"]
    assert loaded[0][1]["winmode"] == 0x1100
    assert closed == list(reversed(opened))


def test_wrong_api_version_is_not_success(tmp_path):
    root, dll, exe = installed(tmp_path)
    assert probe(root, dll, exe, success=True, api=fake_api((4, 1, 0)))[0]["state"] == "api-error"


def test_missing_api_is_not_success(tmp_path):
    root, dll, exe = installed(tmp_path)
    assert probe(root, dll, exe, success=True, api=object())[0]["state"] == "api-error"


def test_tampered_receipt_blocks_every_load(tmp_path):
    root, dll, exe = installed(tmp_path)
    dll.write_bytes(image(["tampered.dll"]))
    report, loaded, _, _ = probe(root, dll, exe)
    assert report["state"] == "integrity-error" and not loaded


def test_wrong_python_architecture_blocks_install_and_load(tmp_path):
    root, dll, exe = installed(tmp_path)
    exe.write_bytes(image(machine=0x014C))
    report, loaded, _, _ = probe(root, dll, exe)
    assert report["state"] == "architecture-error" and not loaded
    assert not report["vc14_repair_candidate"]


def test_vc_missing_is_named_and_repairable(tmp_path):
    root, dll, exe = installed(tmp_path, ["MSVCP140.dll", "KERNEL32.dll"])
    report, _, opened, closed = probe(root, dll, exe, failed=["MSVCP140.dll"])
    assert report["vc14_repair_candidate"] and report["load_error"]["winerror"] == 126
    assert "MSVCP140.dll" in m.format_report(report)
    assert closed == list(reversed(opened))


def test_mingw_not_mistaken_for_vc(tmp_path):
    root, dll, exe = installed(tmp_path, ["libstdc++-6.dll"])
    report, _, _, _ = probe(root, dll, exe, failed=["libstdc++-6.dll"])
    assert not report["vc14_repair_candidate"]


def test_debug_runtime_never_suggests_release_redist(tmp_path):
    root, dll, exe = installed(tmp_path, ["MSVCP140d.dll", "MSVCP140.dll"])
    report, _, _, _ = probe(root, dll, exe, failed=["MSVCP140.dll", "MSVCP140d.dll"])
    assert not report["vc14_repair_candidate"] and "Debug" in report["recommendation"]


def test_delay_imports_not_treated_as_missing_requirements(tmp_path):
    root, dll, exe = installed(tmp_path, ["KERNEL32.dll"], delay=["MSVCP140.dll"])
    report, loaded, _, _ = probe(root, dll, exe, failed=["MSVCP140.dll"])
    assert not report["vc14_repair_candidate"]
    assert "MSVCP140.dll" not in [name for name, _ in loaded]


def test_entry_point_update_is_a_candidate_not_a_claim(tmp_path):
    root, dll, exe = installed(tmp_path, ["MSVCP140.dll"])
    report, _, _, _ = probe(root, dll, exe, code=127)
    assert report["vc14_repair_candidate"] and "not a confirmed cause" in report["recommendation"]


def test_non_vc_entry_point_error_not_repairable(tmp_path):
    root, dll, exe = installed(tmp_path, ["KERNEL32.dll"])
    report, _, _, _ = probe(root, dll, exe, code=127)
    assert not report["vc14_repair_candidate"]


def test_other_windows_errors_not_claimed_repairable(tmp_path):
    root, dll, exe = installed(tmp_path, ["MSVCP140.dll"])
    report, _, _, _ = probe(root, dll, exe, failed=["MSVCP140.dll"], code=1114)
    assert not report["vc14_repair_candidate"]


def test_local_transitive_imports_and_mixed_case_dlls(tmp_path):
    root, dll, exe = installed(tmp_path, ["helper.dll"])
    extra = root / "extra"
    extra.mkdir()
    (extra / "HELPER.DLL").write_bytes(image(["MSVCP140.dll"]))
    report, loaded, opened, closed = probe(root, dll, exe, failed=["MSVCP140.dll"])
    assert any(row["importer"] == "HELPER.DLL" and row["name"] == "MSVCP140.dll" for row in report["dependencies"])
    assert str(extra.resolve()) in opened
    assert closed == list(reversed(opened))
    assert report["vc14_repair_candidate"]


def test_duplicate_dll_names_rejected(tmp_path):
    root, dll, exe = installed(tmp_path)
    extra = root / "extra"
    extra.mkdir()
    (extra / dll.name).write_bytes(dll.read_bytes())
    report, loaded, _, _ = probe(root, dll, exe)
    assert report["state"] == "diagnostic-error" and not loaded


def test_local_wrong_architecture_blocks_repair(tmp_path):
    root, dll, exe = installed(tmp_path, ["helper.dll"])
    (dll.parent / "helper.dll").write_bytes(image(machine=0x014C))
    report, _, _, _ = probe(root, dll, exe)
    assert report["state"] == "architecture-error" and not report["vc14_repair_candidate"]


def test_directory_handle_cleanup_when_registration_fails(tmp_path):
    root, dll, exe = installed(tmp_path)
    extra = root / "extra"
    extra.mkdir()
    (extra / "extra.dll").write_bytes(image())
    closed = []
    def add(path):
        if path == str(extra):
            raise OSError("directory registration failed")
        return SimpleNamespace(close=lambda: closed.append(path))
    report = m.diagnose(dll, root, windows=True, executable=exe, add_directory=add, loader=lambda *a, **k: fake_api())
    assert report["state"] == "diagnostic-error" and closed == [str(dll.parent)]


def test_non_windows_does_not_execute_dll(tmp_path):
    root, dll, _ = installed(tmp_path, ["MSVCP140.dll"])
    def fail(*args, **kwargs):
        raise AssertionError("No native load is allowed")
    report = m.diagnose(dll, root, windows=False, loader=fail)
    assert report["state"] == "inspection-only" and not report["vc14_repair_candidate"]
