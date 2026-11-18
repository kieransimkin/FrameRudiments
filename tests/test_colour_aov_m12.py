"""Bounded AOV schema, immutability, optional transport and OCIO guards."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from framerudiments import colour_aov as m12
from framerudiments import colour_aov_cli


def frame():
    rgb = np.array([[[0., 0.25, 3.5], [-0.1, 1.2, .5]],
                    [[1., 0., 0.], [2., .75, 0.]]], dtype=np.float32)
    a = np.full((2, 2), .75, dtype=np.float32)
    z = np.array([[0, 1], [2, 3]], dtype=np.float32)
    normals = np.zeros((2, 2, 3), dtype=np.float32)
    normals[..., 2] = 1
    motion = np.full((2, 2, 2), -.625, dtype=np.float32)
    return m12.AOVFrame(rgb, "ACEScg", a, z, normals, motion)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_all_channels_preserve_hdr_negative_values_and_provenance():
    f = frame()
    assert f.size == (2, 2)
    assert f.rgb[0, 0, 2] == 3.5
    assert f.rgb[0, 1, 0] < 0
    assert set(f.channels()) == {"RGB", "A", "Z", "N", "motion"}
    receipt = f.evidence()
    assert receipt["schema"] == m12.SCHEMA
    assert receipt["channels"]["Z"] == hashlib.sha256(f.depth.tobytes()).hexdigest()


def test_frame_is_detached_and_read_only():
    raw = np.full((2, 2, 3), .4, dtype=np.float32)
    source = m12.AOVFrame(raw, "Linear Rec.709")
    raw[0, 0, 0] = 999
    assert source.rgb[0, 0, 0] == pytest.approx(.4)
    with pytest.raises(ValueError):
        source.rgb[0, 0, 0] = 2


@pytest.mark.parametrize("field,value", [
    ("rgb", np.zeros((2, 2, 4), dtype="float32")),
    ("rgb", np.ones((2, 2, 3), dtype="float64")),
    ("rgb", np.full((2, 2, 3), np.nan, dtype="float32")),
    ("alpha", np.full((2, 2), 1.1, dtype="float32")),
    ("alpha", np.full((2, 2), -.1, dtype="float32")),
    ("depth", np.full((2, 2), -.1, dtype="float32")),
    ("depth", np.zeros((3, 2), dtype="float32")),
    ("normals", np.zeros((2, 2, 2), dtype="float32")),
    ("velocity", np.zeros((3, 2, 2), dtype="float32")),
])
def test_invalid_float_buffers_rejected(field, value):
    base = {"rgb": frame().rgb, "colour_space": "ACEScg"}
    base[field] = value
    with pytest.raises(ValueError):
        m12.AOVFrame(**base)


def test_linear_decoding_srgb_midgray_and_black_white():
    display = np.array([[[0, 128, 255]]], dtype=np.uint8)
    linear = m12.srgb8_to_scene_linear(display)
    assert linear[0, 0, 0] == 0
    assert linear[0, 0, 1] == pytest.approx(.21586, rel=1e-4)
    assert linear[0, 0, 2] == 1
    assert m12.scene_linear_to_srgb8(linear, tone_map="clip").tolist() == display.tolist()


def test_preview_tonemaps_hdr_without_mutating_source():
    f = frame()
    snap = f.rgb.tobytes()
    out = m12.scene_linear_to_srgb8(f.rgb, exposure=1, tone_map="reinhard")
    assert out.dtype == np.uint8 and out.shape == (2, 2, 3)
    assert out[0, 0, 2] < 255
    assert f.rgb.tobytes() == snap


@pytest.mark.parametrize("exposure,mode", [(float("nan"), "clip"), (20, "clip"), (0, "xyz")])
def test_invalid_display_transform_fails(exposure, mode):
    with pytest.raises(ValueError):
        m12.scene_linear_to_srgb8(frame().rgb, exposure=exposure, tone_map=mode)


def test_manifest_pinned_exact_npy_roundtrip(tmp_path):
    source = frame()
    pins = {}
    for name, arr in source.channels().items():
        path = tmp_path / f"{name}.npy"
        np.save(path, arr)
        pins[name] = {"path": path.name, "sha256": sha(path)}
    manifest = tmp_path / "source.json"
    manifest.write_text(json.dumps({"schema": m12.MANIFEST_SCHEMA,
                                    "colour_space": "ACEScg", "channels": pins}))
    restored, proof = m12.frame_from_manifest(manifest)
    for name, value in source.channels().items():
        np.testing.assert_array_equal(restored.channels()[name], value)
    assert proof["manifest_sha256"] == sha(manifest)
    (tmp_path / "Z.npy").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        m12.frame_from_manifest(manifest)


def test_manifest_rejects_unknown_keys_traversal_and_pickle(tmp_path):
    arr = tmp_path / "src.npy"
    np.save(arr, frame().rgb)
    doc = {"schema": m12.MANIFEST_SCHEMA, "colour_space": "ACEScg",
           "channels": {"RGB": {"path": arr.name, "sha256": sha(arr)}}}
    manifest = tmp_path / "manifest.json"
    for mutation in (lambda d: d.update(extra=True),
                     lambda d: d["channels"].update(unknown=d["channels"]["RGB"]),
                     lambda d: d["channels"]["RGB"].update(path="../src.npy")):
        updated = json.loads(json.dumps(doc))
        mutation(updated)
        manifest.write_text(json.dumps(updated))
        with pytest.raises((ValueError, FileNotFoundError)):
            m12.frame_from_manifest(manifest)
    manifest.write_text('{"schema":"x","schema":"x"}')
    with pytest.raises(ValueError, match="Duplicate"):
        m12.frame_from_manifest(manifest)


class FakeFile:
    """Contract-only fake: real OpenEXR must run in the optional CI job."""
    def __init__(self, *args, separate_channels=False):
        self.parts = [object()]
        self.mode = "read" if isinstance(args[0], str) else "write"
        if self.mode == "read":
            with np.load(args[0], allow_pickle=False) as data:
                self._header = json.loads(str(data["header"].item()))
                self._arrays = {k: data[k].copy() for k in data.files if k != "header"}
        else:
            self._header, self._arrays = args
        self._header = dict(self._header)

    def header(self):
        return self._header

    def channels(self):
        return {k: SimpleNamespace(pixels=v) for k, v in self._arrays.items()}

    def write(self, path):
        header = dict(self._header)
        header["dataWindow"] = ((0, 0), (next(iter(self._arrays.values())).shape[1] - 1,
                                       next(iter(self._arrays.values())).shape[0] - 1))
        with open(path, "wb") as handle:
            np.savez(handle, header=json.dumps(header), **self._arrays)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def fake_exr(monkeypatch):
    codec = SimpleNamespace(File=FakeFile, ZIP_COMPRESSION="ZIP", scanlineimage="scanline")
    monkeypatch.setattr(m12, "_openexr", lambda: codec)


def test_fake_exr_boundary_exact_roundtrip_and_no_overwrite(tmp_path, monkeypatch):
    fake_exr(monkeypatch)
    source = frame()
    output = tmp_path / "result.exr"
    receipt = m12.write_exr(output, source, source_evidence={"test": "fixture"})
    assert receipt["readback"] == "exact_float32"
    assert receipt["source"]["test"] == "fixture"
    assert output.is_file()
    assert (tmp_path / "result.exr.receipt.json").is_file()
    restored = m12.read_exr(output, sha256=sha(output))
    assert restored.rgb.tobytes() == source.rgb.tobytes()
    assert restored.velocity.tobytes() == source.velocity.tobytes()
    assert m12.inspect_exr(output, sha256=sha(output))["schema"] == m12.SCHEMA
    with pytest.raises(ValueError, match="overwrite"):
        m12.write_exr(output, source)
    assert len(list(tmp_path.glob(".fr-aov-*"))) == 0


def test_exr_write_rollback_on_source_mismatch(tmp_path, monkeypatch):
    fake_exr(monkeypatch)
    source = frame()
    original = m12.read_exr
    def corrupted(*args, **kwargs):
        frame_back = original(*args, **kwargs)
        black = np.zeros_like(frame_back.rgb)
        return m12.AOVFrame(black, frame_back.colour_space, frame_back.alpha,
                            frame_back.depth, frame_back.normals, frame_back.velocity)
    monkeypatch.setattr(m12, "read_exr", corrupted)
    with pytest.raises(ValueError, match="roundtrip"):
        m12.write_exr(tmp_path / "broken.exr", source)
    assert not (tmp_path / "broken.exr").exists()
    assert not (tmp_path / "broken.exr.receipt.json").exists()
    assert not list(tmp_path.glob(".fr-aov-*"))


def test_unpinned_exr_and_missing_colour_metadata_fail(tmp_path, monkeypatch):
    fake_exr(monkeypatch)
    source = frame()
    dest = tmp_path / "test.exr"
    m12.write_exr(dest, source)
    with pytest.raises(ValueError, match="SHA-256"):
        m12.read_exr(dest, sha256="0" * 64)


def test_ocio_transforms_only_rgb_preserves_other_channels_and_pins(tmp_path, monkeypatch):
    config = tmp_path / "palette.ocio"
    config.write_text("mock trusted ocio configuration")
    class Processor:
        def applyRGB(self, pixels):
            pixels *= 2
    class Config:
        @staticmethod
        def CreateFromFile(_path):
            return Config()
        def getColorSpace(self, name):
            return name if name in ("ACEScg", "scene_linear") else None
        def getProcessor(self, src, dst):
            return SimpleNamespace(getDefaultCPUProcessor=lambda: Processor())
    monkeypatch.setattr(m12, "_ocio", lambda: SimpleNamespace(Config=Config))
    src = frame()
    converted = m12.transform_ocio(src, config=config, config_sha256=sha(config),
                                  source_space="ACEScg", target_space="scene_linear")
    assert converted.rgb[0, 0, 2] == 7.
    assert converted.rgb[0, 1, 0] == pytest.approx(-.2)
    assert np.array_equal(converted.depth, src.depth)
    assert converted.colour_space == "scene_linear"
    assert src.rgb[0, 0, 2] == 3.5
    with pytest.raises(ValueError, match="source colour space"):
        m12.transform_ocio(src, config=config, config_sha256=sha(config),
                           source_space="not-the-source", target_space="ACEScg")
    with pytest.raises(ValueError, match="SHA-256"):
        m12.transform_ocio(src, config=config, config_sha256="f" * 64,
                           source_space="ACEScg", target_space="scene_linear")


def test_colour_cli_export_inspect_with_injected_codec(tmp_path, monkeypatch, capsys):
    fake_exr(monkeypatch)
    rgb = tmp_path / "rgb.npy"
    np.save(rgb, frame().rgb)
    manifest = tmp_path / "channels.json"
    manifest.write_text(json.dumps({"schema": m12.MANIFEST_SCHEMA,
                                    "colour_space": "ACEScg",
                                    "channels": {"RGB": {"path": rgb.name, "sha256": sha(rgb)}}}))
    out = tmp_path / "result.exr"
    assert colour_aov_cli.main(["export-exr", str(manifest), "--output", str(out)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["colour_space"] == "ACEScg"
    assert colour_aov_cli.main(["inspect-exr", str(out), "--sha256", sha(out)]) == 0
    assert json.loads(capsys.readouterr().out)["colour_space"] == "ACEScg"


def test_explicit_optional_dependencies_fail_with_actionable_error(monkeypatch):
    monkeypatch.setattr(m12, "_openexr", lambda: (_ for _ in ()).throw(RuntimeError("OpenEXR needed")))
    with pytest.raises(RuntimeError, match="OpenEXR needed"):
        m12.write_exr("test.exr", frame())
