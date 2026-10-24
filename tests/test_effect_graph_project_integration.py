"""Actual FrameRudiments project/compositor graph integration (no FFmpeg/GPU)."""
import copy
import os
from pathlib import Path

from PIL import ImageDraw
import pytest

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo


@pytest.fixture
def demo_project(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        for path in (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ):
            if path.is_file():
                font = str(path)
                break
    if not font or not Path(font).is_file():
        pytest.skip("An installed TTF font is required")
    file = create_demo(tmp_path / "graph-demo", font, width=180, height=320)
    original = Project.load(file)
    data = copy.deepcopy(original.data)
    data["render_quality"] = {"enabled": False}
    return Project(data, original.base)


def configured(project, nodes, output, *, controls=()):
    data = copy.deepcopy(project.data)
    data["effect_graph"] = {"nodes": nodes, "output": output}
    if controls:
        data["effect_graph_controls"] = list(controls)
    return Project(data, project.base)


def test_graph_identity_of_final_frame_is_repeatable(demo_project):
    # With only the final-frame source, graph evaluation is a pure identity.
    # Existing tests separately protect the no-graph legacy output path.
    project = configured(demo_project, [{"id": "frame", "type": "source"}], "frame")
    comp = Compositor(project)
    first = comp.frame(27, apply_quality=False)[0]
    comp.frame(60, apply_quality=False)
    again = comp.frame(27, apply_quality=False)[0]
    assert first.size == (180, 320)
    assert first.tobytes() == again.tobytes()
    assert len(first.getcolors(maxcolors=1000000)) > 1


def test_background_provider_is_not_contaminated_by_visible_layers(demo_project):
    project = configured(demo_project, [{"id": "background", "type": "source"}], "background")
    comp = Compositor(project)
    result, _ = comp.frame(35, apply_quality=False)
    assert result.getpixel((90, 207)) == (16, 27, 46)
    assert result.getpixel((90, 287)) == (16, 27, 46)


def test_invalid_source_fails_at_compositor_construction(demo_project):
    project = configured(demo_project, [{"id": "not-a-layer", "type": "source"}], "not-a-layer")
    with pytest.raises(ValueError, match="Unknown effect graph source"):
        Compositor(project)


def test_forked_native_transition_is_clock_driven_and_seekable(demo_project):
    nodes = [
        {"id": "layer:native", "type": "source"},
        {"id": "layer:bands", "type": "source"},
        {"id": "chromatic", "type": "chromatic_displacement", "inputs": ["layer:native"],
         "red_offset": [1.25, 0], "blue_offset": [-1.25, 0]},
        {"id": "t", "type": "transition", "inputs": ["chromatic", "layer:bands"],
         "transition_style": "dissolve", "progress": 0},
    ]
    controls = [{
        "id": "beat-transition", "node": "t", "property": "progress",
        "motion": {"kind": "named", "pattern": "circle"},
        "axis": "x", "range": [0, 1],
    }]
    comp = Compositor(configured(demo_project, nodes, "t", controls=controls))
    early, _ = comp.frame(12, apply_quality=False)
    late, _ = comp.frame(45, apply_quality=False)
    assert early.mode == late.mode == "RGB"
    assert early.tobytes() != late.tobytes()
    assert comp.frame(12, apply_quality=False)[0].tobytes() == early.tobytes()
    assert comp.effect_graph.nodes["t"].config["progress"] == 0


def test_custom_scene_source_captured_in_isolation(demo_project):
    def scene(surface, ctx):
        ImageDraw.Draw(surface).rectangle((10, 12, 30, 32), fill=(250, 30, 10, 255))
        return surface
    project = configured(demo_project, [{"id": "scene", "type": "source"}], "scene")
    result, _ = Compositor(project, scene=scene).frame(0, apply_quality=False)
    assert result.getpixel((20, 20)) == (250, 30, 10)
    assert result.getpixel((80, 80)) == (0, 0, 0)


def test_inactive_layer_source_returns_transparent_and_does_not_reuse_old_frame(demo_project):
    # The caption is a valid surface but not always visible. It must remain
    # a transparent RGBA source rather than raising a missing-provider error.
    project = configured(demo_project, [{"id": "caption", "type": "source"}], "caption")
    comp = Compositor(project)
    a, _ = comp.frame(0, apply_quality=False)
    b, _ = comp.frame(235, apply_quality=False)
    assert a.size == b.size == (180, 320)
    assert comp.frame(0, apply_quality=False)[0].tobytes() == a.tobytes()
