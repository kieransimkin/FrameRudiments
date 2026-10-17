"""Per-element vs frame post-effects, native modulation, validation and seeking."""
import copy
import os
from pathlib import Path

from PIL import Image
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def project(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        for candidate in (Path("C:/Windows/Fonts/arial.ttf"),
                          Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
            if candidate.is_file():
                font = str(candidate)
                break
    if not font:
        pytest.skip("An installed TTF font is required for compositor integration")
    return Project.load(create_demo(tmp_path / "scene", font, width=360, height=640))


def _stage(name="tint", **visual):
    return dict(id=name, visual=visual)


def _composed(project, config, at=40):
    new = Project({**copy.deepcopy(project.data), "post_effects": config}, project.base)
    comp = Compositor(new)
    return comp, comp.frame(at)[0]


def test_untargeted_project_remains_byte_for_byte_legacy(project):
    reference = Compositor(project).frame(40)[0]
    extra, img = _composed(project, {})
    assert not extra.post_pipeline.enabled
    assert img.tobytes() == reference.tobytes()


def test_isolated_layer_effect_does_not_grade_background_or_captions(project):
    reference = Compositor(project).frame(40)[0]
    scope = dict(elements={"layer:native": [_stage(hue_shift=180)]})
    comp, single = _composed(project, scope)
    assert single.tobytes() != reference.tobytes()
    # The synthetic sprite is in the lower part of the frame. Earlier text,
    # panels and background are not changed by its isolated hue shift.
    assert single.crop((0, 0, 360, 250)).tobytes() == reference.crop((0, 0, 360, 250)).tobytes()
    whole = dict(frame=[_stage(hue_shift=180)])
    _, full = _composed(project, whole)
    assert full.crop((0, 0, 360, 250)).tobytes() != reference.crop((0, 0, 360, 250)).tobytes()
    assert "layer:native" in comp.post_pipeline.last_report


def test_each_element_can_be_targeted_including_legacy_bars_and_text(project):
    config = dict(elements={
        "background": [_stage("bg", brightness=.75)],
        "layer:native": [_stage("sprite", gamma=.7)],
        "layer:bands": [_stage("bars", hue_shift=120)],
        "identity:0": [_stage("title", glow=3)],
        "caption": [_stage("caption", glow=2)],
    })
    comp, image = _composed(project, config, at=75)
    assert image.mode == "RGB"
    assert {"background", "layer:native", "layer:bands", "identity:0", "caption"}.issubset(
        comp.post_pipeline.last_report)
    assert comp.evidence()["post_effects"]["elements"]["identity:0"][0]["id"] == "title"


def test_two_stages_in_order_are_deterministic_in_random_access(project):
    config = dict(all_elements=[_stage("all", contrast=.8)],
                  frame=[_stage("glow", glow=2), _stage("scan", scanlines=.35, pixel_size=2)])
    comp, late = _composed(project, config, at=90)
    early, _ = comp.frame(12)
    again, _ = comp.frame(90)
    assert late.tobytes() == again.tobytes()
    assert late.tobytes() != early.tobytes()
    assert len(comp.post_pipeline.last_report["frame"]) == 2
    assert comp.post_pipeline.last_report["frame"][1]["parameters"]["scanlines"] == .35


def test_native_modulation_is_source_clocked_and_reported(project):
    config = dict(frame=[dict(id="live", visual=dict(glow=1, scanlines=.25), modulations=[
        dict(id="shine", target="visual.glow", motion=dict(kind="named", pattern="bounce"),
             range=[0, 8], axis="x", smoothing_frames=4),
        dict(id="crt", target="visual.scanlines", motion=dict(kind="named", pattern="circle"),
             range=[0, .8], axis="y", lag_frames=2),
    ])])
    comp, first = _composed(project, config, at=90)
    comp.frame(40)
    second, _ = comp.frame(90)
    assert first.tobytes() == second.tobytes()
    report = comp.post_pipeline.last_report["frame"][0]
    assert {"shine", "crt"} == set(report["modulations"])
    assert 0 <= report["parameters"]["glow"] <= 8
    assert 0 <= report["parameters"]["scanlines"] <= .8
    assert "post_effects" in comp.evidence()


def test_discrete_palette_and_gradient_settings_can_be_driven(project):
    config = dict(frame=[dict(id="choice", visual=dict(gradient={"mode": "angular",
        "stops": "aurora"}), modulations=[
        dict(id="palette", motion=dict(kind="named", pattern="circle"), target="visual.palette",
             choices=["aurora", "fire", "ice"], axis="x"),
        dict(id="direction", motion=dict(kind="named", pattern="bounce"),
             target="visual.gradient.mode", choices=["angular", "radial", "vertical"], axis="y"),
        dict(id="colours", motion=dict(kind="named", pattern="circle"),
             target="visual.gradient.stops", choices=["rainbow", "sunset"], axis="z"),
    ])])
    comp, image = _composed(project, config)
    assert image.size == (360, 640)
    applied = comp.post_pipeline.last_report["frame"][0]
    assert applied["parameters"]["palette"] in ["aurora", "fire", "ice"]
    assert applied["parameters"]["gradient"]["mode"] in ["angular", "radial", "vertical"]
    assert applied["parameters"]["gradient"]["stops"] in ["rainbow", "sunset"]


@pytest.mark.parametrize("config", [
    {"unknown": []},
    {"elements": {"layer:does-not-exist": [_stage()]}},
    {"frame": [dict(id="bad", visual={"unrecognised": 12})]},
    {"frame": [{"id": "duplicate"}, {"id": "duplicate"}]},
    {"frame": [dict(id="bad", modulations=[dict(target="visual.gamma", motion=dict(
        kind="named", pattern="circle"), range=[2, 1])])]},
    {"frame": [dict(id="bad", modulations=[dict(target="visual.palette", motion=dict(
        kind="named", pattern="circle"), choices=["red", "not-a-palette"])])]},
    {"frame": [dict(id="bad", modulations=[dict(target="visual.gradient.blah", motion=dict(
        kind="named", pattern="circle"), choices=["horizontal", "vertical"])])]},
])
def test_invalid_effect_targets_and_parameters_fail_on_load(project, config):
    with pytest.raises((ValueError, KeyError)):
        _composed(project, config)


def test_effect_pipeline_preserves_alpha_and_unrelated_regions(project):
    config = dict(elements={"layer:native": [_stage(glow=5)]})
    p = Project({**copy.deepcopy(project.data), "post_effects": config}, project.base)
    compositor = Compositor(p)
    ctx = compositor.context(40)
    canvas = Image.new("RGBA", (360, 640))
    with_effect = compositor.post_pipeline.apply_element(canvas, "layer:native", ctx)
    assert with_effect.mode == "RGBA" and with_effect.getbbox() is None
    assert canvas.getbbox() is None


def test_scene_scope_and_frame_scope_are_distinct(project):
    def scene(image, ctx):
        from PIL import ImageDraw
        ImageDraw.Draw(image).rectangle((10, 10, 90, 90), fill="#ff0000")
        return image
    p = Project({**copy.deepcopy(project.data), "post_effects": dict(elements={
        "scene": [_stage(hue_shift=120)]})}, project.base)
    comp = Compositor(p, scene=scene)
    image, _ = comp.frame(40)
    assert image.size == (360, 640)
    assert comp.post_pipeline.last_report.get("scene")
    p.data["post_effects"] = {"frame": [_stage(hue_shift=120)]}
    other = Compositor(p, scene=scene).frame(40)[0]
    assert other.tobytes() != image.tobytes()


def test_full_frame_opacity_is_composited_on_black_matte(project):
    original = Compositor(project).frame(40)[0]
    _, faded = _composed(project, dict(frame=[_stage("fade", opacity=.4)]))
    # A full-frame alpha reduction must remain visible after RGB encoding.
    assert faded.getpixel((2, 2)) != original.getpixel((2, 2))
    assert max(faded.getpixel((2, 2))) < max(original.getpixel((2, 2)))


def test_native_driver_can_select_stage_coordinate_space_and_modulate_padding(project):
    config = dict(elements={"layer:native": [dict(id="position", visual=dict(glow=4),
        modulations=[
            dict(id="padding", motion=dict(kind="named", pattern="circle"),
                 target="stage.padding", range=[12, 80], axis="x"),
            dict(id="space", motion=dict(kind="named", pattern="bounce"),
                 target="stage.coordinate_space", choices=["element", "frame"], axis="y"),
        ])]})
    comp, frame = _composed(project, config)
    assert frame.mode == "RGB"
    actual = comp.post_pipeline.last_report["layer:native"][0]["stage_options"]
    assert 12 <= actual["padding"] <= 80
    assert actual["coordinate_space"] in ("element", "frame")
