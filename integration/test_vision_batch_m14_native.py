"""M14.3 native ONNX Runtime CPU qualification. CI installs onnx and onnxruntime."""
from hashlib import sha256
import json

import numpy as np
import pytest
from PIL import Image

from framerudiments.vision_batch_m14 import CPU_PROVIDER, SCHEMA, infer_batch

onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
TensorProto, helper = onnx.TensorProto, onnx.helper


def _sha(path):
    return sha256(path.read_bytes()).hexdigest()


def native_fixture(tmp_path, *, dynamic, task="segmentation", provider=CPU_PROVIDER):
    n = "B" if dynamic else 1
    if task == "segmentation":
        ins = [helper.make_tensor_value_info("input", TensorProto.FLOAT, [n, 3, 16, 16])]
        nodes = [helper.make_node("ReduceMean", ["input"], ["out"], axes=[1], keepdims=1)]
        initializers = []
        channels = 1
    else:
        ins = [helper.make_tensor_value_info(name, TensorProto.FLOAT, [n, 3, 16, 16])
               for name in ("previous", "current")]
        nodes = [helper.make_node("Sub", ["current", "previous"], ["difference"]),
                 helper.make_node("Gather", ["difference", "channel_indices"], ["out"], axis=1)]
        initializers = [helper.make_tensor("channel_indices", TensorProto.INT64, [2], [0, 1])]
        channels = 2
    output = helper.make_tensor_value_info("out", TensorProto.FLOAT, [n, channels, 16, 16])
    graph = helper.make_graph(nodes, "m14-3-test", ins, [output], initializer=initializers)
    model = helper.make_model(graph, producer_name="framerudiments-tests",
                              opset_imports=[helper.make_operatorsetid("", 13)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    onnx.save(model, str(tmp_path / "model.onnx"))
    items = []
    for frame in range(3):
        current = tmp_path / f"frame-{frame}.png"
        Image.new("RGB", (16, 16), (40 + frame * 20, 70, 100)).save(current)
        doc = {"schema": "framerudiments.vision-request.v1", "task": task,
               "model": {"path": "model.onnx", "sha256": _sha(tmp_path / "model.onnx")},
               "current": {"path": current.name, "sha256": _sha(current)},
               "input_names": [i.name for i in ins], "output_name": "out", "width": 16, "height": 16}
        if task == "optical_flow":
            prior = tmp_path / f"prior-{frame}.png"
            Image.new("RGB", (16, 16), (30, 65, 110)).save(prior)
            doc["previous"] = {"path": prior.name, "sha256": _sha(prior)}
        request = tmp_path / f"request-{frame}.json"
        request.write_text(json.dumps(doc))
        items.append({"frame": frame, "request": {"path": request.name, "sha256": _sha(request)}})
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({"schema": SCHEMA, "provider": provider, "batch_size": 2,
                                 "items": items, "timeout_seconds": 120}), encoding="utf-8")
    return batch


@pytest.mark.parametrize("dynamic", [False, True])
def test_actual_native_cpu_shared_session_and_dynamic_batch(tmp_path, dynamic):
    batch = native_fixture(tmp_path, dynamic=dynamic)
    result = infer_batch(batch, tmp_path / "result")
    assert result["provider"] == CPU_PROVIDER
    assert result["execution"]["model_sessions"] == 1
    assert result["execution"]["provider_node_events"] >= 2
    assert result["execution"]["batches"] == ([2, 1] if dynamic else [1, 1, 1])
    assert result["execution"]["execution_mode"] == ("dynamic_batch" if dynamic else "shared_session_single")
    expected = []
    for frame in range(3):
        arr = np.load(tmp_path / "result" / "frames" / f"{frame:08d}" / "estimate.npy", allow_pickle=False)
        assert arr.shape == (16, 16, 1)
        assert np.isfinite(arr).all()
        expected.append(float(arr[0, 0, 0]))
    assert expected[0] < expected[1] < expected[2]
    with pytest.raises(ValueError, match="new directory"):
        infer_batch(batch, tmp_path / "result")


def test_actual_native_paired_flow_in_one_dynamic_batch(tmp_path):
    batch = native_fixture(tmp_path, dynamic=True, task="optical_flow")
    result = infer_batch(batch, tmp_path / "flow")
    assert result["execution"]["batches"] == [2, 1]
    for frame in range(3):
        array = np.load(tmp_path / "flow" / "frames" / f"{frame:08d}" / "estimate.npy", allow_pickle=False)
        assert array.shape == (16, 16, 2)
        assert np.isfinite(array).all()
        assert array[:, :, 0].mean() > 0
