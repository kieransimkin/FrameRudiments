"""Manual-only real-device M14.3 GPU parity qualification; never run on CPU CI."""
import os
from pathlib import Path
import json
import shutil

import pytest

from framerudiments.vision_batch_m14 import GPU_PROVIDERS, infer_batch
from test_vision_batch_m14_native import native_fixture

runtime = pytest.importorskip("onnxruntime")


def test_real_selected_gpu_executes_every_operator_and_agrees_with_cpu(tmp_path):
    provider = os.environ.get("FRAMERUDIMENTS_M14_GPU_PROVIDER")
    assert provider in GPU_PROVIDERS, "Select an explicit GPU provider through the device workflow"
    assert provider in runtime.get_available_providers(), "The selected provider is not installed on this runner"
    manifest = native_fixture(tmp_path, dynamic=True, provider=provider)
    result = infer_batch(manifest, tmp_path / "gpu-result")
    assert result["provider"] == provider
    assert result["numeric_parity_passed"] is True
    assert result["execution"]["provider_node_events"] >= 2
    assert result["cpu_reference"]["provider"] == "CPUExecutionProvider"
    for row in result["frames"]:
        assert row["frame"] in (0, 1, 2)
    evidence_dir = os.environ.get("FRAMERUDIMENTS_M14_GPU_EVIDENCE")
    if evidence_dir:
        evidence = Path(evidence_dir)
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "provider-comparison.json").write_text(json.dumps(result, indent=2) + "\n")
        for frame in range(3):
            image = tmp_path / "gpu-result" / "frames" / f"{frame:08d}" / "preview.png"
            shutil.copyfile(image, evidence / f"frame-{frame:02d}.png")
