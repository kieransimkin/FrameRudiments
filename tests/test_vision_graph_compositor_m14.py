"""Actual native Project/Compositor M14.2 vision-source integration.

No real ONNX is required here: worker provenance and CPU ONNX correctness are
qualified by M14.1. This checks the native effect DAG and cache wiring.
"""
from hashlib import sha256
from copy import deepcopy
import wave

import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from test_vision_graph_m14 import inputs, mocked_infer


def ref(path):
    return {"path": path.name, "sha256": sha256(path.read_bytes()).hexdigest()}


def prepare(tmp_path, *, vision=True):
    with wave.open(str(tmp_path / "source.wav"), "wb") as wav:
        wav.setparams((1, 2, 10000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 10000)
    native = {
        "schema": "framerudiments.project.v1", "width": 16, "height": 16,
        "fps": 10, "fps_reason": "Exact M14.2 test fixtures", "purpose": "visualiser",
        "audio": {"source": ref(tmp_path / "source.wav"), "sample_rate": 10000,
                  "start_sample": 0, "end_sample": 10000, "fade_seconds": 0},
        "clock": {"authority": "synthetic M14.2", "status": "synthetic", "bpm": 120},
        "background": {"colour": "#11243a"}, "render_quality": {"enabled": False},
        "effect_graph": {"nodes": [{"id": "vision:foreground", "type": "source"}],
                         "output": "vision:foreground"},
    }
    if vision:
        _, config = inputs(tmp_path)
        config["sources"][0]["missing"] = "transparent"
        native["vision_sources"] = config
    return Project(native, tmp_path)


def test_actual_compositor_frame_injects_pinned_mask_and_retains_seek_purity(monkeypatch, tmp_path):
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    project = prepare(tmp_path)
    comp = Compositor(project)
    try:
        first, context = comp.frame(0, apply_quality=False)
        assert first.mode == "RGB" and first.size == (16, 16)
        assert first.getpixel((0, 0)) == (128, 128, 128)
        missing, _ = comp.frame(1, apply_quality=False)
        assert missing.getpixel((0, 0)) == (0, 0, 0)
        assert comp.frame(0, apply_quality=False)[0].tobytes() == first.tobytes()
        assert comp.evidence()["vision_sources"]["model_estimated"] is True
        assert context.frame == 0
        project.verify_inputs()
    finally:
        comp.close()


def test_unknown_vision_source_rejected_at_project_compositor_admission(tmp_path):
    project = prepare(tmp_path, vision=False)
    with pytest.raises(ValueError, match="Unknown effect graph source"):
        Compositor(project)


def test_graph_estimate_accepts_exact_temporal_samples(monkeypatch, tmp_path):
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    project = prepare(tmp_path)
    data = deepcopy(project.data)
    data["render_quality"] = {"enabled": True, "spatial_scale": 1, "temporal_samples": 2}
    comp = Compositor(Project(data, tmp_path))
    try:
        image, _ = comp.frame(0)
        assert image.mode == "RGB" and image.size == (16, 16)
        assert image.getpixel((0, 0)) == (128, 128, 128)
        assert comp.frame(0)[0].tobytes() == image.tobytes()
    finally:
        comp.close()


def test_bad_vision_source_rejected_before_native_pixels(tmp_path, monkeypatch):
    project = prepare(tmp_path)
    spec = deepcopy(project.data)
    spec["vision_sources"]["sources"][0]["frames"][0]["request"]["sha256"] = "f" * 64
    with pytest.raises(ValueError):
        Compositor(Project(spec, tmp_path))


def test_command_warms_exact_graph_frames_without_rendering(tmp_path, monkeypatch, capsys):
    import json

    from framerudiments.vision_m14_cli import main

    project = prepare(tmp_path)
    doc = tmp_path / "project.json"
    doc.write_text(json.dumps(project.data), encoding="utf-8")
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    assert main(["warm-graph", str(doc), "--frame", "0", "--source", "vision:foreground"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["schema"] == "framerudiments.vision-graph-warm.v1"
    assert receipt["sources"][0]["estimate"]["model_estimated"] is True
    assert receipt["sources"][0]["frame"] == 0
    assert main(["warm-graph", str(doc), "--frame", "0", "--source", "vision:invalid"]) == 2
