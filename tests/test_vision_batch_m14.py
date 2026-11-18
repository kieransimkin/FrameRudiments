"""M14.3 provider policy, real batching shape contracts, rollback and element masks."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from framerudiments.effect_graph_integration import evaluate_effect_graph
from framerudiments.vision_batch_m14 import (
    CPU_PROVIDER, SCHEMA, _run_provider, _verify, infer_batch, inspect_batch,
)
from framerudiments.vision_graph_m14 import VisionGraphSources
from test_vision_graph_m14 import inputs, mocked_infer


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def batch_fixture(tmp_path, *, count=3, provider=CPU_PROVIDER, batch_size=4):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"synthetic pinned model - fake runtime only")
    rows = []
    for frame in range(count):
        image = tmp_path / f"image-{frame}.png"
        Image.new("RGB", (16, 16), (frame * 30, 64, 128)).save(image)
        request = {
            "schema": "framerudiments.vision-request.v1", "task": "segmentation",
            "model": {"path": model.name, "sha256": digest(model)},
            "current": {"path": image.name, "sha256": digest(image)},
            "input_names": ["input"], "output_name": "out", "width": 16, "height": 16,
        }
        path = tmp_path / f"request-{frame}.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        rows.append({"frame": frame, "request": {"path": path.name, "sha256": digest(path)}})
    manifest = tmp_path / "batch.json"
    manifest.write_text(json.dumps({"schema": SCHEMA, "provider": provider,
                                    "batch_size": batch_size, "items": rows}), encoding="utf-8")
    return manifest


def test_strict_manifest_and_frame_order(tmp_path):
    manifest = batch_fixture(tmp_path)
    plan = inspect_batch(manifest)
    assert plan.provider == CPU_PROVIDER
    assert [x.frame for x in plan.items] == [0, 1, 2]
    assert plan.document()["model_estimated"] is True
    assert plan.document()["gpu_parity_required"] is False
    assert plan.items[0].spec.width == 16


@pytest.mark.parametrize("changed", [
    {"provider": "auto"}, {"batch_size": 0}, {"batch_size": True},
    {"batch_size": 9}, {"timeout_seconds": 1}, {"gpu_mem_limit_mib": -10},
    {"parity": {"max_abs": 1}}, {"parity": {"mean_abs": float("nan")}},
    {"items": []}, {"unexpected": 1}, {"schema": "broken"},
])
def test_admission_fails_closed(tmp_path, changed):
    manifest = batch_fixture(tmp_path)
    doc = json.loads(manifest.read_text())
    doc.update(changed)
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        inspect_batch(manifest)


def test_duplicate_frame_and_changed_manifest_and_invalid_path(tmp_path):
    manifest = batch_fixture(tmp_path)
    doc = json.loads(manifest.read_text())
    doc["items"].append(doc["items"][0].copy())
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="unique"):
        inspect_batch(manifest)
    doc["items"] = doc["items"][:1]
    doc["items"][0]["request"]["path"] = "../outside.json"
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        inspect_batch(manifest)
    doc["items"][0]["request"]["path"] = "C:/outside.json"
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        inspect_batch(manifest)
    doc["items"][0]["request"]["path"] = "request-0.json"
    doc["items"][0]["request"]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        inspect_batch(manifest)


class FakeRuntime:
    __version__ = "fake-ort-1"
    ExecutionMode = SimpleNamespace(ORT_SEQUENTIAL=0)

    def __init__(self, *, dynamic=True, providers=(CPU_PROVIDER,), assignments=None):
        self.dynamic = dynamic
        self.providers = list(providers)
        self.assignments = assignments
        self.sessions = 0
        self.runs = []

    def get_available_providers(self):
        return [CPU_PROVIDER, "CUDAExecutionProvider", "DmlExecutionProvider"]

    class SessionOptions:
        def add_session_config_entry(self, name, value):
            if name != "session.disable_cpu_ep_fallback" or value != "1":
                raise ValueError("Incorrect provider fallback contract")

    def InferenceSession(self, model, *, sess_options, providers):
        self.sessions += 1
        runtime = self
        assert model.startswith(b"synthetic")
        provider = providers[0][0] if isinstance(providers[0], tuple) else providers[0]

        class Session:
            def disable_fallback(self):
                pass

            def get_providers(self):
                return runtime.providers

            def get_inputs(self):
                return [SimpleNamespace(name="input", type="tensor(float)",
                                        shape=["batch" if runtime.dynamic else 1, 3, 16, 16])]

            def get_outputs(self):
                return [SimpleNamespace(name="out", type="tensor(float)",
                                        shape=["batch" if runtime.dynamic else 1, 1, 16, 16])]

            def run(self, outputs, feeds):
                runtime.runs.append(feeds["input"].shape[0])
                return [feeds["input"].mean(axis=1, keepdims=True).astype(np.float32)]

            def end_profiling(self):
                trace = Path(sess_options.profile_file_prefix + ".json")
                trace.write_text(json.dumps([{"cat": "Node", "args": {"provider": p}}
                                             for p in runtime.assignments or [provider]]))
                return str(trace)
        return Session()


def test_dynamic_batch_reuses_one_actual_session(tmp_path):
    plan = inspect_batch(batch_fixture(tmp_path, count=5, batch_size=3))
    runtime = FakeRuntime(dynamic=True)
    out, report = _run_provider(plan, CPU_PROVIDER, tmp_path, runtime=runtime)
    assert runtime.sessions == 1 and runtime.runs == [3, 2]
    assert report["execution_mode"] == "dynamic_batch"
    assert report["batches"] == [3, 2] and report["model_sessions"] == 1
    assert list(out) == [0, 1, 2, 3, 4]
    assert out[0].shape == (16, 16, 1)


def test_static_batch_uses_one_session_but_reports_nonbatched(tmp_path):
    plan = inspect_batch(batch_fixture(tmp_path, count=4))
    runtime = FakeRuntime(dynamic=False)
    _, report = _run_provider(plan, CPU_PROVIDER, tmp_path, runtime=runtime)
    assert runtime.sessions == 1 and runtime.runs == [1, 1, 1, 1]
    assert report["execution_mode"] == "shared_session_single"


def test_requested_provider_must_be_attached_without_cpu_fallback(tmp_path):
    plan = inspect_batch(batch_fixture(tmp_path, count=1, provider="CUDAExecutionProvider"))
    with pytest.raises(RuntimeError, match="provider fallback"):
        _run_provider(plan, "CUDAExecutionProvider", tmp_path,
                      runtime=FakeRuntime(providers=["CUDAExecutionProvider", CPU_PROVIDER]))
    with pytest.raises(RuntimeError, match="graph assignment"):
        _run_provider(plan, "CUDAExecutionProvider", tmp_path,
                      runtime=FakeRuntime(providers=["CUDAExecutionProvider"],
                                          assignments=[CPU_PROVIDER]))


def test_missing_provider_rejected_not_silently_using_cpu(tmp_path):
    plan = inspect_batch(batch_fixture(tmp_path, count=1))
    runtime = FakeRuntime()
    runtime.get_available_providers = lambda: [CPU_PROVIDER]
    with pytest.raises(RuntimeError, match="unavailable"):
        _run_provider(plan, "CUDAExecutionProvider", tmp_path, runtime=runtime)


def test_gpu_cpu_comparison_and_digest_receipts(tmp_path, monkeypatch):
    from framerudiments import vision_batch_m14

    manifest = batch_fixture(tmp_path, count=2, provider="CUDAExecutionProvider")
    plan = inspect_batch(manifest)
    calls = []

    def fake_run(p, provider, stage, **kwargs):
        calls.append(provider)
        arrays = {x.frame: np.full((16, 16, 1), .5 if provider == CPU_PROVIDER else .5001,
                                   dtype=np.float32) for x in p.items}
        return arrays, {"provider": provider, "batches": [2], "model_sessions": 1}

    monkeypatch.setattr(vision_batch_m14, "_run_provider", fake_run)
    stage = tmp_path / "stage"
    stage.mkdir()
    vision_batch_m14._worker(manifest, stage)
    report = _verify(stage, plan)
    assert calls == ["CUDAExecutionProvider", CPU_PROVIDER]
    assert report["numeric_parity_passed"] is True
    assert report["frames"][0]["frame"] == 0
    assert (stage / "frames/00000000/estimate.npy").is_file()


def test_gpu_parity_failure_does_not_pretend_success(tmp_path, monkeypatch):
    from framerudiments import vision_batch_m14

    manifest = batch_fixture(tmp_path, provider="CUDAExecutionProvider")

    def fake_run(plan, provider, stage, **kwargs):
        value = .5 if provider == CPU_PROVIDER else .9
        return ({x.frame: np.full((16, 16, 1), value, np.float32) for x in plan.items},
                {"provider": provider, "batches": [len(plan.items)]})

    monkeypatch.setattr(vision_batch_m14, "_run_provider", fake_run)
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ValueError, match="parity"):
        vision_batch_m14._worker(manifest, stage)
    assert not (stage / "batch.json").exists()


def test_worker_timeout_and_partial_results_are_not_published(tmp_path, monkeypatch):
    from framerudiments import vision_batch_m14

    manifest = batch_fixture(tmp_path)

    def fail(*args, **kwargs):
        target = Path(args[0][-1])
        (target / "partial.txt").write_text("untrusted partial")
        raise subprocess.TimeoutExpired(args[0], 2)

    import subprocess

    monkeypatch.setattr(vision_batch_m14.subprocess, "run", fail)
    with pytest.raises(TimeoutError):
        infer_batch(manifest, tmp_path / "not-published")
    assert not (tmp_path / "not-published").exists()
    assert not list(tmp_path.glob(".not-published-*"))


def test_element_mask_multiplies_only_declared_graph_layer(monkeypatch, tmp_path):
    project, config = inputs(tmp_path)
    project.data = {"layers": [{"id": "portrait"}]}
    config["element_masks"] = [{"element": "layer:portrait", "source": "vision:foreground"}]
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    owner = VisionGraphSources(project, config)
    assert owner.element_masks == {"layer:portrait": "vision:foreground"}
    original = Image.new("RGBA", (16, 16), (60, 100, 220, 200))
    other = Image.new("RGBA", (16, 16), (30, 60, 90, 255))
    result = owner.mask_elements({"layer:portrait": original, "layer:other": other}, 0)
    assert result["layer:portrait"].getpixel((0, 0)) == (60, 100, 220, 100)
    assert result["layer:portrait"] is not original
    assert original.getpixel((0, 0))[3] == 200
    assert result["layer:other"] is other

    class DummyGraph:
        source_ids = frozenset(("layer:portrait",))
        layer_style_ids = frozenset()

        def render(self, sources, **kwargs):
            return sources["layer:portrait"].copy()

    frame = Image.new("RGBA", (16, 16), (0, 0, 0, 255))
    result = evaluate_effect_graph(DummyGraph(), None, SimpleNamespace(frame=0), frame,
                                   {"layer:portrait": original}, vision_sources=owner)
    assert result.getpixel((0, 0))[3] == 100
    assert frame.getpixel((0, 0))[3] == 255


@pytest.mark.parametrize("binding", [
    {"element": "layer:unknown", "source": "vision:foreground"},
    {"element": "layer:portrait", "source": "vision:missing"},
    {"element": "frame", "source": "vision:foreground"},
])
def test_invalid_element_masks_fail_at_preflight(tmp_path, binding):
    project, config = inputs(tmp_path)
    project.data = {"layers": [{"id": "portrait"}]}
    config["element_masks"] = [binding]
    with pytest.raises(ValueError, match="element mask"):
        VisionGraphSources(project, config)


def test_element_mask_rejects_model_relative_depth(tmp_path):
    project, config = inputs(tmp_path, task="relative_depth")
    project.data = {"layers": [{"id": "portrait"}]}
    config["element_masks"] = [{"element": "layer:portrait", "source": "vision:foreground"}]
    with pytest.raises(ValueError, match="segmentation"):
        VisionGraphSources(project, config)


def test_verified_cpu_batch_seeds_exact_graph_cache_without_reinference(monkeypatch, tmp_path):
    from framerudiments import vision_batch_m14, vision_graph_m14

    project, config = inputs(tmp_path)
    project.verify_inputs = lambda: None
    owner = VisionGraphSources(project, config)
    request = tmp_path / "request-0.json"
    batch_manifest = tmp_path / "manifest-batch.json"
    batch_manifest.write_text(json.dumps({"schema": SCHEMA, "provider": CPU_PROVIDER,
                                          "items": [{"frame": 0, "request": {
                                              "path": request.name, "sha256": digest(request)}}]}))
    plan = inspect_batch(batch_manifest)

    def fake_run(_plan, _provider, _stage, **kwargs):
        return ({0: np.full((16, 16, 1), 0.5, dtype=np.float32)},
                {"provider": CPU_PROVIDER, "runtime_version": "not-installed",
                 "model_sessions": 1, "batches": [1]})

    monkeypatch.setattr(vision_batch_m14, "_run_provider", fake_run)
    stage = tmp_path / "native-results"
    stage.mkdir()
    vision_batch_m14._worker(batch_manifest, stage)
    _verify(stage, plan)
    monkeypatch.setattr(vision_graph_m14, "_onnx_runtime_version", lambda: "not-installed")
    result = vision_batch_m14.seed_graph_cache(owner, batch_manifest, stage)
    assert result["new_entries"] == 1

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("Batch cache must satisfy graph without a new model session")

    monkeypatch.setattr(vision_graph_m14, "infer", must_not_run)
    assert owner.source("vision:foreground", 0).getpixel((0, 0))[3] == 128
    assert vision_batch_m14.seed_graph_cache(owner, batch_manifest, stage)["existing_entries"] == 1


def test_gpu_batch_cannot_seed_cpu_graph_cache(tmp_path):
    project, config = inputs(tmp_path)
    project.verify_inputs = lambda: None
    owner = VisionGraphSources(project, config)
    request = tmp_path / "request-0.json"
    batch_manifest = tmp_path / "manifest-batch.json"
    batch_manifest.write_text(json.dumps({"schema": SCHEMA, "provider": "CUDAExecutionProvider",
                                          "items": [{"frame": 0, "request": {
                                              "path": request.name, "sha256": digest(request)}}]}))
    with pytest.raises(ValueError, match="GPU estimates cannot seed"):
        from framerudiments.vision_batch_m14 import seed_graph_cache
        seed_graph_cache(owner, batch_manifest, tmp_path)
