"""M5.1 compositor-level smoke tests with pinned synthetic media and native motion."""

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
        for candidate in (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ):
            if candidate.is_file():
                font = str(candidate)
                break
    if not font or not Path(font).is_file():
        pytest.skip("M5.1 integration requires an installed TTF font")
    path = create_demo(tmp_path / "m5-matte-demo", font, width=180, height=320)
    p = Project.load(path)
    payload = copy.deepcopy(p.data)
    payload["render_quality"] = {"enabled": False}
    return Project(payload, p.base)


def configured(project, *, controls=(), matte_channel="alpha"):
    data = copy.deepcopy(project.data)
    data["effect_graph"] = {
        "nodes": [
            {"id": "background", "type": "source"},
            {"id": "frame", "type": "source"},
            {"id": "layer:native", "type": "source"},
            {"id": "mix", "type": "transition", "transition_style": "matte",
             "inputs": ["background", "frame", "layer:native"],
             "progress": .5, "matte_channel": matte_channel, "feather": .2},
        ],
        "output": "mix",
    }
    if controls:
        data["effect_graph_controls"] = list(controls)
    return Project(data, project.base)


def test_actual_matte_transition_is_random_access_and_preserves_background(project):
    p = configured(project)
    compositor = Compositor(p)
    frame, _ = compositor.frame(35, apply_quality=False)
    compositor.frame(85, apply_quality=False)
    again, _ = compositor.frame(35, apply_quality=False)
    assert frame.mode == "RGB"
    assert frame.size == (180, 320)
    assert frame.tobytes() == again.tobytes()
    # Opaque background stays identical where the mask is empty.
    assert frame.getpixel((2, 2)) == (16, 27, 46)
    assert len(frame.getcolors(maxcolors=1_000_000)) > 1
    assert p.data["effect_graph"]["nodes"][-1]["progress"] == .5
    compositor.close()


def test_native_clock_modulates_matte_transition_progress(project):
    controls = [{
        "id": "beat-matte-progress", "node": "mix", "property": "progress",
        "motion": {"kind": "named", "pattern": "circle"},
        "axis": "x", "range": [0, 1],
    }]
    static_project = configured(project)
    animated_project = configured(project, controls=controls)
    # A crisp 0/255 alpha mask reveals identical regions for most progress
    # values. Use a full feather to verify actual modulation in rendered pixels.
    static_project.data["effect_graph"]["nodes"][-1]["feather"] = 1.0
    animated_project.data["effect_graph"]["nodes"][-1]["feather"] = 1.0
    static = Compositor(static_project)
    animated = Compositor(animated_project)
    try:
        changed_frames = []
        for at_frame in (12, 25, 35, 45, 65):
            baseline, _ = static.frame(at_frame, apply_quality=False)
            changed, _ = animated.frame(at_frame, apply_quality=False)
            assert isinstance(changed, Image.Image)
            assert changed.mode == baseline.mode == "RGB"
            resolved = animated.effect_graph_controls.values(
                animated.context(at_frame), animated.effect_graph
            )
            if resolved[("mix", "progress")] != .5:
                changed_frames.append((at_frame, changed))
                if changed.tobytes() != baseline.tobytes():
                    break
        assert changed_frames, "native clock did not alter transition progress"
        assert changed.tobytes() != baseline.tobytes(), "feathered transition ignored native motion"
        animated.frame(8, apply_quality=False)
        assert animated.frame(at_frame, apply_quality=False)[0].tobytes() == changed.tobytes()
        assert animated.effect_graph.nodes["mix"].config["progress"] == .5
    finally:
        static.close()
        animated.close()


def test_unknown_third_source_rejected_before_render(project):
    invalid = configured(project)
    invalid.data["effect_graph"]["nodes"][-2]["id"] = "not-a-layer"
    invalid.data["effect_graph"]["nodes"][-1]["inputs"][-1] = "not-a-layer"
    with pytest.raises(ValueError, match="Unknown effect graph source"):
        Compositor(invalid)
