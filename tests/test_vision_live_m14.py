"""M14.5 live Q-tier prediction admission and revision-fenced cancellation."""
from types import SimpleNamespace
import subprocess

from PIL import Image
import pytest

from framerudiments.effect_graph_integration import evaluate_effect_graph
from framerudiments.vision_graph_m14 import VisionGraphSources
from framerudiments.vision_live_m14 import prepare_live_frame
from framerudiments.vision_m14 import VisionCancelled, _run_worker
from test_vision_graph_m14 import inputs, mocked_infer


def setup(tmp_path, *, quality=0, age=True, missing="error"):
    project, config = inputs(tmp_path)
    config["sources"][0]["missing"] = missing
    if age:
        config["sources"][0]["preview_max_age_frames"] = [4, 3, 2, 1, 0, 0]
    project._vision_preview_quality = quality
    return VisionGraphSources(project, config)


def test_live_preview_reuses_q0_but_exact_q5_does_not_reuse(monkeypatch, tmp_path):
    owner = setup(tmp_path)
    calls = []

    def infer(manifest, output, **kwargs):
        calls.append(manifest)
        return mocked_infer(manifest, output)

    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", infer)
    report = prepare_live_frame(owner, 3, "Q0", cancelled=lambda: False)
    assert report["verified_predictions"] == 1
    assert report["reused_taps"] == 1
    assert owner.last_used["vision:foreground"]["source_frame"] == 0
    assert calls and len(calls) == 1
    owner.project._vision_preview_quality = 5
    with pytest.raises(ValueError, match="Missing"):
        prepare_live_frame(owner, 3, "Q5", cancelled=lambda: False)
    assert len(calls) == 1


def test_live_q5_prefetches_exact_frame_without_resetting_source_clock(monkeypatch, tmp_path):
    owner = setup(tmp_path, quality=5)
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", mocked_infer)
    report = prepare_live_frame(owner, 0, "Q5", cancelled=lambda: False)
    assert report["quality_rank"] == 5
    assert report["reused_taps"] == 0
    assert owner.last_used["vision:foreground"]["preview_quality"] == 5


def test_live_preview_cancellation_prevents_inference_and_cache_creation(monkeypatch, tmp_path):
    owner = setup(tmp_path)
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer",
                        lambda *_a, **_k: pytest.fail("Cancelled preview started ONNX"))
    with pytest.raises(VisionCancelled):
        prepare_live_frame(owner, 0, "Q0", cancelled=lambda: True)
    assert not owner.cache.exists()


def test_live_preview_cancelled_after_inference_does_not_publish(monkeypatch, tmp_path):
    owner = setup(tmp_path)
    cancelled = [False]

    def infer(manifest, output, **kwargs):
        mocked_infer(manifest, output)
        cancelled[0] = True

    monkeypatch.setattr("framerudiments.vision_graph_m14.infer", infer)
    owner.project._vision_preview_cancelled = lambda: cancelled[0]
    with pytest.raises(VisionCancelled):
        prepare_live_frame(owner, 0, "Q0", cancelled=lambda: cancelled[0])
    assert not [p for p in owner.cache.glob("*") if p.is_dir()]


@pytest.mark.parametrize("profile", [None, 0, "Q6", "Q00", "q0", True])
def test_live_preview_rejects_invalid_profiles(tmp_path, profile):
    owner = setup(tmp_path)
    with pytest.raises(ValueError):
        prepare_live_frame(owner, 0, profile, cancelled=lambda: False)


def test_live_preview_rejects_wrong_quality_or_missing_callback(tmp_path):
    owner = setup(tmp_path)
    with pytest.raises(ValueError, match="not the same"):
        prepare_live_frame(owner, 0, "Q2", cancelled=lambda: False)
    with pytest.raises(ValueError, match="callback"):
        prepare_live_frame(owner, 0, "Q0", cancelled=None)


def test_explicit_transparent_preview_never_produces_an_inference(monkeypatch, tmp_path):
    owner = setup(tmp_path, missing="transparent")
    monkeypatch.setattr("framerudiments.vision_graph_m14.infer",
                        lambda *_a, **_k: pytest.fail("Transparent source ran ONNX"))
    report = prepare_live_frame(owner, 6, "Q0", cancelled=lambda: False)
    assert report["verified_predictions"] == 0


def test_temporal_subframes_resolve_to_encoded_prediction_frame():
    sample = Image.new("RGBA", (4, 4), "red")
    calls = []

    class Source:
        project = SimpleNamespace(_time_sampling_factor=4, _vision_preview_quality=2)
        source_ids = {"vision:sample"}
        element_masks = {}

        def source(self, key, frame, *, quality=None):
            calls.append((key, frame, quality))
            return sample

    class Graph:
        source_ids = {"vision:sample"}
        layer_style_ids = ()

        def render(self, sources, **_kwargs):
            assert sources["vision:sample"] is sample
            return sample.copy()

    result = evaluate_effect_graph(Graph(), None, SimpleNamespace(frame=11), sample, {},
                                   vision_sources=Source())
    assert result.tobytes() == sample.tobytes()
    assert calls == [("vision:sample", 2, 2)]


def test_predicted_element_mask_receives_same_quality_as_direct_graph_source():
    image = Image.new("RGBA", (4, 4), "red")
    class Source:
        project = SimpleNamespace(_time_sampling_factor=1, _vision_preview_quality=3)
        source_ids = {"vision:mask"}
        element_masks = {"layer:native": "vision:mask"}
        def source(self, key, frame, *, quality=None):
            assert (key, frame, quality) == ("vision:mask", 6, 3)
            return image.copy()
        def mask_elements(self, sources, frame, *, quality=None):
            assert (frame, quality) == (6, 3)
            assert "vision:mask" in sources
            return sources
    class Graph:
        source_ids = {"vision:mask", "layer:native"}
        layer_style_ids = ()
        def render(self, sources, **_kw):
            return sources["vision:mask"].copy()
    assert evaluate_effect_graph(Graph(), None, SimpleNamespace(frame=6), image,
                                 {"layer:native": image}, vision_sources=Source()).size == (4, 4)


class FakeProc:
    def __init__(self, *args, **kwargs):
        self.returncode = None
        self.killed = False
        self.polls = 0
    def communicate(self, timeout=None):
        if timeout is None:
            return "", ""
        self.polls += 1
        raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
    def poll(self):
        return None if not self.killed else -9
    def kill(self):
        self.killed = True


def test_stale_worker_killed_and_reaped(monkeypatch):
    child = FakeProc()
    monkeypatch.setattr("framerudiments.vision_m14.subprocess.Popen", lambda *a, **kw: child)
    ticks = [0]
    def cancelled():
        ticks[0] += 1
        return ticks[0] >= 3
    with pytest.raises(VisionCancelled):
        _run_worker(["python", "-m", "example"], timeout=5, cancelled=cancelled)
    assert child.killed
    assert child.polls >= 1


def test_offline_worker_unchanged(monkeypatch):
    expected = subprocess.CompletedProcess(["python"], 0, "ok", "")
    monkeypatch.setattr("framerudiments.vision_m14.subprocess.run", lambda *a, **kw: expected)
    assert _run_worker(["python"], timeout=5, cancelled=None) is expected


def test_real_isolated_subprocess_stops_on_superseded_revision():
    import sys
    from time import monotonic

    checks = [0]
    def superseded():
        checks[0] += 1
        return checks[0] >= 3
    started = monotonic()
    with pytest.raises(VisionCancelled):
        _run_worker([sys.executable, "-c", "import time; time.sleep(10)"],
                    timeout=15, cancelled=superseded)
    assert monotonic() - started < 5
    assert checks[0] >= 3
