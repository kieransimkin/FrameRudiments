"""M14.1: model-estimate admission, tensor, receipts and no-GPU safety contracts."""

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from framerudiments.vision_m14 import (
    REQUEST_SCHEMA, _postprocess, _execute_model, _preview, infer, inspect_request,
)
from framerudiments.vision_m14_cli import main as cli_main


def _sha(path):
    return sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, task="segmentation", pair=False):
    root = tmp_path / "inputs"
    root.mkdir(parents=True)
    model = root / "model.onnx"
    model.write_bytes(b"ONNX fixture bytes used only with injected test runtime")
    current = root / "current.png"
    Image.new("RGB", (16, 16), (63, 127, 191)).save(current)
    row = {"schema": REQUEST_SCHEMA, "task": task,
           "model": {"path": model.name, "sha256": _sha(model)},
           "current": {"path": current.name, "sha256": _sha(current)},
           "input_names": ["image"], "output_name": "prediction",
           "width": 16, "height": 16}
    if task == "optical_flow":
        previous = root / "previous.png"
        Image.new("RGB", (16, 16), (20, 40, 80)).save(previous)
        row["previous"] = {"path": previous.name, "sha256": _sha(previous)}
        if pair:
            row["input_names"] = ["previous", "current"]
    request = root / "request.json"
    request.write_text(json.dumps(row), encoding="utf-8")
    return request, row


def write_doc(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("task", ["segmentation", "relative_depth", "optical_flow"])
def test_request_contract_and_inspection_do_not_import_runtime(tmp_path, task):
    request, _ = fixture(tmp_path, task)
    spec = inspect_request(request)
    doc = spec.document()
    assert doc["task"] == task
    assert doc["provider"] == "CPUExecutionProvider"
    assert doc["native_geometry"] is False
    assert doc["model_estimated"] is True
    assert doc["shape"] == [16, 16]
    assert "file" not in str(doc).lower()


def test_cli_inspection_no_model_inference(tmp_path, capsys):
    request, _ = fixture(tmp_path)
    assert cli_main(["inspect", str(request)]) == 0
    assert json.loads(capsys.readouterr().out)["model_estimated"]


def test_duplicate_fields_rejected(tmp_path):
    request, _ = fixture(tmp_path)
    request.write_text(request.read_text().replace('"task":', '"task":"segmentation", "task":'))
    with pytest.raises(ValueError, match="Duplicate"):
        inspect_request(request)


@pytest.mark.parametrize("field,value", [
    ("task", "face_detection"), ("width", 0), ("height", 1200),
    ("output_name", ""), ("input_names", []), ("input_names", ["image", "image"]),
    ("timeout_seconds", 999), ("activation", "bad"),
    ("preprocess", {"std": [1, 0, 1]}), ("extra", True),
])
def test_invalid_declarations_fail_before_loading_model(tmp_path, field, value):
    request, obj = fixture(tmp_path)
    obj[field] = value
    write_doc(request, obj)
    with pytest.raises(ValueError):
        inspect_request(request)


def test_flow_requires_previous_and_accepts_paired_inputs(tmp_path):
    request, obj = fixture(tmp_path, "optical_flow", pair=True)
    assert len(inspect_request(request).input_names) == 2
    del obj["previous"]
    write_doc(request, obj)
    with pytest.raises(ValueError, match="requires previous"):
        inspect_request(request)


def test_non_flow_rejects_previous(tmp_path):
    request, obj = fixture(tmp_path)
    previous = Path(request.parent / "current.png")
    obj["previous"] = {"path": previous.name, "sha256": _sha(previous)}
    write_doc(request, obj)
    with pytest.raises(ValueError, match="other tasks forbid"):
        inspect_request(request)


def test_sha_mismatch_rejected(tmp_path):
    request, obj = fixture(tmp_path)
    obj["model"]["sha256"] = "0" * 64
    write_doc(request, obj)
    with pytest.raises(ValueError, match="SHA-256"):
        inspect_request(request)


@pytest.mark.parametrize("path", ["../escape.onnx", "/absolute.onnx", "C:/escape.onnx", "dir\\file.onnx"])
def test_path_traversal_and_absolute_rejected(tmp_path, path):
    request, obj = fixture(tmp_path)
    obj["model"]["path"] = path
    write_doc(request, obj)
    with pytest.raises(ValueError):
        inspect_request(request)


def test_outside_symlink_rejected(tmp_path):
    request, obj = fixture(tmp_path)
    other = tmp_path / "outside.onnx"
    other.write_bytes(b"secret")
    symlink = request.parent / "external.onnx"
    try:
        symlink.symlink_to(other)
    except (NotImplementedError, OSError):
        pytest.skip("Filesystem symlinks unavailable")
    obj["model"] = {"path": symlink.name, "sha256": _sha(other)}
    write_doc(request, obj)
    with pytest.raises(ValueError, match="outside"):
        inspect_request(request)


def test_rgb_png_is_required(tmp_path):
    request, obj = fixture(tmp_path)
    image_path = request.parent / "current.png"
    Image.new("RGBA", (16, 16)).save(image_path)
    obj["current"]["sha256"] = _sha(image_path)
    write_doc(request, obj)
    with pytest.raises(ValueError, match="RGB8"):
        inspect_request(request)


class Runtime:
    __version__ = "synthetic-ort-test"

    class SessionOptions:
        pass

    def __init__(self, outputs=None, providers=None, inputs=None, output_shape=None):
        self.outputs = outputs
        self.providers = providers or ["CPUExecutionProvider"]
        self.input_shapes = inputs
        self.output_shape = output_shape
        self.seen_feeds = None
        self.seen_options = None

    def InferenceSession(self, content, sess_options, providers):
        assert content.startswith(b"ONNX fixture")
        assert providers == ["CPUExecutionProvider"]
        self.seen_options = sess_options
        return self

    def disable_fallback(self):
        self.fallback_disabled = True

    def get_providers(self):
        return self.providers

    def get_inputs(self):
        names = self.input_shapes or [("image", [1, 3, 16, 16])]
        return [SimpleNamespace(name=n, shape=shape, type="tensor(float)") for n, shape in names]

    def get_outputs(self):
        return [SimpleNamespace(name="prediction", shape=self.output_shape or [1, 1, 16, 16],
                                type="tensor(float)")]

    def run(self, names, feeds):
        self.seen_feeds = feeds
        return [self.outputs if self.outputs is not None else np.full((1, 1, 16, 16), 0.4, np.float32)]


def test_injected_strict_cpu_runtime_and_pinned_bytes(tmp_path):
    request, _ = fixture(tmp_path)
    spec = inspect_request(request)
    rt = Runtime()
    arr, version = _execute_model(spec, runtime=rt)
    assert version == "synthetic-ort-test"
    assert rt.fallback_disabled and rt.seen_options.enable_cpu_mem_arena is False
    assert rt.seen_feeds["image"].shape == (1, 3, 16, 16)
    assert arr.shape == (16, 16, 1)
    assert float(arr[0, 0, 0]) == pytest.approx(.4)


def test_strict_cpu_provider_rejects_accidental_gpu(tmp_path):
    request, _ = fixture(tmp_path)
    rt = Runtime(providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    with pytest.raises(ValueError, match="Strict CPU"):
        _execute_model(inspect_request(request), runtime=rt)


def test_tensor_contract_wrong_name_or_dimensions_fail(tmp_path):
    request, _ = fixture(tmp_path)
    rt = Runtime(inputs=[("other_name", [1, 3, 16, 16])])
    with pytest.raises(ValueError, match="input names"):
        _execute_model(inspect_request(request), runtime=rt)
    rt = Runtime(output_shape=[1, 3, 16, 16])
    with pytest.raises(ValueError, match="output tensor"):
        _execute_model(inspect_request(request), runtime=rt)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.3, 1.3])
def test_probability_rejects_nonfinite_or_outside_unit_interval(tmp_path, value):
    request, _ = fixture(tmp_path)
    rt = Runtime(outputs=np.full((1, 1, 16, 16), value, np.float32))
    with pytest.raises(ValueError):
        _execute_model(inspect_request(request), runtime=rt)


def test_segmentation_logits_sigmoid_and_preview(tmp_path):
    request, obj = fixture(tmp_path)
    obj["activation"] = "sigmoid"
    write_doc(request, obj)
    spec = inspect_request(request)
    arr = _postprocess(np.zeros((1, 1, 16, 16), np.float32), spec, np)
    assert (arr == .5).all()
    preview, evidence = _preview(arr, spec)
    assert preview.getpixel((0, 0)) == 128
    assert evidence["mapping"].startswith("probability")


def test_flow_one_tensor_and_two_tensors_use_explicit_previous_first(tmp_path):
    request, _ = fixture(tmp_path, "optical_flow")
    rt = Runtime(inputs=[("image", [1, 6, 16, 16])], output_shape=[1, 2, 16, 16],
                 outputs=np.zeros((1, 2, 16, 16), np.float32))
    arr, _ = _execute_model(inspect_request(request), runtime=rt)
    assert arr.shape == (16, 16, 2)
    assert rt.seen_feeds["image"].shape == (1, 6, 16, 16)
    assert rt.seen_feeds["image"][0, 0, 0, 0] < rt.seen_feeds["image"][0, 3, 0, 0]


def test_depth_negative_and_oversized_flow_fail(tmp_path):
    request, _ = fixture(tmp_path, "relative_depth")
    rt = Runtime(outputs=np.full((1, 1, 16, 16), -1, np.float32))
    with pytest.raises(ValueError, match="Relative depth"):
        _execute_model(inspect_request(request), runtime=rt)
    request2, _ = fixture(tmp_path / "second", "optical_flow")
    rt2 = Runtime(inputs=[("image", [1, 6, 16, 16])], output_shape=[1, 2, 16, 16],
                  outputs=np.full((1, 2, 16, 16), 5000, np.float32))
    with pytest.raises(ValueError, match="Optical flow"):
        _execute_model(inspect_request(request2), runtime=rt2)


def test_no_overwrite_of_inference_result(tmp_path):
    request, _ = fixture(tmp_path)
    output = tmp_path / "already-here"
    output.mkdir()
    with pytest.raises(ValueError, match="new directory"):
        infer(request, output)


def test_cli_invalid_request_reports_failure_not_success(tmp_path, capsys):
    request, obj = fixture(tmp_path)
    obj["model"]["sha256"] = "0" * 64
    write_doc(request, obj)
    assert cli_main(["infer", str(request), "--output", str(tmp_path / "new")]) == 2
    assert "SHA-256" in capsys.readouterr().err


def test_worker_timeout_is_reported_and_staging_removed(tmp_path, monkeypatch):
    import subprocess
    import framerudiments.vision_m14 as vision

    request, _ = fixture(tmp_path)
    output = tmp_path / "deadline-output"

    def pretend_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(vision.subprocess, "run", pretend_timeout)
    with pytest.raises(TimeoutError, match="deadline"):
        infer(request, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".deadline-output-*"))


def test_worker_corrupt_result_is_rejected_before_publication(tmp_path, monkeypatch):
    import framerudiments.vision_m14 as vision

    request, _ = fixture(tmp_path)
    output = tmp_path / "untrusted-result"

    def pretend_worker(args, **kwargs):
        directory = Path(args[-1])
        (directory / "estimate.npy").write_bytes(b"invalid")
        (directory / "preview.png").write_bytes(b"invalid")
        (directory / "receipt.json").write_text('{}')
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(vision.subprocess, "run", pretend_worker)
    with pytest.raises(ValueError, match="verification failed"):
        infer(request, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".untrusted-result-*"))


@pytest.mark.parametrize("task,channels", [
    ("segmentation", 1), ("relative_depth", 1), ("optical_flow", 2),
])
def test_successful_worker_receipt_is_independently_verified(tmp_path, monkeypatch, task, channels):
    import framerudiments.vision_m14 as vision

    request, _ = fixture(tmp_path, task)
    output = tmp_path / "published"
    manifest_before = request.read_bytes()
    image_before = (request.parent / "current.png").read_bytes()

    def fake_inference(spec):
        value = np.full((spec.height, spec.width, channels), .5, np.float32)
        return value, "synthetic-test-runtime"

    def inline_worker(args, **_kwargs):
        vision._worker(args[-2], Path(args[-1]))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(vision, "_execute_model", fake_inference)
    monkeypatch.setattr(vision.subprocess, "run", inline_worker)
    data = infer(request, output)
    assert data["tensor_shape"] == [16, 16, channels]
    assert data["runtime_version"] == "synthetic-test-runtime"
    assert data["inputs_sha256"]["current"] == _sha(request.parent / "current.png")
    assert _sha(output / "estimate.npy") == data["estimate_sha256"]
    assert _sha(output / "preview.png") == data["preview_sha256"]
    assert (output / "receipt.json").is_file()
    assert (request.parent / "current.png").read_bytes() == image_before
    assert request.read_bytes() == manifest_before
