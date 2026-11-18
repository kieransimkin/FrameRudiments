"""Dependency-light USD scene admission, projected geometry, exact time and rollback."""
from hashlib import sha256
import json
from types import SimpleNamespace

import numpy as np
import pytest

from framerudiments import usd_m15 as mod


SOURCE = '''#usda 1.0
(
    metersPerUnit = 1
    upAxis = "Y"
    timeCodesPerSecond = 24
)
def Xform "World" {
    def Camera "Camera" {
        float focalLength = 35
        float horizontalAperture = 20
        float verticalAperture = 15
        float2 clippingRange = (0.1, 100)
        double3 xformOp:translate = (0, 0, 6)
        uniform token[] xformOpOrder = ["xformOp:translate"]
    }
    def Mesh "Triangle" {
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(-1, -1, 0), (1, -1, 0), (0, 1, 0)]
        uniform token subdivisionScheme = "none"
        color3f[] primvars:displayColor = [(1, 0.25, 0.1)]
    }
}
'''


def sample(tmp_path, source=SOURCE):
    file = tmp_path / "scene.usda"
    file.write_text(source, encoding="utf-8")
    return file, sha256(file.read_bytes()).hexdigest()


def params(path, pin):
    return dict(path=path, sha256=pin, camera="/World/Camera", frame=24,
                fps="24000/1001", width=128, height=96)


def test_admission_and_rational_metadata(tmp_path):
    path, digest = sample(tmp_path)
    spec = mod.frame_spec(**params(path, digest))
    assert spec["fps"] == "24000/1001"
    assert spec["frame"] == 24
    assert mod.digest(path) == digest
    assert mod.capabilities()["hydra_qualified"] is False


@pytest.mark.parametrize("change", (
    "@outside.usd@", "subLayers = []", "references = []", "payloads = []",
    "asset[] textures = []", "variantSets = []", "clips = {}", "relocates = {}",
))
def test_disallows_external_arcs_and_unknown_composition(tmp_path, change):
    path, digest = sample(tmp_path, SOURCE + "\n" + change + "\n")
    with pytest.raises(ValueError):
        mod.frame_spec(**params(path, digest))


def test_mutated_pin_symlink_and_wrong_extension_fail(tmp_path):
    path, digest = sample(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        mod.frame_spec(**params(path, "0" * 64))
    alias = tmp_path / "alias.usda"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        mod.frame_spec(**params(alias, digest))
    other = tmp_path / "bad.usdc"
    other.write_bytes(path.read_bytes())
    with pytest.raises(ValueError):
        mod.frame_spec(**params(other, digest))


@pytest.mark.parametrize("bad", [
    {"camera": "../Camera"}, {"camera": "/World/Cam/../Other"},
    {"frame": -1}, {"frame": 1.5}, {"fps": "0"}, {"fps": "30000/0"},
    {"fps": "30000.0"}, {"width": 8000}, {"height": 1},
])
def test_invalid_frame_selection_fails(tmp_path, bad):
    path, pin = sample(tmp_path)
    args = params(path, pin)
    args.update(bad)
    with pytest.raises(ValueError):
        mod.frame_spec(**args)


def example_scene(depth_near=2, depth_far=5):
    def tri(z, colour):
        return {"colour": colour, "opacity": 1, "vertices": [
            [8, 8, z, -1, -1, -z], [55, 8, z, 1, -1, -z],
            [30, 43, z, 0, 1, -z]], "triangles": [[0, 1, 2]]}
    return {"schema": mod.SCHEMA, "hydra": "not_qualified", "width": 64, "height": 48,
            "meshes": [tri(depth_far, [0, 0, 1]), tri(depth_near, [1, 0, 0])]}


def test_camera_depth_normals_and_draw_order():
    src = example_scene()
    a = mod.raster(src)
    assert a.size == (64, 48)
    assert a.colour_space == "Linear-sRGB"
    assert float(a.depth[20, 30]) == pytest.approx(2)
    assert tuple(a.rgb[20, 30]) == (1, 0, 0)
    assert float(a.alpha[20, 30]) == 1
    assert np.linalg.norm(a.normals[20, 30]) == pytest.approx(1)
    assert float(a.depth[0, 0]) == 0
    src["meshes"].reverse()
    b = mod.raster(src)
    for key in ("rgb", "alpha", "normals", "depth"):
        assert np.array_equal(getattr(a, key), getattr(b, key))


def test_worker_errors_and_receipt_verification(tmp_path, monkeypatch):
    path, pin = sample(tmp_path)
    args = params(path, pin)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom"))
    with pytest.raises(RuntimeError):
        mod.inspect(**args)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({"schema": mod.SCHEMA, "camera": "/Other",
                                         "source_sha256": pin}).encode()))
    with pytest.raises(RuntimeError):
        mod.inspect(**args)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        mod.subprocess.TimeoutExpired("usd", timeout=1)))
    with pytest.raises(TimeoutError):
        mod.inspect(**args, timeout=1)


def test_no_overwrite_precedes_optional_native_runtime(tmp_path):
    path, pin = sample(tmp_path)
    output = tmp_path / "output.exr"
    output.write_bytes(b"must survive")
    with pytest.raises(ValueError, match="exists"):
        mod.render_exr(path, pin, output, camera="/World/Camera", frame=0,
                       fps="24", width=64, height=48)
    assert output.read_bytes() == b"must survive"


def test_material_and_normal_validation(tmp_path):
    assert mod._normal((0, 0, 1, 0, 0, -1), (0, 0, 1, 1, 0, -1),
                       (0, 0, 1, 0, 1, -1)) == [0, 0, 1]
    with pytest.raises(ValueError):
        mod._normal([0] * 6, [0] * 6, [0] * 6)
    with pytest.raises(ValueError):
        mod._vector([0, float("nan"), 1], "point", 3)


def test_worker_render_failure_cannot_mutate_existing_output(tmp_path, monkeypatch):
    path, pin = sample(tmp_path)
    target = tmp_path / "protected.exr"
    target.write_bytes(b"owned by another operation")
    monkeypatch.setattr(mod, "inspect", lambda *args, **kwargs: pytest.fail("should not run the worker"))
    with pytest.raises(ValueError):
        mod.render_exr(path, pin, target, camera="/World/Camera", frame=0,
                       fps="24", width=64, height=48)
    assert target.read_bytes() == b"owned by another operation"


def test_raster_rejects_invalid_scene_dimensions_and_type():
    scene = example_scene()
    scene["hydra"] = "qualified"
    with pytest.raises(ValueError):
        mod.raster(scene)
    scene["hydra"] = "not_qualified"
    scene["width"] = 0
    with pytest.raises(ValueError):
        mod.raster(scene)
