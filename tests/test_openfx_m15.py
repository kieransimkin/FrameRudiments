"""Dependency-light M15.3 trust, memory and transaction validation contracts."""
from hashlib import sha256
import json
from pathlib import Path
import platform

from PIL import Image
import pytest

from framerudiments import openfx_m15 as ofx


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def ofx_request(tmp_path):
    data = bytearray(128)
    arch = platform.machine().lower()
    is_arm = arch in ("aarch64", "arm64")
    if platform.system() == "Linux":
        data[0:4] = b"\x7fELF"
        data[4:6] = b"\x02\x01"
        data[18:20] = (183 if is_arm else 62).to_bytes(2, "little")
    elif platform.system() == "Windows":
        data[:2] = b"MZ"
        data[60:64] = (64).to_bytes(4, "little")
        data[64:68] = b"PE\x00\x00"
        data[68:70] = (0xaa64 if is_arm else 0x8664).to_bytes(2, "little")
    elif platform.system() == "Darwin":
        data[:4] = b"\xcf\xfa\xed\xfe"
        data[4:8] = (0x0100000c if is_arm else 0x01000007).to_bytes(4, "little")
    else:
        pytest.fail("Unsupported native OFX test host")
    binary = tmp_path / "filter.so"
    binary.write_bytes(data)
    image = tmp_path / "frame.png"
    Image.new("RGBA", (16, 18), (40, 20, 90, 127)).save(image)
    record = {"schema": ofx.SCHEMA,
              "plugin": {"path": "filter.so", "sha256": digest(binary),
                         "identifier": "org.framerudiments.reference.invert",
                         "declared_license": "MIT", "trusted_execution": True},
              "source": {"path": "frame.png", "sha256": digest(image)},
              "frame": 24, "fps": "24000/1001", "timeout_seconds": 10,
              "pixel_contract": {"mode": "RGBA8", "alpha": "straight", "transfer": "sRGB"}}
    manifest = tmp_path / "ofx_request.json"
    manifest.write_text(json.dumps(record), encoding="utf-8")
    return manifest, record


def test_scan_is_bounded_and_does_not_load_code(ofx_request, monkeypatch):
    manifest, _record = ofx_request
    import ctypes
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args: pytest.fail("scan loaded executable code"))
    result = ofx.describe(manifest)
    assert not result["binary_loaded"] and result["execution"] == "not_performed"
    assert result["time_seconds"] == "1001/1000"
    assert result["supported_host"]["parameter_suite"] is False
    assert result["supported_host"]["pixel_depth"] == "OfxBitDepthByte"


def test_scan_records_licence_declaration_not_proof(ofx_request):
    result = ofx.describe(ofx_request[0])
    assert result["declared_license"] == "MIT" and not result["licence_verified_externally"]


@pytest.mark.parametrize("field,value", [
    ("fps", "23.975"), ("fps", 1.5), ("fps", "0/1"), ("fps", "121"),
    ("frame", -1), ("frame", 4.0), ("timeout_seconds", 0),
    ("timeout_seconds", 121), ("pixel_contract", {"mode": "RGBA8", "alpha": "opaque", "transfer": "sRGB"}),
    ("pixel_contract", {"mode": "RGB8", "alpha": "straight", "transfer": "sRGB"}),
])
def test_invalid_contract_rejected(ofx_request, field, value):
    path, row = ofx_request
    row[field] = value
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError):
        ofx.inspect(path)


@pytest.mark.parametrize("bad", ["../filter.so", "/tmp/filter.so", "C:/filter.dll", "..\\filter.dll", "x:/filter.so"])
def test_plugin_path_traversal_fails(ofx_request, bad):
    path, row = ofx_request
    row["plugin"]["path"] = bad
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError):
        ofx.inspect(path)


def test_plugin_hash_mismatch_fails_before_native_load(ofx_request):
    path, row = ofx_request
    row["plugin"]["sha256"] = "0" * 64
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="pin mismatch"):
        ofx.inspect(path)


def test_declared_untrusted_rejected(ofx_request):
    path, row = ofx_request
    row["plugin"]["trusted_execution"] = False
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError):
        ofx.inspect(path)


def test_changed_source_refuses_to_execute(ofx_request):
    path, row = ofx_request
    (path.parent / "frame.png").write_bytes(b"changed")
    with pytest.raises(ValueError):
        ofx.inspect(path)


def test_duplicate_manifest_fields_fail_closed(ofx_request):
    path, _row = ofx_request
    content = path.read_text()
    path.write_text(content.replace('"frame": 24', '"frame": 23, "frame": 24'))
    with pytest.raises(ValueError, match="Duplicate"):
        ofx.inspect(path)


def test_output_must_not_exist(ofx_request, monkeypatch):
    path, _row = ofx_request
    out = path.parent / "result.png"
    out.write_bytes(b"do not replace")
    monkeypatch.setattr(ofx, "_execute_worker", lambda *_args: pytest.fail("should not start worker"))
    with pytest.raises(ValueError):
        ofx.render(path, out)
    assert out.read_bytes() == b"do not replace"


def test_receipt_collision_refused(ofx_request, monkeypatch):
    path, _row = ofx_request
    target = path.parent / "result.png"
    note = path.parent / "result.png.receipt.json"
    note.write_text("keep")
    monkeypatch.setattr(ofx, "_execute_worker", lambda *_args: pytest.fail("should not start worker"))
    with pytest.raises(ValueError):
        ofx.render(path, target)
    assert not target.exists() and note.read_text() == "keep"


def test_missing_worker_file_does_not_publish(ofx_request, monkeypatch):
    path, _row = ofx_request
    monkeypatch.setattr(ofx, "_execute_worker", lambda *_args: {"sha256": "0"*64})
    with pytest.raises(ValueError):
        ofx.render(path, path.parent / "out.png")
    assert not (path.parent / "out.png").exists()


def test_changed_plugin_during_worker_refused(ofx_request, monkeypatch):
    path, _row = ofx_request
    def corrupt(r, stage):
        Image.new("RGBA", (r.width,r.height), "red").save(stage)
        r.plugin.write_bytes(b"tampered")
        return {"sha256": digest(stage), "evidence": {"identifier": r.identifier}}
    monkeypatch.setattr(ofx, "_execute_worker", corrupt)
    with pytest.raises(ValueError, match="changed"):
        ofx.render(path, path.parent / "out.png")
    assert not (path.parent / "out.png").exists()


def test_output_transaction_rolls_back_on_receipt_link_failure(ofx_request, monkeypatch):
    path, _row = ofx_request
    original_link = ofx.os.link
    def fail_receipt(source, dest):
        if str(dest).endswith("receipt.json"):
            raise OSError("simulated receipt publish collision")
        return original_link(source, dest)
    def fake(r, temp):
        Image.new("RGBA", (r.width, r.height)).save(temp)
        return {"sha256": digest(temp), "evidence": {"identifier": r.identifier}}
    monkeypatch.setattr(ofx.os, "link", fail_receipt)
    monkeypatch.setattr(ofx, "_execute_worker", fake)
    with pytest.raises(OSError):
        ofx.render(path, path.parent / "out.png")
    assert not (path.parent / "out.png").exists()


def test_output_success_receipt_is_machine_readable(ofx_request, monkeypatch):
    path, _row = ofx_request
    def fake(r, temp):
        Image.new("RGBA", (r.width, r.height), "#ff00ff").save(temp)
        return {"sha256": digest(temp), "evidence": {"identifier": r.identifier}}
    monkeypatch.setattr(ofx, "_execute_worker", fake)
    result = ofx.render(path, path.parent / "out.png")
    assert json.loads((path.parent / "out.png.receipt.json").read_text()) == result
    assert result["frame"] == 24 and result["fps"] == "24000/1001"
    assert not result["production_approved"]


def test_elf_architecture_mismatch_fails(ofx_request):
    path, row = ofx_request
    binary = path.parent / "filter.so"
    data = bytearray(binary.read_bytes())
    if platform.system() == "Linux":
        data[18:20] = (0x1337).to_bytes(2, "little")
    elif platform.system() == "Windows":
        data[68:70] = (0x1337).to_bytes(2, "little")
    else:
        data[4:8] = (0x1337).to_bytes(4, "little")
    binary.write_bytes(data)
    row["plugin"]["sha256"] = digest(binary)
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="machine|CPU"):
        ofx.inspect(path)
