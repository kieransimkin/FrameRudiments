import json
import os

import dancerudiments as dr
from PIL import Image, ImageDraw
import pytest

from framerudiments import Project, default_renderers, render, preflight
from framerudiments.cli import main
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo, create_rudiment_demo
from framerudiments.io import write_json


@pytest.fixture
def project(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        pytest.skip("Set FRAMERUDIMENTS_TEST_FONT")
    return Project.load(create_demo(tmp_path / "demo", font, width=180, height=320))


def layer(project, renderer="particles", **extra):
    cfg = dict(
        id="view",
        type="rudiment",
        renderer=renderer,
        bounds=[10, 10, 170, 150],
        motion=dict(kind="named", pattern="circle"),
    )
    cfg.update(extra)
    project.data["layers"] = [cfg]
    return Compositor(project)


@pytest.mark.parametrize("renderer", ["particles", "bars", "waveform", "path"])
def test_renderers_animate_and_random_access_matches_sequential(project, renderer):
    compositor = layer(project, renderer)
    first, _ = compositor.frame(0)
    expected, _ = compositor.frame(43)
    for n in range(44):
        sequential, _ = compositor.frame(n)
    assert expected.tobytes() == sequential.tobytes()
    assert expected.tobytes() != first.tobytes()
    compositor.frame(179)
    assert compositor.frame(43)[0].tobytes() == expected.tobytes()
    evidence = compositor.evidence()["rudiment_renderers"]["view"]
    assert evidence["renderer"]["name"] == renderer
    assert evidence["renderer"]["source"]["sha256"]
    bounds = compositor.evidence()["transformed_alpha_extents"]["view"]
    assert 10 <= bounds[0] < bounds[2] <= 170
    assert 10 <= bounds[1] < bounds[3] <= 150


def test_all_recording_dimensions_or_explicit_named_axes_are_native(project):
    recording = project.data["layers"][1]["motion"]
    compositor = layer(project, "bars", motion=recording)
    view = compositor.layers[0]
    data = view.data(compositor.context(30))
    assert data.dimensions == ("low", "mid", "high", "air")
    assert data.values == tuple(view.motion.recording.sample(64))
    selected = layer(project, "waveform", motion=recording, axes=["air", "low"])
    data = selected.layers[0].data(selected.context(30))
    assert data.dimensions == ("air", "low")
    native = tuple(view.motion.recording.sample(64))
    assert data.values == (native[3], native[0])
    with pytest.raises(ValueError, match="outside non-looping"):
        selected.layers[0].motion.values(4)


def test_history_contains_real_frames_only_and_respects_gates(project):
    compositor = layer(project, "waveform", history_frames=8, history_stride=2, start_seconds=1)
    view = compositor.layers[0]
    assert view.draw(Image.new("RGBA", (180, 320)), compositor.context(59)) is None
    assert [s.context.frame for s in view.data(compositor.context(60)).history] == [60]
    data = view.data(compositor.context(75))
    assert [s.context.frame for s in data.history] == list(range(61, 76, 2))
    for sample in data.history:
        expected = dr.sample("circle", project.clock.pip_at(sample.context.source_seconds))
        assert sample.values == (expected.x, expected.y, expected.z)
    gated = layer(project, "path", cue_labels=["closing"])
    assert [s.context.frame for s in gated.layers[0].data(gated.context(120)).history] == [120]


def test_history_tracks_exact_repeated_excerpt_clocks(project):
    project.data["audio"].update(start_sample=12000, end_sample=36000, repeats=3, fade_seconds=0)
    project = Project(project.data, project.base)
    compositor = layer(project, "path", history_frames=12)
    data = compositor.layers[0].data(compositor.context(35))
    assert any(
        b.context.source_seconds < a.context.source_seconds for a, b in zip(data.history, data.history[1:])
    )
    assert all(b.context.beat > a.context.beat for a, b in zip(data.history, data.history[1:]))
    for sample in data.history:
        expected = dr.sample("circle", int(sample.context.beat * 64))
        assert sample.values == (expected.x, expected.y, expected.z)


@pytest.mark.parametrize(
    "change",
    [
        dict(renderer="missing"),
        dict(axes=["missing"]),
        dict(axes=[True]),
        dict(history_frames=0),
        dict(history_frames=513),
        dict(history_stride=0),
        dict(bounds=[0.5, 10, 170, 150]),
        dict(style={"unrecognised": 2}),
        dict(style={"colours": []}),
        dict(opacity=float("nan")),
    ],
)
def test_bad_renderer_configuration_fails_closed(project, change):
    with pytest.raises(ValueError):
        layer(project, **change)


def test_registry_extension_is_local_and_custom_pixels_are_viewport_bounded(project, tmp_path):
    class Dot:
        def __init__(self, size, style):
            self.size = size

        def render(self, frame):
            image = Image.new("RGBA", self.size)
            ImageDraw.Draw(image).rectangle((0, 0, *self.size), fill="#fa5060")
            return image

    registry = default_renderers()
    registry.register("dot", Dot)
    assert "dot" not in default_renderers().names
    with pytest.raises(ValueError, match="already registered"):
        registry.register("particles", Dot)
    project.data["layers"] = [
        dict(
            id="dot",
            type="rudiment",
            renderer="dot",
            bounds=[10, 10, 170, 150],
            motion=dict(kind="named", pattern="bounce"),
        )
    ]
    from framerudiments.output_quality import OutputQuality

    project.output_quality = OutputQuality({"enabled": False})
    compositor = Compositor(project, renderers=registry)
    image, _ = compositor.frame(30)
    assert image.getpixel((9, 9)) != (250, 80, 96)
    assert image.getpixel((10, 10)) == (250, 80, 96)
    assert compositor.evidence()["rudiment_renderers"]["dot"]["renderer"]["factory"].endswith("Dot")
    if os.environ.get("FRAMERUDIMENTS_FFMPEG"):
        result = preflight(project, tmp_path / "custom-preflight", renderers=registry)
        assert result["composition"]["rudiment_renderers"]["dot"]["renderer"]["name"] == "dot"


def test_invalid_plugin_output_and_too_many_axes_for_viewport_fail(project):
    class Bad:
        def __init__(self, size, style):
            pass

        def render(self, frame):
            return Image.new("RGB", (1, 1))

    registry = default_renderers()
    registry.register("bad", Bad)
    project.data["layers"] = [
        dict(id="bad", type="rudiment", renderer="bad", motion=dict(kind="named", pattern="circle"))
    ]
    from framerudiments.output_quality import OutputQuality

    project.output_quality = OutputQuality({"enabled": False})
    with pytest.raises(ValueError, match="viewport-sized RGBA"):
        Compositor(project, renderers=registry).frame(0)
    compositor = layer(project, "bars", bounds=[10, 10, 24, 30], axes=[0, 1, 2], style={"gap": 3})
    with pytest.raises(ValueError, match="select axes"):
        compositor.frame(0)


@pytest.mark.parametrize("size", [(180, 320), (320, 180), (180, 180)])
def test_encoded_modular_demo_has_four_renderers_and_native_evidence(tmp_path, size):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font or not os.environ.get("FRAMERUDIMENTS_FFMPEG"):
        pytest.skip("Set font and FFmpeg for integration")
    path = create_rudiment_demo(tmp_path / "modular", font, width=size[0], height=size[1])
    data = json.loads(path.read_text())
    data["audio"].update(end_sample=48000, fade_seconds=0)
    write_json(path, data)
    result = render(path, tmp_path / "encoded", preset="ultrafast")
    assert result["frames"] == 60
    assert result["verification"]["full_decode"] == "passed"
    assert result["software"]["framerudiments"] == __import__("framerudiments").__version__
    assert set(result["composition"]["rudiment_renderers"]) == {"particles", "bars", "waveform", "path"}
    assert set(result["composition"]["motions"]) == {"particles", "bars", "waveform", "path"}
    assert not result["verification"]["listening_approved"]


def test_cli_lists_builtin_renderers(capsys):
    assert main(["renderers"]) == 0
    names = json.loads(capsys.readouterr().out)
    assert names[:5] == ["particles", "bars", "waveform", "path", "spectrum"]
    from framerudiments.visualisation_catalogue import ALL_VIEWS

    assert set(names[5:]) == {view.name for view in ALL_VIEWS}
