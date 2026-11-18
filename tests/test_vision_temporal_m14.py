"""M14.4 explicitly Q-bounded temporal taps; authored outputs remain unchanged."""
import json
from pathlib import Path

import pytest

from framerudiments.vision_graph_m14 import VisionGraphSources
from framerudiments.vision_temporal_m14 import plan_preview, warm_preview
from test_vision_graph_m14 import Project, inputs, mocked_infer


AGES = [4, 3, 2, 1, 0, 0]


def fixture(tmp_path, *, missing="error", preview=True):
    project, cfg = inputs(tmp_path)
    cfg["sources"][0]["missing"] = missing
    if preview:
        cfg["sources"][0]["preview_max_age_frames"] = AGES.copy()
    return project, cfg


def test_explicit_q0_past_reuse_and_q5_exact_contract(tmp_path):
    project, cfg = fixture(tmp_path)
    owner = VisionGraphSources(project, cfg)
    assert owner.selection("vision:foreground", 0, quality=5)[0] == 0
    assert owner.selection("vision:foreground", 4, quality=0)[:1] == (0,)
    assert owner.selection("vision:foreground", 2, quality=2)[0] == 0
    with pytest.raises(ValueError, match="Missing"):
        owner.selection("vision:foreground", 3, quality=2)
    with pytest.raises(ValueError, match="Missing"):
        owner.selection("vision:foreground", 1, quality=5)
    with pytest.raises(ValueError, match="Missing"):
        owner.selection("vision:foreground", 5, quality=0)
    with pytest.raises(ValueError, match="Missing"):
        owner.selection("vision:foreground", 1)


def test_authored_hold_unchanged_but_preview_q5_does_not_silently_hold(tmp_path):
    project, cfg = fixture(tmp_path, missing="hold")
    owner = VisionGraphSources(project, cfg)
    assert owner.selection("vision:foreground", 7)[2] == "authored_hold"
    with pytest.raises(ValueError, match="Missing"):
        owner.selection("vision:foreground", 1, quality=5)


def test_read_only_plan_reports_unique_taps_and_no_unverified_success(monkeypatch, tmp_path):
    owner = VisionGraphSources(*fixture(tmp_path))
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer",
                        lambda *_: pytest.fail("read-only plan ran ONNX"))
    report = plan_preview(owner, [4, 0, 1, 2, 3, 3], quality=0)
    assert report["frames"] == [0, 1, 2, 3, 4]
    assert report["unique_predictions"] == 1
    assert report["reused_taps"] == 4
    assert report["cache_presence_is_verification"] is False
    assert all(not row["cache_present_unverified"] for row in report["taps"])
    assert all(row["cache_key"] for row in report["taps"])
    assert report["authored_render_unchanged"] is True
    assert not (tmp_path / "_vision_cache").exists()


def test_warm_preview_deduplicates_predictions_and_proves_bytes(monkeypatch, tmp_path):
    owner = VisionGraphSources(*fixture(tmp_path))
    calls = []

    def run(manifest, output):
        calls.append(manifest)
        return mocked_infer(manifest, output)

    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", run)
    result = warm_preview(owner, [0, 1, 2, 3, 4], quality=0)
    assert len(calls) == 1
    assert result["unique_verified"] == 1
    assert result["entries"][0]["verified_read"] is True
    assert owner.source("vision:foreground", 3, quality=0).getpixel((0, 0))[3] == 128
    assert owner.last_used["vision:foreground"]["source_frame"] == 0
    assert len(calls) == 1
    assert plan_preview(owner, [3], quality=0)["taps"][0]["cache_present_unverified"] is True
    assert not (Path(tmp_path) / "_vision_cache" / "preview_quality").exists()


def test_transparent_missing_preview_never_runs_inference(monkeypatch, tmp_path):
    owner = VisionGraphSources(*fixture(tmp_path, missing="transparent"))
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer",
                        lambda *_: pytest.fail("transparent frame invoked worker"))
    assert owner.selection("vision:foreground", 7, quality=0) == (None, None, "transparent")
    assert owner.source("vision:foreground", 7, quality=0).getbbox() is None
    report = plan_preview(owner, [7], quality=0)
    assert report["unique_predictions"] == 0
    assert report["taps"][0]["cache_key"] is None


@pytest.mark.parametrize("ages", ([1], [4, 5, 1, 1, 0, 0], [4, 3, 2, 1, 1, 1],
                                  [True, 3, 2, 1, 0, 0], [3.5, 3, 2, 1, 0, 0],
                                  [-1, 0, 0, 0, 0, 0], [65, 0, 0, 0, 0, 0]))
def test_preview_age_contract_rejects_invalid_policies(tmp_path, ages):
    project, cfg = inputs(tmp_path)
    cfg["sources"][0]["preview_max_age_frames"] = ages
    with pytest.raises(ValueError, match="preview ages"):
        VisionGraphSources(project, cfg)


def test_no_cross_frame_optical_flow_preview_reuse(tmp_path):
    project, cfg = inputs(tmp_path, task="optical_flow", previous=True)
    cfg["sources"][0]["preview_max_age_frames"] = AGES.copy()
    with pytest.raises(ValueError, match="optical flow"):
        VisionGraphSources(project, cfg)
    cfg["sources"][0]["preview_max_age_frames"] = [0] * 6
    owner = VisionGraphSources(Project(tmp_path), cfg)
    assert owner.selection("vision:foreground", 0, quality=0)[0] == 0
    with pytest.raises(ValueError, match="Missing"):
        owner.selection("vision:foreground", 1, quality=0)


@pytest.mark.parametrize("quality", (None, -1, 6, True, 1.5, "q0"))
def test_preview_plan_requires_exact_quality(tmp_path, quality):
    owner = VisionGraphSources(*fixture(tmp_path))
    with pytest.raises(ValueError, match="quality"):
        plan_preview(owner, [0], quality=quality)


def test_preview_planner_validates_frame_bounds_source_names_and_size(tmp_path):
    owner = VisionGraphSources(*fixture(tmp_path))
    for frames in ([], [True], [-1], [8], [0.5], list(range(257))):
        with pytest.raises(ValueError):
            plan_preview(owner, frames, quality=0)
    for names in ([], ["not-there"], ["vision:foreground"] * 2):
        with pytest.raises(ValueError):
            plan_preview(owner, [0], quality=0, sources=names)


def test_changed_image_pins_invalidate_existing_preview_cache(monkeypatch, tmp_path):
    owner = VisionGraphSources(*fixture(tmp_path))
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    owner.source("vision:foreground", 0, quality=0)
    (tmp_path / "current.png").write_bytes(b"modified image")
    with pytest.raises(ValueError):
        owner.source("vision:foreground", 1, quality=0)


def test_original_source_schema_unchanged_without_preview_policy(tmp_path):
    project, cfg = fixture(tmp_path, missing="hold", preview=False)
    original = json.dumps(cfg, sort_keys=True)
    owner = VisionGraphSources(project, cfg)
    assert owner.selection("vision:foreground", 2)[0] == 0
    with pytest.raises(ValueError):
        owner.selection("vision:foreground", 2, quality=0)
    assert json.dumps(cfg, sort_keys=True) == original
