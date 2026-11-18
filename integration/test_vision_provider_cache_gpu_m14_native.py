"""Real physical-provider batch -> independently sealed graph cache acceptance.

This is manual GPU-runner-only and must not pass as software CPU/GPU evidence.
"""
from hashlib import sha256
import json
import os
from pathlib import Path

import numpy as np

from framerudiments.vision_batch_m14 import GPU_PROVIDERS, infer_batch, inspect_batch
from framerudiments.vision_graph_m14 import PROFILE_SCHEMA, SCHEMA, VisionGraphSources
from framerudiments.vision_provider_cache_m14 import seed_provider_graph_cache
from test_vision_batch_m14_native import native_fixture


def _hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


class _Project:
    def __init__(self, base):
        self.base = base
        self.width = self.height = 16
        self.frame_count = 8
        self.inputs = []
        self.data = {"layers": []}

    def ref(self, row):
        resolved = (self.base / row["path"]).resolve(strict=True)
        if not resolved.is_relative_to(self.base) or _hash(resolved) != row["sha256"]:
            raise ValueError("Native provider import pins differ")
        ref = {"path": str(resolved), "sha256": row["sha256"]}
        self.inputs.append(ref)
        return ref

    def verify_inputs(self):
        if any(_hash(entry["path"]) != entry["sha256"] for entry in self.inputs):
            raise ValueError("Native provider import input changed")


def test_gpu_batch_can_be_consumed_offline_without_cpu_fallback(tmp_path):
    provider = os.environ.get("FRAMERUDIMENTS_M14_GPU_PROVIDER")
    assert provider in GPU_PROVIDERS, "A selected physical ONNX execution provider is required"
    batch = native_fixture(tmp_path, dynamic=True, provider=provider)
    receipt = infer_batch(batch, tmp_path / "provider-result")
    assert receipt["execution"]["provider_node_events"] > 0
    assert receipt["cpu_reference"]["provider"] == "CPUExecutionProvider"
    plan = inspect_batch(batch)
    first = plan.items[0].spec
    profile = {"schema": PROFILE_SCHEMA, "id": "primary", "task": first.task,
               "model_sha256": first.input_hashes["model"], "width": first.width,
               "height": first.height, "input_names": list(first.input_names),
               "output_name": first.output_name}
    frame_rows = [{"frame": item.frame, "request": {"path": item.spec.manifest.name,
                   "sha256": _hash(item.spec.manifest)}} for item in plan.items]
    batch_sha = _hash(tmp_path / "provider-result" / "batch.json")
    config = {"schema": SCHEMA, "cache_dir": "cache", "profiles": [profile],
              "sources": [{"id": "foreground", "profile": "primary",
                           "frames": frame_rows,
                           "provider": {"name": provider, "batch_sha256": batch_sha}}]}
    owner = VisionGraphSources(_Project(tmp_path), config)
    imported = seed_provider_graph_cache(owner, batch, tmp_path / "provider-result")
    assert imported["numeric_parity_passed"] is True
    assert imported["new_entries"] == 3
    assert imported["physical_device_certified"] is False
    for item in plan.items:
        preview = owner.source("vision:foreground", item.frame)
        array = np.load(tmp_path / "provider-result" / "frames" / f"{item.frame:08d}" /
                        "estimate.npy", allow_pickle=False)
        assert preview.mode == "RGBA" and preview.size == (16, 16)
        assert preview.getchannel("A").getpixel((0, 0)) == round(float(array[0, 0, 0]) * 255)
        assert owner.last_used["vision:foreground"]["provider"] == provider
    evidence = os.environ.get("FRAMERUDIMENTS_M14_GPU_EVIDENCE")
    if evidence:
        root = Path(evidence)
        root.mkdir(parents=True, exist_ok=True)
        (root / "provider-cache-import.json").write_text(json.dumps(imported, indent=2) + "\n")
