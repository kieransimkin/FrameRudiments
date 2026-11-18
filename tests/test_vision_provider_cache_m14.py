"""M14.4 provider-scoped GPU caches require authentic pinned CPU parity evidence."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from framerudiments import vision_batch_m14, vision_graph_m14, vision_provider_cache_m14
from framerudiments.vision_batch_m14 import inspect_batch
from framerudiments.vision_graph_m14 import PROFILE_SCHEMA, SCHEMA, VisionGraphSources
from framerudiments.vision_provider_cache_m14 import (
    provider_cache_key, seed_provider_graph_cache,
)
from test_vision_batch_m14 import batch_fixture
from test_vision_graph_m14 import Project

PROVIDER = "CUDAExecutionProvider"


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def fixture(monkeypatch, tmp_path, *, count=3, provider=PROVIDER):
    manifest = batch_fixture(tmp_path, count=count, provider=provider)
    plan = inspect_batch(manifest)

    def run(p, chosen, stage, **kwargs):
        value = .5 if chosen == "CPUExecutionProvider" else .5001
        data = {item.frame: np.full((16, 16, 1), value, dtype=np.float32) for item in p.items}
        return data, {"provider": chosen, "model_sessions": 1,
                      "provider_node_events": count + 1, "batches": [count],
                      "runtime_version": "fake-runtime-1"}

    monkeypatch.setattr(vision_batch_m14, "_run_provider", run)
    stage = tmp_path / "verified-batch"
    stage.mkdir()
    vision_batch_m14._worker(manifest, stage)
    primary = plan.items[0].spec
    profile = {"schema": PROFILE_SCHEMA, "id": "primary", "task": "segmentation",
               "model_sha256": primary.input_hashes["model"], "width": 16, "height": 16,
               "input_names": list(primary.input_names), "output_name": primary.output_name}
    rows = [{"frame": item.frame, "request": {
        "path": Path(item.spec.manifest).relative_to(tmp_path).as_posix(),
        "sha256": digest(item.spec.manifest)}} for item in plan.items]
    cfg = {"schema": SCHEMA, "cache_dir": "_vision_cache", "profiles": [profile],
           "sources": [{"id": "people", "profile": "primary", "frames": rows,
                        "provider": {"name": provider,
                                     "batch_sha256": digest(stage / "batch.json")}}]}
    project = Project(tmp_path, count=10)

    def verify():
        if any(digest(row["path"]) != row["sha256"] for row in project.inputs):
            raise ValueError("Pinned GPU project input changed")
    project.verify_inputs = verify
    return manifest, plan, stage, project, cfg


def test_gpu_cache_requires_pinned_parity_batch_and_never_falls_back(monkeypatch, tmp_path):
    manifest, _plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    owner = VisionGraphSources(project, cfg)
    monkeypatch.setattr(vision_graph_m14, "infer",
                        lambda *_: pytest.fail("GPU cache miss silently used CPU inference"))
    with pytest.raises(ValueError, match="GPU provider prediction"):
        owner.source("vision:people", 0)
    assert not (tmp_path / "_vision_cache").exists()
    result = seed_provider_graph_cache(owner, manifest, stage)
    assert result["new_entries"] == 3 and result["existing_entries"] == 0
    assert result["provider"] == PROVIDER and result["numeric_parity_passed"]
    assert result["physical_device_certified"] is False
    assert owner.source("vision:people", 0).getpixel((0, 0)) == (255, 255, 255, 128)
    assert owner.last_used["vision:people"]["provider"] == PROVIDER
    assert owner.source("vision:people", 2).getchannel("A").getpixel((0, 0)) == 128
    assert len(list((tmp_path / "_vision_cache").glob("gpu-*"))) == 3
    again = seed_provider_graph_cache(owner, manifest, stage)
    assert again["new_entries"] == 0 and again["existing_entries"] == 3


def test_gpu_provider_identity_distinguishes_same_request_cpu_and_gpu(monkeypatch, tmp_path):
    _manifest, plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    owner = VisionGraphSources(project, cfg)
    first = plan.items[0]
    profile = owner.sources["vision:people"][0]
    gpu_key = provider_cache_key(profile, first.spec, PROVIDER, digest(stage / "batch.json"), 0)
    assert gpu_key != owner._identity(profile, first.spec)
    assert provider_cache_key(profile, first.spec, PROVIDER, digest(stage / "batch.json"), 1) != gpu_key
    assert gpu_key != provider_cache_key(profile, first.spec, PROVIDER, "f" * 64, 0)


def test_corrupt_estimate_or_provenance_fails_closed_without_cpu(monkeypatch, tmp_path):
    manifest, _plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    owner = VisionGraphSources(project, cfg)
    seed_provider_graph_cache(owner, manifest, stage)
    cache = list((tmp_path / "_vision_cache").glob("gpu-*"))[0]
    with (cache / "estimate.npy").open("ab") as handle:
        handle.write(b"altered")
    # Choose the entry's exact frame instead of assuming directory order.
    selected = json.loads((cache / "cache.json").read_text())["source_frame"]
    monkeypatch.setattr(vision_graph_m14, "infer",
                        lambda *_: pytest.fail("corrupt GPU cache fell back to CPU"))
    with pytest.raises(ValueError, match="provenance"):
        owner.source("vision:people", selected)


def test_gpu_seal_has_to_match_individual_frame_receipt(monkeypatch, tmp_path):
    manifest, _plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    owner = VisionGraphSources(project, cfg)
    seed_provider_graph_cache(owner, manifest, stage)
    cache = next((tmp_path / "_vision_cache").glob("gpu-*"))
    frame = json.loads((cache / "cache.json").read_text())["source_frame"]
    seal = json.loads((cache / "cache.json").read_text())
    seal["parity"]["max_abs"] = 0.019  # not the original provider/CPU comparison
    (cache / "cache.json").write_text(json.dumps(seal))
    with pytest.raises(ValueError, match="parity"):
        owner.source("vision:people", frame)


def test_missing_cpu_reference_rejected_even_when_manifest_sha_is_pinned(monkeypatch, tmp_path):
    manifest, _plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    receipt = json.loads((stage / "batch.json").read_text())
    receipt["cpu_reference"] = None
    (stage / "batch.json").write_text(json.dumps(receipt))
    cfg["sources"][0]["provider"]["batch_sha256"] = digest(stage / "batch.json")
    owner = VisionGraphSources(project, cfg)
    with pytest.raises(ValueError, match="CPU reference"):
        seed_provider_graph_cache(owner, manifest, stage)


def test_failed_gpu_kernel_attribution_rejected(monkeypatch, tmp_path):
    manifest, _plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    receipt = json.loads((stage / "batch.json").read_text())
    receipt["execution"]["provider_node_events"] = 0
    (stage / "batch.json").write_text(json.dumps(receipt))
    cfg["sources"][0]["provider"]["batch_sha256"] = digest(stage / "batch.json")
    owner = VisionGraphSources(project, cfg)
    with pytest.raises(ValueError, match="GPU kernels"):
        seed_provider_graph_cache(owner, manifest, stage)


def test_gpu_seed_rejects_unpinned_batch_identity(monkeypatch, tmp_path):
    manifest, _plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    cfg["sources"][0]["provider"]["batch_sha256"] = "f" * 64
    owner = VisionGraphSources(project, cfg)
    with pytest.raises(ValueError, match="not pinned"):
        seed_provider_graph_cache(owner, manifest, stage)
    assert not (tmp_path / "_vision_cache").exists()


def test_changed_model_source_rejected_on_warm_GPU_cache(monkeypatch, tmp_path):
    manifest, plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    owner = VisionGraphSources(project, cfg)
    seed_provider_graph_cache(owner, manifest, stage)
    Path(plan.items[0].spec.current).write_bytes(b"new-input-image")
    with pytest.raises(ValueError, match="SHA-256"):
        owner.source("vision:people", 0)


def test_gpu_provider_policy_fails_closed(tmp_path):
    from test_vision_graph_m14 import inputs

    project, cfg = inputs(tmp_path)
    for provider in ("auto", "CPUExecutionProvider", "GPU", 42, None):
        cfg["sources"][0]["provider"] = {"name": provider, "batch_sha256": "a" * 64}
        with pytest.raises(ValueError):
            VisionGraphSources(project, cfg)
    cfg["sources"][0]["provider"] = {"name": PROVIDER, "batch_sha256": "bad"}
    with pytest.raises(ValueError):
        VisionGraphSources(project, cfg)


def test_failure_mid_import_rolls_back_all_staged_cache_entries(monkeypatch, tmp_path):
    manifest, _plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    owner = VisionGraphSources(project, cfg)
    copied = []
    original = vision_provider_cache_m14.shutil.copyfile

    def fail_at_second_file(src, dst):
        copied.append(str(src))
        if len(copied) == 4:
            raise OSError("copy failed mid-stage")
        return original(src, dst)

    monkeypatch.setattr(vision_provider_cache_m14.shutil, "copyfile", fail_at_second_file)
    with pytest.raises(OSError, match="mid-stage"):
        seed_provider_graph_cache(owner, manifest, stage)
    assert not list((tmp_path / "_vision_cache").iterdir())


def test_provider_cache_quota_includes_existing_CPU_entries(monkeypatch, tmp_path):
    manifest, _plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    cfg["max_cache_bytes"] = 64 * 1024 * 1024
    owner = VisionGraphSources(project, cfg)
    other = tmp_path / "_vision_cache" / ("a" * 64)
    other.mkdir(parents=True)
    with (other / "big").open("wb") as f:
        f.truncate(64 * 1024 * 1024)
    with pytest.raises(ValueError, match="quota"):
        seed_provider_graph_cache(owner, manifest, stage)
    assert len(list((tmp_path / "_vision_cache").iterdir())) == 1


def test_provider_cache_inspection_does_not_run_native_gpu(monkeypatch, tmp_path):
    manifest, _plan, stage, project, cfg = fixture(monkeypatch, tmp_path)
    owner = VisionGraphSources(project, cfg)
    monkeypatch.setattr(vision_graph_m14, "infer", lambda *_: pytest.fail("surprise ONNX call"))
    from framerudiments.vision_temporal_m14 import plan_preview

    report = plan_preview(owner, [0], quality=5)
    assert report["taps"][0]["provider"] == PROVIDER
    assert report["taps"][0]["cache_present_unverified"] is False
    seed_provider_graph_cache(owner, manifest, stage)
    result = plan_preview(owner, [0], quality=5)
    assert result["taps"][0]["cache_present_unverified"] is True
