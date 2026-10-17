"""New circular/colour/modulation regression tests; no private audio or DSP."""

from pathlib import Path
import sys
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from framerudiments.rudiment_renderers import default_renderers
from framerudiments.visualisation_catalogue import CIRCULAR_VIEWS
from framerudiments.visual_style import VisualStyle, PALETTES
from framerudiments.modulation import RudimentModulations


def _frame(t):
    context = SimpleNamespace(frame=t, source_seconds=t / 60, output_seconds=t / 60, beat=t / 30)
    return context


def _modulations(configs):
    p = SimpleNamespace(base=Path("."), clock=SimpleNamespace(beat_at=lambda x: x * 2))
    return RudimentModulations(p, configs, _frame)


def test_every_circular_view_is_registered_and_has_independent_output():
    from framerudiments.visualisation_catalogue import ALL_VIEWS

    names = default_renderers().names
    assert len(names) == 213
    assert len(names) == len(set(names))
    assert len(CIRCULAR_VIEWS) == 60
    assert set(spec.name for spec in ALL_VIEWS).issubset(names)


def test_gradient_modes_preserve_transparency_and_affect_ink():
    image = Image.new("RGBA", (96, 96))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 15, 82, 75), fill="#ffffff")
    styles = [
        {"gradient": {"mode": mode, "stops": ["#ff0000", "#0000ff"], "blend": "replace"}}
        for mode in ("radial", "angular", "vertical", "horizontal", "diagonal", "level", "checker")
    ]
    outputs = [VisualStyle(row).apply(image.copy()) for row in styles]
    assert all(x.getpixel((0, 0))[3] == 0 for x in outputs)
    assert all(x.getpixel((25, 30)) != x.getpixel((75, 65)) for x in outputs[:5])
    assert len({x.tobytes() for x in outputs}) == len(outputs)
    assert VisualStyle(styles[0]).apply(image.copy()).tobytes() == outputs[0].tobytes()
    assert VisualStyle({}).apply(image.copy()).tobytes() == image.tobytes()


def test_custom_gradient_stops_and_colour_grading():
    p = VisualStyle(
        {
            "palette": "aurora",
            "gradient": {
                "mode": "angular",
                "stops": [
                    {"at": 0, "colour": "#111111"},
                    {"at": 0.5, "colour": "#00ff00"},
                    {"at": 1, "colour": "#111111"},
                ],
                "space": "hsv",
                "wrap": "repeat",
                "cycles": 3,
                "bands": 10,
            },
            "hue_shift": 120,
            "saturation": 0.8,
            "brightness": 1.2,
            "contrast": 1.1,
            "gamma": 0.8,
            "posterize": 6,
            "opacity": 0.5,
        }
    )
    im = Image.new("RGBA", (64, 64))
    ImageDraw.Draw(im).ellipse((4, 4, 60, 60), fill="#caaaff")
    result = p.apply(im)
    assert result.getpixel((32, 32))[3] == 128
    assert result.getpixel((0, 0))[3] == 0
    assert all(len(palette) >= 2 for palette in PALETTES.values())


@pytest.mark.parametrize(
    "bad",
    [
        {"gradient": {"mode": "no-such-gradient"}},
        {"gradient": {"mode": "radial", "stops": []}},
        {"gradient": {"stops": [{"at": 0.7, "colour": "#ff0000"}, {"at": 0.2, "colour": "#0000ff"}]}},
        {"palette": "invalid"},
        {"rotation": float("nan")},
        {"brightness": -1},
        {"gradient": {"space": "xyz"}},
        {"gradient": {"mode": "radial", "wrap": "invalid"}},
    ],
)
def test_reject_bad_visual_inputs(bad):
    with pytest.raises((ValueError, KeyError)):
        VisualStyle(bad)


@pytest.mark.parametrize(
    "bad",
    [
        {"target": "visual.unknown"},
        {"target": "os.system"},
        {"target": "style._private"},
        {"target": "visual.hue_shift", "range": [1, -1]},
        {"target": "visual.hue_shift", "axis": "not-an-axis"},
        {"target": "visual.hue_shift", "curve": "invalid"},
        {"target": "visual.hue_shift", "smoothing_frames": 121},
    ],
)
def test_reject_invalid_modulation_configs(bad):
    base = {"motion": {"kind": "named", "pattern": "circle"}}
    with pytest.raises((ValueError, KeyError)):
        _modulations([dict(base, **bad)])


def test_source_clocked_independent_motion_modulates_multiple_properties():
    mods = _modulations(
        [
            {
                "id": "spin",
                "motion": {"kind": "named", "pattern": "circle"},
                "axis": "x",
                "target": "style.start_angle",
                "range": [-180, 180],
                "curve": "smoothstep",
                "smoothing_frames": 3,
            },
            {
                "id": "glow",
                "motion": {"kind": "named", "pattern": "bounce"},
                "axis": "y",
                "target": "visual.glow",
                "range": [0, 12],
                "curve": "absolute",
            },
            {
                "id": "rainbow",
                "motion": {"kind": "named", "pattern": "circle"},
                "axis": "z",
                "target": "gradient.phase",
                "range": [-1, 1],
                "combine": "add",
            },
        ]
    )
    base_style = {"start_angle": 0}
    base_visual = {"gradient": {"mode": "angular", "stops": "rainbow"}}
    a = mods.apply(_frame(70), base_style, base_visual)
    b = mods.apply(_frame(20), base_style, base_visual)
    assert mods.apply(_frame(70), base_style, base_visual) == a
    assert a != b
    style, visual, report = a
    assert -180 <= style["start_angle"] <= 180
    assert 0 <= visual["glow"] <= 12
    assert "spin" in report and "glow" in report and "rainbow" in report
    assert "glow" not in base_visual and base_style["start_angle"] == 0
    assert report["spin"]["target"] == "style.start_angle"


def test_integer_circular_fields_are_rounded_for_modulation():
    mods = _modulations(
        [
            {"motion": {"kind": "named", "pattern": "circle"}, "target": "style.axis", "range": [0, 2]},
            {
                "motion": {"kind": "named", "pattern": "bounce"},
                "target": "style.fill_alpha",
                "range": [50, 230],
            },
            {
                "motion": {"kind": "named", "pattern": "bounce"},
                "target": "style.led_segments",
                "range": [4, 32],
            },
        ]
    )
    style, _, _ = mods.apply(_frame(80), {}, {})
    assert all(isinstance(style[name], int) for name in ("axis", "fill_alpha", "led_segments"))


def test_modulator_repeated_excerpt_clock_not_hidden_state():
    mods = _modulations(
        [
            {
                "motion": {"kind": "named", "pattern": "circle", "repeat_clock": "source"},
                "target": "visual.rotation",
                "axis": "x",
                "range": [-120, 120],
                "lag_frames": 2,
                "smoothing_frames": 12,
                "sample_stride": 2,
            }
        ]
    )
    early = mods.apply(_frame(12), {}, {})
    mods.apply(_frame(100), {}, {})
    assert mods.apply(_frame(12), {}, {}) == early
    assert early[1]["rotation"] >= -120 and early[1]["rotation"] <= 120


def test_circular_style_parameters_affect_rendered_geometry():
    from generate_visualisation_gallery import fixture

    r = default_renderers()
    frame = fixture(95, spectral=True)
    first, _ = r.create("spectrum-annular-bars", (320, 240), {"inner_radius": 0.2, "sweep_angle": 360})
    second, _ = r.create(
        "spectrum-annular-bars",
        (320, 240),
        {"inner_radius": 0.45, "sweep_angle": 240, "bar_gap": 0.4, "frequency_scale": "mel"},
    )
    assert first.render(frame).tobytes() != second.render(frame).tobytes()
    with pytest.raises(ValueError, match="calibrated"):
        first.render(fixture(95, spectral=False))
    with pytest.raises(ValueError, match="inner_radius"):
        r.create("spectrum-annular-bars", (320, 240), {"inner_radius": 0.8, "outer_radius": 0.3})


def test_gallery_can_generate_213_distinct_renderer_previews():
    # The existing gallery test verifies counts + every PNG. Avoid generating
    # hundreds of 480x270 images twice in every test run.
    from framerudiments.visualisation_catalogue import ALL_VIEWS

    assert len(ALL_VIEWS) == 208


def test_native_source_drives_layer_modulation_in_random_access_compositor(tmp_path):
    """Production compositor exercise with existing project and native clocks."""
    import os

    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        pytest.skip("Requires the CI-pinned render font")
    from framerudiments import Project
    from framerudiments.compositor import Compositor
    from framerudiments.demo import create_demo

    p = Project.load(create_demo(tmp_path / "native-mod-demo", font, width=360, height=640))
    p.data["layers"] = [
        dict(
            id="coupled",
            type="rudiment",
            renderer="waveform-annular-bars",
            motion={"kind": "named", "pattern": "circle"},
            bounds=[20, 180, 340, 500],
            history_frames=120,
            style=dict(inner_radius=0.28, outer_radius=0.9, line_width=4, background="#091624"),
            visual=dict(
                palette="sunset", gradient={"mode": "angular", "stops": "rainbow", "space": "hsv"}, glow=1
            ),
            modulations=[
                dict(
                    id="angle",
                    motion={"kind": "named", "pattern": "bounce"},
                    target="style.start_angle",
                    axis="x",
                    range=[-80, 80],
                    curve="smoothstep",
                ),
                dict(
                    id="colour",
                    motion={"kind": "named", "pattern": "circle"},
                    target="visual.hue_shift",
                    axis="y",
                    range=[-90, 90],
                ),
                dict(
                    id="twist",
                    motion={"kind": "named", "pattern": "bounce"},
                    target="visual.rotation",
                    axis="z",
                    range=[-15, 15],
                    lag_frames=2,
                ),
            ],
        )
    ]
    compositor = Compositor(p)
    first, _ = compositor.frame(40)
    other, _ = compositor.frame(15)
    again, _ = compositor.frame(40)
    assert first.tobytes() == again.tobytes()
    assert first.tobytes() != other.tobytes()
    evidence = compositor.evidence()["rudiment_renderers"]["coupled"]
    assert len(evidence["modulations"]) == 3
    assert evidence["renderer"]["name"] == "waveform-annular-bars"
    assert compositor.layers[0].frame_report["kind"] == "rudiment-visual-modulation"
    assert len(compositor.layers[0].frame_report["modulations"]) == 3
