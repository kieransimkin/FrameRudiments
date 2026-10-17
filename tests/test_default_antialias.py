"""Maximum-quality defaults and deliberate low-quality options."""
import copy
import os
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from framerudiments.output_quality import OutputQuality


def image():
    source = Image.new("RGB", (100, 74), "#09152c")
    ImageDraw.Draw(source).polygon([(8, 9), (83, 15), (76, 64), (11, 58)], fill="#f4a27a")
    return source


def test_enabled_by_default_at_maximum_supported_settings():
    obj = OutputQuality()
    assert obj.enabled and obj.vector_scale == 4 and obj.time_samples == 8
    assert obj.passes == 3 and obj.strength == 1
    assert obj.evidence["spatial_scale"] == 4
    assert obj.evidence["temporal_samples"] == 8
    out = obj.apply(image())
    assert out.mode == "RGB" and out.size == image().size
    assert out.tobytes() != image().tobytes()
    assert out.tobytes() == obj.apply(image()).tobytes()


def test_explicit_lower_quality_and_pixel_art_opt_out():
    low = OutputQuality({"spatial_scale": 1, "temporal_samples": 1, "antialias": 0})
    assert (low.vector_scale, low.time_samples) == (1, 1)
    assert low.apply(image()).tobytes() == image().tobytes()
    off = OutputQuality({"enabled": False})
    assert (off.vector_scale, off.time_samples) == (1, 1)
    assert off.apply(image()).tobytes() == image().tobytes()
    assert not off.evidence["enabled"]


@pytest.mark.parametrize("bad", [
    {"enabled": 0}, {"spatial_scale": 0}, {"spatial_scale": 5},
    {"spatial_scale": 2.5}, {"temporal_samples": 0}, {"temporal_samples": 9},
    {"temporal_samples": True}, {"antialias_passes": 4},
    {"antialias": float("nan")}, {"other": "unknown"},
])
def test_invalid_or_unbounded_quality_fails_early(bad):
    with pytest.raises((ValueError, TypeError)):
        OutputQuality(bad)


@pytest.fixture
def demo(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font or not Path(font).is_file():
        pytest.skip("Native CI font is not installed")
    from framerudiments.demo import create_demo
    from framerudiments.project import Project
    return Project.load(create_demo(tmp_path / "max-quality", font, width=160, height=240))


def test_direct_compositor_uses_distinct_subframes_by_default(demo):
    from framerudiments.compositor import Compositor
    comp = Compositor(demo)
    high, context = comp.frame(3)
    assert context.frame == 3
    assert comp._temporal_virtual.project.fps == 8 * demo.fps
    assert high.mode == "RGB" and high.size == (160, 240)
    replay, _ = comp.frame(3)
    assert high.tobytes() == replay.tobytes()
    assert comp.evidence()["render_quality"]["spatial_scale"] == 4


def test_explicit_one_sample_does_not_build_high_fps_compositor(demo):
    from framerudiments.project import Project
    from framerudiments.compositor import Compositor
    custom = Project({**copy.deepcopy(demo.data),
                      "render_quality": {"spatial_scale": 1, "temporal_samples": 1,
                                         "antialias": 0}}, demo.base)
    comp = Compositor(custom)
    comp.frame(3)
    assert not hasattr(comp, "_temporal_virtual")


def test_temporal_history_is_not_applied_to_each_subframe(demo):
    from framerudiments.project import Project
    from framerudiments.compositor import Compositor
    config = dict(**copy.deepcopy(demo.data), render_quality={"spatial_scale": 2, "temporal_samples": 2})
    config["post_effects"] = {"temporal": {"strength": .5, "samples": 2, "shutter_frames": 1}}
    comp = Compositor(Project(config, demo.base))
    a, _ = comp.frame(8)
    comp.frame(2)
    assert comp.frame(8)[0].tobytes() == a.tobytes()
    assert comp.post_pipeline.last_report["temporal"]["source_frames"][-1] == 8
    assert comp._temporal_virtual.post_pipeline.temporal is None
