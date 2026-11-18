"""M14.2 profiles, immutable cache, frame-addressed graph inputs and pin invalidation."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from framerudiments.vision_m14 import RESULT_SCHEMA, _hash, _preview, inspect_request
from framerudiments.vision_graph_m14 import (
    CACHE_SCHEMA, PROFILE_SCHEMA, SCHEMA, VisionGraphSources,
    VisionProfile, encode_graph_surface,
)


def sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


class Project:
    def __init__(self, folder, *, width=16, height=16, count=8):
        self.base = Path(folder)
        self.width, self.height, self.frame_count = width, height, count
        self.inputs = []

    def ref(self, ref):
        assert set(ref) == {"path", "sha256"}
        path = self.base / ref["path"]
        if sha(path) != ref["sha256"]:
            raise ValueError("Pinned manifest checksum changed")
        entry = {"path": str(path.resolve()), "sha256": ref["sha256"]}
        self.inputs.append(entry)
        return entry


def inputs(tmp_path, *, task="segmentation", frame=0, shape=(16, 16), previous=False):
    base = tmp_path
    (base / "weights.onnx").write_bytes(b"test-model-stub-not-valid-onnx")
    cur = base / "current.png"
    Image.new("RGB", shape, (50, 80, 160)).save(cur)
    if previous:
        old = base / "previous.png"
        Image.new("RGB", shape, (30, 80, 120)).save(old)
    doc = {
        "schema": "framerudiments.vision-request.v1", "task": task,
        "model": {"path": "weights.onnx", "sha256": sha(base / "weights.onnx")},
        "current": {"path": cur.name, "sha256": sha(cur)},
        "input_names": ["input"], "output_name": "out", "width": shape[0], "height": shape[1],
    }
    if previous:
        doc["previous"] = {"path": old.name, "sha256": sha(old)}
    path = base / f"request-{frame}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    profile = {
        "schema": PROFILE_SCHEMA, "id": "primary", "task": task,
        "model_sha256": sha(base / "weights.onnx"),
        "width": shape[0], "height": shape[1],
        "input_names": ["input"], "output_name": "out",
    }
    cfg = {"schema": SCHEMA, "cache_dir": "_vision_cache", "profiles": [profile],
           "sources": [{"id": "foreground", "profile": "primary",
                        "frames": [{"frame": frame, "request": {"path": path.name, "sha256": sha(path)}}]}]}
    return Project(tmp_path, width=shape[0], height=shape[1]), cfg


def mocked_infer(manifest, output):
    spec = inspect_request(manifest)
    out = Path(output)
    out.mkdir()
    dtype = np.float32
    data = np.full((spec.height, spec.width, spec.channels), 0.5, dtype=dtype)
    if spec.task == "relative_depth":
        data[:, :, 0] = 3.0
    if spec.task == "optical_flow":
        data[:, :, 0], data[:, :, 1] = -2.0, 4.0
    np.save(out / "estimate.npy", data, allow_pickle=False)
    preview, mapping = _preview(data, spec)
    preview.save(out / "preview.png")
    result = dict(spec.document(), schema=RESULT_SCHEMA, model_runtime="onnxruntime",
                  runtime_version="test", tensor_shape=list(data.shape), tensor_dtype="float32",
                  estimate_sha256=_hash(out / "estimate.npy"),
                  preview_sha256=_hash(out / "preview.png"), preview_mapping=mapping,
                  value_range=[float(np.min(data)), float(np.max(data))])
    (out / "receipt.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def test_profile_validation_rejects_incorrect_dimensions_names_and_tasks():
    valid = {"schema": PROFILE_SCHEMA, "id": "foreground", "task": "segmentation",
             "model_sha256": "a" * 64, "width": 16, "height": 16,
             "input_names": ["input"], "output_name": "out"}
    profile = VisionProfile.parse(valid)
    assert profile.display_range == (0, 1)
    for difference in ({"width": True}, {"task": "unknown"}, {"model_sha256": "short"},
                       {"input_names": []}, {"output_name": ""}, {"schema": "wrong"},
                       {"id": "../../bad"}, {"display_range": [2, 2]},
                       {"activation": "sigmoidz"}):
        with pytest.raises(ValueError):
            VisionProfile.parse({**valid, **difference})


def test_graph_source_alpha_mask_is_derived_from_float_prediction(monkeypatch, tmp_path):
    p, cfg = inputs(tmp_path)
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    owner = VisionGraphSources(p, cfg)
    assert owner.source_ids == {"vision:foreground"}
    image = owner.source("vision:foreground", 0)
    assert image.mode == "RGBA" and image.getpixel((0, 0)) == (255, 255, 255, 128)
    assert owner.last_used["vision:foreground"]["model_estimated"] is True
    assert {"weights", "current"} <= {Path(pth["path"]).stem for pth in p.inputs}


def test_cached_model_is_computed_only_once_and_writes_checksums(monkeypatch, tmp_path):
    project, cfg = inputs(tmp_path)
    times = []

    def inference(manifest, output):
        times.append(1)
        return mocked_infer(manifest, output)

    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", inference)
    owner = VisionGraphSources(project, cfg)
    first = owner.source("vision:foreground", 0).tobytes()
    other = VisionGraphSources(Project(tmp_path), cfg)
    assert other.source("vision:foreground", 0).tobytes() == first
    assert times == [1]
    caches = [p for p in (tmp_path / "_vision_cache").iterdir() if p.is_dir()]
    assert len(caches) == 1
    receipt = json.loads((caches[0] / "cache.json").read_text())
    assert receipt["schema"] == CACHE_SCHEMA
    assert receipt["receipt_sha256"] == sha(caches[0] / "receipt.json")


def test_corrupt_cache_does_not_silently_recompute(monkeypatch, tmp_path):
    project, cfg = inputs(tmp_path)
    calls = []

    def inference(manifest, output):
        calls.append(True)
        return mocked_infer(manifest, output)

    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", inference)
    owner = VisionGraphSources(project, cfg)
    owner.source("vision:foreground", 0)
    cache = next((tmp_path / "_vision_cache").iterdir())
    with (cache / "estimate.npy").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(ValueError, match="provenance"):
        owner.source("vision:foreground", 0)
    assert len(calls) == 1


def test_request_changes_fail_while_existing_compositor_is_alive(monkeypatch, tmp_path):
    project, cfg = inputs(tmp_path)
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    owner = VisionGraphSources(project, cfg)
    image = owner.source("vision:foreground", 0)
    assert image.getchannel("A").getpixel((0, 0)) == 128
    (tmp_path / "current.png").write_bytes(b"edited")
    with pytest.raises(ValueError):
        owner.source("vision:foreground", 0)


def test_cold_frame_missing_policy_is_explicit(monkeypatch, tmp_path):
    project, cfg = inputs(tmp_path, frame=3)
    owner = VisionGraphSources(project, cfg)
    with pytest.raises(ValueError, match="Missing"):
        owner.source("vision:foreground", 1)
    cfg["sources"][0]["missing"] = "transparent"
    transparent = VisionGraphSources(Project(tmp_path), cfg).source("vision:foreground", 1)
    assert transparent.getbbox() is None
    cfg["sources"][0]["missing"] = "hold"
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    held = VisionGraphSources(Project(tmp_path), cfg)
    assert held.source("vision:foreground", 4).getchannel("A").getpixel((0, 0)) == 128


def test_depth_and_flow_projections_are_distinguished_from_geometry(monkeypatch, tmp_path):
    for kind in ("relative_depth", "optical_flow"):
        base = tmp_path / kind
        base.mkdir()
        project, cfg = inputs(base, task=kind, previous=kind == "optical_flow")
        monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
        owner = VisionGraphSources(project, cfg)
        result = owner.source("vision:foreground", 0)
        assert result.mode == "RGBA"
        if kind == "relative_depth":
            assert result.getpixel((0, 0)) == (76, 76, 76, 255)
        else:
            assert result.getpixel((0, 0))[0] < 128
            assert result.getpixel((0, 0))[1] > 128


def test_profile_change_changes_cache_identity_and_readout(monkeypatch, tmp_path):
    project, cfg = inputs(tmp_path)
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    first = VisionGraphSources(project, cfg)
    first.source("vision:foreground", 0)
    a = first.last_used["vision:foreground"]["cache_key"]
    cfg["profiles"][0]["display_range"] = [0, .8]
    second = VisionGraphSources(Project(tmp_path), cfg)
    second.source("vision:foreground", 0)
    assert second.last_used["vision:foreground"]["cache_key"] != a
    assert len(list((tmp_path / "_vision_cache").iterdir())) == 2


def test_output_frame_and_profiles_are_strict(tmp_path):
    project, cfg = inputs(tmp_path)
    for broken in ({"profiles": []}, {"sources": []}, {"schema": "invalid"},
                   {"cache_dir": "../escape"}, {"cache_dir": "C:/bad"},
                   {"max_cache_bytes": -5}, {"cache_dir": "."}, {"unknown": 123}):
        with pytest.raises(ValueError):
            VisionGraphSources(Project(tmp_path), {**cfg, **broken})
    adapter = VisionGraphSources(project, cfg)
    for frame in (-1, 1.5, 8, True):
        with pytest.raises(ValueError):
            adapter.source("vision:foreground", frame)


def test_exact_source_profile_mismatch_detected_at_preparation(tmp_path):
    project, cfg = inputs(tmp_path)
    cfg["profiles"][0]["model_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="profile"):
        VisionGraphSources(project, cfg)


def test_graph_surface_rejects_invalid_float_and_shape():
    profile = VisionProfile.parse({"schema": PROFILE_SCHEMA, "id": "test", "task": "segmentation",
                                   "model_sha256": "a" * 64, "width": 16, "height": 16,
                                   "input_names": ["input"], "output_name": "out"})
    for value in (np.full((16, 16, 1), np.nan, np.float32),
                  np.ones((16, 16, 3), np.float32), np.ones((16, 16, 1), np.float64),
                  np.full((16, 16, 1), 2, np.float32)):
        with pytest.raises(ValueError):
            encode_graph_surface(value, profile)


def test_large_project_uses_explicit_mask_resampling_only(monkeypatch, tmp_path):
    _, cfg = inputs(tmp_path)
    with pytest.raises(ValueError, match="resolution mismatch"):
        VisionGraphSources(Project(tmp_path, width=32, height=32), cfg)
    cfg["sources"][0]["resample"] = "nearest"
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    owner = VisionGraphSources(Project(tmp_path, width=32, height=32), cfg)
    assert owner.source("vision:foreground", 0).size == (32, 32)
    # An opt-in flow frame remains in input pixel units, so no implicit remap.
    assert owner.evidence["native_geometry"] is False


def test_failed_worker_rolls_back_stage_without_publishing(monkeypatch, tmp_path):
    project, config = inputs(tmp_path)
    owner = VisionGraphSources(project, config)

    def fail(_manifest, target):
        path = Path(target)
        path.mkdir()
        (path / "partial").write_text("incomplete")
        raise TimeoutError("model exceeded deadline")

    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", fail)
    with pytest.raises(TimeoutError, match="deadline"):
        owner.source("vision:foreground", 0)
    assert list((tmp_path / "_vision_cache").iterdir()) == []


def test_cache_quota_rejects_large_existing_entry_without_overwriting(monkeypatch, tmp_path):
    project, cfg = inputs(tmp_path)
    cfg["max_cache_bytes"] = 64 * 1024 * 1024
    owner = VisionGraphSources(project, cfg)
    blocked = tmp_path / "_vision_cache" / ("a" * 64)
    blocked.mkdir(parents=True)
    with (blocked / "big").open("wb") as f:
        f.truncate(64 * 1024 * 1024)
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    with pytest.raises(ValueError, match="quota"):
        owner.source("vision:foreground", 0)
    assert (blocked / "big").stat().st_size == 64 * 1024 * 1024


def test_flow_resizing_is_rejected_even_when_requested(tmp_path):
    folder = tmp_path / "flow"
    folder.mkdir()
    _, config = inputs(folder, task="optical_flow", previous=True)
    config["sources"][0]["resample"] = "bilinear"
    with pytest.raises(ValueError, match="resolution mismatch"):
        VisionGraphSources(Project(folder, width=32, height=32), config)
