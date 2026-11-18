"""M15.1 offline GStreamer boundary tests; no external native plugins needed here."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments import gstreamer_m15 as gst
from framerudiments.gstreamer_m15_cli import main


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_manifest(tmp_path, *, size=(32, 24), count=3, fps="30000/1001"):
    frames = []
    for n in range(count):
        file = tmp_path / f"frame-{n:03d}.png"
        Image.new("RGB", size, (n * 40, 85, 170)).save(file)
        frames.append({"path": file.name, "sha256": digest(file)})
    data = {"schema": "framerudiments.video-frames.v1", "width": size[0],
            "height": size[1], "fps": fps, "frames": frames}
    manifest = tmp_path / "frames.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest


def fake_binaries(tmp_path, monkeypatch):
    launch, inspect = (tmp_path / "gst-launch-1.0", tmp_path / "gst-inspect-1.0")
    for file in (launch, inspect):
        file.write_text("fake")

    def fake(cmd, *, timeout=20, cwd=None):
        if cmd[1] == "--version":
            return SimpleNamespace(returncode=0, stdout=b"gst-launch-1.0 version 1.26.0\n", stderr=b"")
        if Path(cmd[0]).name == "gst-inspect-1.0":
            return SimpleNamespace(returncode=0, stdout=(
                b"Factory Details:\n  Long-name test\nPlugin Details:\n  Version 1.26.0\n"), stderr=b"")
        assert cwd is not None and cmd[1:3] == ["-q", "-e"]
        assert cmd.count("!") == 7
        assert cmd[-2:] == ["filesink", "location=video.mp4"]
        (Path(cwd) / "video.mp4").write_bytes(b"fake-video")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(gst, "_bounded_command", fake)
    monkeypatch.setattr(gst, "_verify_export", lambda *a, **kw: {
        "frames": kw["count"], "timestamps": "exact rational CFR from zero"})
    return launch, inspect


def test_gstreamer_capabilities_fails_closed_without_tools(monkeypatch):
    monkeypatch.setattr(gst.shutil, "which", lambda _: None)
    report = gst.capabilities()
    assert not report["available"] and not report["hardware_gpu_qualified"]
    assert report["elements"] == {}


def test_gstreamer_capability_discovery_checks_every_required_factory(tmp_path, monkeypatch):
    launch, inspect = fake_binaries(tmp_path, monkeypatch)
    report = gst.capabilities(gst_launch=launch, gst_inspect=inspect)
    assert report["available"]
    assert set(report["elements"]) == set(gst.REQUIRED_ELEMENTS)
    assert all(row["plugin_version"] == "1.26.0" for row in report["elements"].values())
    assert report["exact_pts_qualified"] is False  # a probe is not encoded verification


def test_pipeline_closed_and_rational_timescale():
    for fps in (Fraction(30000, 1001), Fraction(24000, 1001), Fraction(60)):
        parts = gst.pipeline(fps, 128, 96, bitrate_kbps=800, preset="fast")
        assert f"framerate={fps.numerator}/{fps.denominator}" in parts
        assert f"trak-timescale={fps.numerator}" in parts
        assert parts.count("!") == 7
        assert "tcpclientsink" not in parts and "appsrc" not in parts
    for kwargs in ({"bitrate_kbps": 0}, {"bitrate_kbps": 50001},
                   {"bitrate_kbps": True}, {"preset": "invalid"}):
        with pytest.raises(ValueError):
            gst.pipeline(Fraction(25), 128, 96, **kwargs)


def test_raw_stride_padding_and_sha256(tmp_path):
    manifest = make_manifest(tmp_path, size=(34, 24), count=2)
    _manifest, w, h, _fps, rows, stride, size = gst._strict_local_frames(manifest)
    assert stride == 104 and size == 104 * 24 * 2
    output = tmp_path / "source.raw"
    sha, byte_count = gst._write_raw(rows, w, h, stride, output)
    content = output.read_bytes()
    assert sha == hashlib.sha256(content).hexdigest()
    assert byte_count == len(content) == size
    assert content[102:104] == b"\0\0"
    assert content[206:208] == b"\0\0"


def test_pinned_frames_cannot_escape_manifest_directory(tmp_path):
    manifest = make_manifest(tmp_path)
    data = json.loads(manifest.read_text())
    for value in ("../other.png", "/tmp/absolute.png", "C:/absolute.png", "a\\name.png", "a:evil.png"):
        data["frames"][0]["path"] = value
        manifest.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="manifest directory"):
            gst._strict_local_frames(manifest)


def test_bad_manifest_and_raw_budget_are_rejected(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data["frames"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="SHA-256 differs"):
        gst._strict_local_frames(manifest)
    manifest = make_manifest(tmp_path)
    monkeypatch.setattr(gst, "MAX_RAW_BYTES", 2000)
    with pytest.raises(ValueError, match="raw frame budget"):
        gst._strict_local_frames(manifest)


def test_encode_stages_padded_frames_independently_verifies_then_publishes(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path, size=(34, 24), fps="24000/1001")
    launch, inspect = fake_binaries(tmp_path, monkeypatch)
    outfile = tmp_path / "with spaces.mp4"
    result = gst.encode_video(manifest, outfile, gst_launch=launch, gst_inspect=inspect)
    receipt = tmp_path / "with spaces.mp4.receipt.json"
    assert outfile.read_bytes() == b"fake-video"
    assert result == json.loads(receipt.read_text())
    assert result["encoding"]["row_stride_bytes"] == 104
    assert result["encoding"]["fps"] == "24000/1001"
    assert result["exact_pts_qualified"]
    assert result["video_sha256"] == digest(outfile)
    assert not any(p.name.startswith(".fr-m15-") for p in tmp_path.iterdir())
    with pytest.raises(ValueError, match="already exists"):
        gst.encode_video(manifest, outfile, gst_launch=launch, gst_inspect=inspect)


def test_encode_rolls_back_verification_failure(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    launch, inspect = fake_binaries(tmp_path, monkeypatch)
    monkeypatch.setattr(gst, "_verify_export", lambda *a, **kw: (_ for _ in ()).throw(ValueError("PTS mismatch")))
    with pytest.raises(ValueError, match="PTS mismatch"):
        gst.encode_video(manifest, tmp_path / "bad.mp4", gst_launch=launch, gst_inspect=inspect)
    assert not (tmp_path / "bad.mp4").exists()
    assert not (tmp_path / "bad.mp4.receipt.json").exists()
    assert not any(p.name.startswith(".fr-m15-") for p in tmp_path.iterdir())


def test_encode_rolls_back_if_receipt_hardlink_fails(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    launch, inspect = fake_binaries(tmp_path, monkeypatch)
    link = gst.os.link
    calls = []

    def sabotage(source, target):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("receipt destination unavailable")
        return link(source, target)

    monkeypatch.setattr(gst.os, "link", sabotage)
    with pytest.raises(OSError, match="receipt destination"):
        gst.encode_video(manifest, tmp_path / "atomic.mp4", gst_launch=launch, gst_inspect=inspect)
    assert not (tmp_path / "atomic.mp4").exists()
    assert not (tmp_path / "atomic.mp4.receipt.json").exists()


def test_bad_source_pixel_mode_cannot_be_encoded(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    frame = tmp_path / "frame-000.png"
    Image.new("RGBA", (32, 24), (1, 2, 3, 4)).save(frame)
    doc = json.loads(manifest.read_text())
    doc["frames"][0]["sha256"] = digest(frame)
    manifest.write_text(json.dumps(doc))
    launch, inspect = fake_binaries(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="RGB8 PNG"):
        gst.encode_video(manifest, tmp_path / "bad-mode.mp4", gst_launch=launch, gst_inspect=inspect)
    assert not (tmp_path / "bad-mode.mp4").exists()


def test_cli_inspect_reports_missing_tool_not_failure(monkeypatch, capsys):
    monkeypatch.setattr(gst.shutil, "which", lambda _: None)
    assert main(["inspect"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["available"]


def test_missing_required_plugin_is_an_explicit_failure(tmp_path, monkeypatch):
    launch, inspect = fake_binaries(tmp_path, monkeypatch)
    original = gst._bounded_command

    def one_missing(cmd, **kwargs):
        if Path(cmd[0]).name == "gst-inspect-1.0" and cmd[1] == "mp4mux":
            return SimpleNamespace(returncode=1, stdout=b"", stderr=b"not found")
        return original(cmd, **kwargs)

    monkeypatch.setattr(gst, "_bounded_command", one_missing)
    report = gst.capabilities(gst_launch=launch, gst_inspect=inspect)
    assert not report["available"]
    assert report["elements"]["mp4mux"] == {"available": False, "plugin_version": None}
    manifest = make_manifest(tmp_path)
    with pytest.raises(ValueError, match="elements are unavailable"):
        gst.encode_video(manifest, tmp_path / "missing.mp4", gst_launch=launch, gst_inspect=inspect)
    assert not (tmp_path / "missing.mp4").exists()


def test_pinned_symlink_cannot_escape_source_directory(tmp_path):
    safe = tmp_path / "safe"
    safe.mkdir()
    manifest = make_manifest(safe)
    other = tmp_path / "outside.png"
    Image.new("RGB", (32, 24), "red").save(other)
    source = safe / "frame-000.png"
    source.unlink()
    try:
        source.symlink_to(other)
    except (NotImplementedError, OSError):
        # Windows runners without symlink privileges still test the same
        # fail-closed boundary; the regular rendering matrix forbids skips.
        document = json.loads(manifest.read_text())
        document["frames"][0]["path"] = "../outside.png"
        manifest.write_text(json.dumps(document))
        with pytest.raises(ValueError, match="manifest directory"):
            gst._strict_local_frames(manifest)
        return
    data = json.loads(manifest.read_text())
    data["frames"][0]["sha256"] = digest(other)
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="resolved outside"):
        gst._strict_local_frames(manifest)


def test_input_changes_after_encode_cannot_be_published(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    launch, inspect = fake_binaries(tmp_path, monkeypatch)
    run = gst._bounded_command

    def changing_encoder(command, **kwargs):
        result = run(command, **kwargs)
        if "-e" in command:
            source = tmp_path / "frame-000.png"
            Image.new("RGB", (32, 24), "red").save(source)
        return result

    monkeypatch.setattr(gst, "_bounded_command", changing_encoder)
    with pytest.raises(ValueError, match="SHA-256 differs"):
        gst.encode_video(manifest, tmp_path / "changed.mp4", gst_launch=launch, gst_inspect=inspect)
    assert not (tmp_path / "changed.mp4").exists()
    assert not (tmp_path / "changed.mp4.receipt.json").exists()


def test_output_receipt_collision_rejected_before_encoders_start(tmp_path, monkeypatch):
    manifest = make_manifest(tmp_path)
    launch, inspect = fake_binaries(tmp_path, monkeypatch)
    receipt = tmp_path / "output.mp4.receipt.json"
    receipt.write_text("should-stay-unchanged")
    with pytest.raises(ValueError, match="receipt already exists"):
        gst.encode_video(manifest, tmp_path / "output.mp4", gst_launch=launch, gst_inspect=inspect)
    assert receipt.read_text() == "should-stay-unchanged"
    assert not (tmp_path / "output.mp4").exists()


def test_user_presets_never_accept_arbitrary_pipeline_parts():
    for bad in ("fast ! filesink", "../x264enc", "fast\n! tcpclientsink"):
        with pytest.raises(ValueError, match="preset"):
            gst.pipeline(Fraction(30), 128, 96, preset=bad)
