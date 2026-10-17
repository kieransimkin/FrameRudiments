"""Production regression tests for edge AA, spatial blur and temporal shutter."""

import copy
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from framerudiments.quality_effects import (
    TemporalShutter,
    directional_motion_blur,
    edge_aware_antialias,
    linear_shutter_composite,
)
from framerudiments.visual_style import VisualStyle, VISUAL_RANGES


def _pattern():
    frame = Image.new("RGBA", (120, 80))
    ImageDraw.Draw(frame).polygon([(18, 12), (94, 20), (78, 68), (26, 52)], fill=(230, 55, 30, 255))
    return frame


def test_new_quality_settings_are_real_opt_in_and_validated():
    image = _pattern()
    assert VisualStyle({}).apply(image.copy()).tobytes() == image.tobytes()
    assert VisualStyle({"antialias": 0.85}).apply(image.copy()).tobytes() != image.tobytes()
    assert (
        VisualStyle({"motion_blur": 1, "motion_blur_distance": 18}).apply(image.copy()).tobytes()
        != image.tobytes()
    )
    for key in (
        "antialias",
        "antialias_radius",
        "antialias_threshold",
        "antialias_passes",
        "motion_blur",
        "motion_blur_distance",
        "motion_blur_angle",
        "motion_blur_samples",
        "motion_blur_trail",
        "motion_blur_falloff",
    ):
        assert key in VISUAL_RANGES
        with pytest.raises(ValueError):
            VisualStyle({key: float("nan")})


def test_premultiplied_edge_smoothing_is_deterministic_and_alpha_safe():
    src = _pattern()
    aa = edge_aware_antialias(src, strength=1, radius=1, threshold=0, passes=2)
    assert aa.mode == "RGBA" and aa.size == src.size
    assert aa.getpixel((0, 0))[3] == 0
    assert aa.getpixel((45, 36))[0:3] == src.getpixel((45, 36))[0:3]
    assert aa.tobytes() == edge_aware_antialias(src, strength=1, radius=1, threshold=0, passes=2).tobytes()
    assert src.tobytes() != aa.tobytes()
    assert edge_aware_antialias(src, strength=0).tobytes() == src.tobytes()


def test_directional_blur_changes_orientation_without_colour_halation():
    src = _pattern()
    left = directional_motion_blur(src, strength=0.85, distance=24, angle=0, samples=12)
    up = directional_motion_blur(src, strength=0.85, distance=24, angle=90, samples=12)
    assert left.mode == up.mode == "RGBA"
    assert left.size == up.size == src.size
    assert left.tobytes() != up.tobytes()
    assert src.tobytes() == directional_motion_blur(src, strength=0, distance=24).tobytes()
    assert left.getpixel((0, 0))[3] == 0
    assert (
        left.tobytes()
        == directional_motion_blur(src, strength=0.85, distance=24, angle=0, samples=12).tobytes()
    )


def test_linear_shutter_uses_linear_light_and_is_deterministic(monkeypatch):
    black = Image.new("RGB", (12, 8), "#000000")
    white = Image.new("RGB", (12, 8), "#ffffff")
    mixed = linear_shutter_composite([black, white], [1, 1], strength=1)
    assert 185 <= mixed.getpixel((3, 2))[0] <= 190  # sRGB encodes 0.5 linear as ~188
    assert mixed.tobytes() == linear_shutter_composite([black, white], [1, 1], strength=1).tobytes()
    monkeypatch.setitem(sys.modules, "numpy", None)
    assert mixed.tobytes() == linear_shutter_composite([black, white], [1, 1], strength=1).tobytes()
    assert linear_shutter_composite([black, white], [1, 1], strength=0).tobytes() == white.tobytes()
    with pytest.raises(ValueError):
        linear_shutter_composite([black, white], [1], strength=1)


def _fake_context(n, *, reset_at=None):
    source = n / 60 if reset_at is None else (n % reset_at) / 60
    return SimpleNamespace(frame=n, output_seconds=n / 60, source_seconds=source, beat=n / 30)


def _fake_project():
    return SimpleNamespace(base=Path("."), clock=SimpleNamespace(beat_at=lambda t: 2 * t), inputs=[])


def test_history_plan_never_reads_future_or_previous_repeat():
    shutter = TemporalShutter(
        _fake_project(), {"samples": 7, "shutter_frames": 6}, lambda i: _fake_context(i, reset_at=30)
    )
    at_boundary = shutter.plan(_fake_context(30, reset_at=30))
    assert at_boundary[0] == [30]
    future = shutter.plan(_fake_context(34, reset_at=30))
    assert min(future[0]) >= 30 and max(future[0]) == 34
    assert shutter.plan(_fake_context(34, reset_at=30)) == future
    assert shutter.plan(_fake_context(0, reset_at=30))[0] == [0]


@pytest.mark.parametrize(
    "config",
    [
        {"samples": 0},
        {"strength": 5},
        {"shutter_frames": -1},
        {"falloff": -1},
        {"unrecognised": 1},
        {"samples": 3.3},
        {"modulations": [{"target": "visual.hue_shift", "motion": {"kind": "named", "pattern": "circle"}}]},
    ],
)
def test_bad_temporal_shutter_fails_at_initialization(config):
    with pytest.raises((ValueError, KeyError)):
        TemporalShutter(_fake_project(), config, _fake_context)


@pytest.fixture
def compositor_project(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        for candidate in (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ):
            if candidate.is_file():
                font = str(candidate)
                break
    if not font:
        pytest.skip("Requires an installed TTF font")
    from framerudiments import Project
    from framerudiments.demo import create_demo

    return Project.load(create_demo(tmp_path / "quality-project", font, width=240, height=360))


def _with_fx(project, settings):
    from framerudiments import Project
    from framerudiments.compositor import Compositor

    return Compositor(Project({**copy.deepcopy(project.data), "post_effects": settings}, project.base))


def test_post_effects_allow_element_and_frame_anti_alias_and_blur(compositor_project):
    from framerudiments.compositor import Compositor

    original = Compositor(compositor_project).frame(75)[0]
    comp = _with_fx(
        compositor_project,
        {
            "elements": {
                "layer:native": [
                    {
                        "id": "smooth",
                        "visual": {"antialias": 1, "motion_blur": 0.7, "motion_blur_distance": 12},
                    }
                ]
            },
            "frame": [{"id": "grade", "visual": {"antialias": 0.5}}],
        },
    )
    edited = comp.frame(75)[0]
    assert edited.size == original.size
    assert edited.tobytes() != original.tobytes()
    assert comp.frame(75)[0].tobytes() == edited.tobytes()
    assert comp.post_pipeline.last_report["layer:native"][0]["parameters"]["motion_blur"] == 0.7


def test_real_compositor_temporal_random_access_and_reporting(compositor_project):
    config = {"temporal": {"strength": 0.8, "samples": 4, "shutter_frames": 3}}
    comp = _with_fx(compositor_project, config)
    late, context = comp.frame(78)
    report = comp.post_pipeline.last_report["temporal"]
    assert report["source_frames"][-1] == context.frame
    assert all(f <= context.frame for f in report["source_frames"])
    comp.frame(19)
    replay, _ = comp.frame(78)
    assert late.tobytes() == replay.tobytes()
    assert "temporal" in comp.evidence()["post_effects"]
    assert "temporal" in comp.post_effect_reports()


def test_temporal_blur_repeats_stop_at_source_boundary(compositor_project):
    from framerudiments import Project

    data = copy.deepcopy(compositor_project.data)
    data["audio"].update(start_sample=12000, end_sample=36000, repeats=3, fade_seconds=0)
    project = Project(data, compositor_project.base)
    comp = _with_fx(project, {"temporal": {"strength": 1, "shutter_frames": 5, "samples": 6}})
    comp.frame(30)
    assert comp.post_pipeline.last_report["temporal"]["source_frames"] == [30]
    comp.frame(35)
    assert all(n >= 30 for n in comp.post_pipeline.last_report["temporal"]["source_frames"])


def test_shutter_settings_modulated_by_native_motion(compositor_project):
    comp = _with_fx(
        compositor_project,
        {
            "temporal": {
                "strength": 0.4,
                "shutter_frames": 3,
                "samples": 4,
                "modulations": [
                    {
                        "motion": {"kind": "named", "pattern": "circle"},
                        "target": "temporal.strength",
                        "range": [0.1, 0.9],
                    },
                    {
                        "motion": {"kind": "named", "pattern": "bounce"},
                        "target": "temporal.samples",
                        "range": [2, 6],
                    },
                ],
            }
        },
    )
    a = comp.frame(90)[0]
    params = comp.post_pipeline.last_report["temporal"]["parameters"]
    assert 0.1 <= params["strength"] <= 0.9 and 2 <= params["samples"] <= 6
    comp.frame(15)
    assert a.tobytes() == comp.frame(90)[0].tobytes()


def test_quality_gallery_scene_contract():
    from framerudiments.quality_effects import QUALITY_RANGES

    assert len(QUALITY_RANGES) == 10
    assert all(lo <= default <= hi for lo, hi, default in QUALITY_RANGES.values())
