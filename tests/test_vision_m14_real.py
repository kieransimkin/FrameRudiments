"""Native ONNX Runtime acceptance: models are generated locally, never downloaded.

CI MUST install onnx + onnxruntime and run these tests without skipped cases.
These minimal operators certify the I/O contract, not useful AI accuracy.
"""

from hashlib import sha256
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from framerudiments.vision_m14 import REQUEST_SCHEMA, infer, inspect_request

onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")


def pinned(path):
    return {"path": path.name, "sha256": sha256(path.read_bytes()).hexdigest()}


def create_model(path, task, *, pair=True):
    from onnx import TensorProto, helper, numpy_helper

    shape = [1, 3, 16, 16]
    nodes = []
    values = []
    if task != "optical_flow":
        inputs = [helper.make_tensor_value_info("image", TensorProto.FLOAT, shape)]
        nodes.append(helper.make_node("ReduceMean", ["image"], ["prediction"], axes=[1], keepdims=1))
        output_shape = [1, 1, 16, 16]
    else:
        assert pair
        inputs = [helper.make_tensor_value_info("previous", TensorProto.FLOAT, shape),
                  helper.make_tensor_value_info("current", TensorProto.FLOAT, shape)]
        nodes.extend([
            helper.make_node("Sub", ["current", "previous"], ["difference"]),
            helper.make_node("Slice", ["difference", "starts", "ends", "axes", "steps"], ["first"]),
            helper.make_node("Concat", ["first", "first"], ["prediction"], axis=1),
        ])
        for name, value in (("starts", [0]), ("ends", [1]), ("axes", [1]), ("steps", [1])):
            values.append(numpy_helper.from_array(np.array(value, dtype=np.int64), name=name))
        output_shape = [1, 2, 16, 16]
    output = helper.make_tensor_value_info("prediction", TensorProto.FLOAT, output_shape)
    graph = helper.make_graph(nodes, "m14-structural-synthetic-test", inputs, [output], initializer=values)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 9
    path.write_bytes(model.SerializeToString())


def request(tmp_path, task):
    root = tmp_path / "project"
    root.mkdir()
    model = root / "fixture.onnx"
    create_model(model, task)
    current = root / "current.png"
    Image.new("RGB", (16, 16), (70, 130, 180)).save(current)
    document = {"schema": REQUEST_SCHEMA, "task": task, "model": pinned(model),
                "current": pinned(current), "input_names": ["image"],
                "output_name": "prediction", "width": 16, "height": 16, "timeout_seconds": 35}
    if task == "optical_flow":
        previous = root / "previous.png"
        Image.new("RGB", (16, 16), (10, 20, 60)).save(previous)
        document["previous"] = pinned(previous)
        document["input_names"] = ["previous", "current"]
    manifest = root / "request.json"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return manifest, current


@pytest.mark.parametrize("task", ["segmentation", "relative_depth", "optical_flow"])
def test_real_cpu_model_worker_outputs_verified_files(tmp_path, task):
    manifest, _ = request(tmp_path, task)
    output = tmp_path / "result"
    # Editable installs expose the package; source-tree pytest environments
    # may need PYTHONPATH to carry the current src/ layout into the worker.
    root = Path(__file__).resolve().parents[1]
    os.environ["PYTHONPATH"] = str(root / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
    report = infer(manifest, output)
    expected_channels = 2 if task == "optical_flow" else 1
    assert report["tensor_shape"] == [16, 16, expected_channels]
    assert report["model_estimated"] and report["native_geometry"] is False
    assert report["provider"] == "CPUExecutionProvider"
    assert report["runtime_version"]
    arr = np.load(output / "estimate.npy", allow_pickle=False)
    assert arr.shape == (16, 16, expected_channels)
    assert np.isfinite(arr).all()
    if task == "segmentation":
        assert arr.min() >= 0 and arr.max() <= 1
    elif task == "relative_depth":
        assert (arr >= 0).all()
    else:
        assert (arr > 0).all()
        assert np.allclose(arr[:, :, 0], arr[:, :, 1])
    with Image.open(output / "preview.png") as image:
        assert image.size == (16, 16)
    assert json.loads((output / "receipt.json").read_text()) == report
    assert inspect_request(manifest).manifest_sha256 == report["manifest_sha256"]
    with pytest.raises(ValueError, match="new directory"):
        infer(manifest, output)


def test_invalid_onnx_rolls_back_temp_work(tmp_path):
    manifest, current = request(tmp_path, "segmentation")
    model = manifest.parent / "fixture.onnx"
    model.write_bytes(b"not a valid ONNX model")
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["model"] = pinned(model)
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "should-not-appear"
    with pytest.raises(RuntimeError, match="worker failed"):
        infer(manifest, out)
    assert not out.exists()
    assert not list(tmp_path.glob(".should-not-appear-*"))
    assert current.exists()


def test_native_runtime_cli_smoke(tmp_path):
    from framerudiments.vision_m14_cli import main

    manifest, _ = request(tmp_path, "relative_depth")
    assert main(["inspect", str(manifest)]) == 0
    assert main(["infer", str(manifest), "--output", str(tmp_path / "output")]) == 0
    assert (tmp_path / "output" / "receipt.json").is_file()
