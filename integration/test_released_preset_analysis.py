"""End-to-end contract for the actual pinned wheels; no model downloads."""
import hashlib
import importlib.metadata
import json
from pathlib import Path

from PIL import Image, ImageDraw


def test_pinned_release_wheels_run_temporal_first_without_models(tmp_path):
    from prismmoves.preset_pipeline import analyse_capture_manifest

    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "analysis-releases.json").read_text(encoding="utf-8"))
    for package in lock["packages"]:
        assert importlib.metadata.version(package["name"]) == package["version"]
    frames = []
    for index in range(12):
        path = tmp_path / f"frame-{index}.png"
        image = Image.new("RGB", (96, 96), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((12 + index, 20, 38 + index, 57), fill="white")
        image.save(path)
        frames.append({"frame": index, "output_seconds": index / 15,
                       "output": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}})
    manifest = {"preset": {"id": "synthetic:pinned-release", "name": "Release contract", "engine": "synthetic"},
                "conditions": [{"id": "test", "state": "captured", "imagery": "grid-rings",
                                "audio_mode": "silence", "captures": frames}]}
    messages = []
    def forbidden_semantics(*args, **kwargs):
        raise AssertionError("A model-free contract must never request neural inference")
    report = analyse_capture_manifest(manifest, tmp_path / "report", models=(), budget=4,
                                      models_dir=tmp_path / "models", progress=messages.append,
                                      batch_analyser=forbidden_semantics)
    assert report["performance"]["measured_frames"] == 12
    assert report["performance"]["new_semantic_frames"] == 0
    assert report["description"]["summary"]
    assert (tmp_path / "report" / "temporal.json").is_file()
    assert (tmp_path / "report" / "semantic-plan.json").is_file()
    assert (tmp_path / "report" / "report.html").is_file()
    assert any("before semantic analysis" in message for message in messages)
