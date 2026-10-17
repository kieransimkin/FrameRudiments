"""Phase 4 deterministic projected geometry and rendering contract checks."""

from types import SimpleNamespace

import pytest

from framerudiments.spatial_renderers import SpatialWireRenderer
from framerudiments.rudiment_renderers import default_renderers


def frame(t=0, values=(0.4, -0.2)):
    return SimpleNamespace(values=values, context=SimpleNamespace(output_seconds=t, frame=round(t * 60)))


@pytest.mark.parametrize("kind", ["torus", "sphere", "terrain"])
def test_registered_spatial_modes_are_frame_addressed(kind):
    renderer, _ = default_renderers().create("spatial-" + kind, (180, 120), {"background": "#101b2e"})
    image = renderer.render(frame(1))
    assert image.mode == "RGBA" and image.size == (180, 120)
    assert image.tobytes() == renderer.render(frame(1)).tobytes()
    assert image.tobytes() != renderer.render(frame(2)).tobytes()
    assert renderer.frame_report["kind"] == "procedural-perspective-wire"
    assert renderer.frame_report["projected_edges"] > 10


def test_camera_distance_fog_glow_and_audio_motion_change_pixels():
    a = SpatialWireRenderer((180, 120), {"camera_distance": 4, "glow": 0})
    b = SpatialWireRenderer((180, 120), {"camera_distance": 8, "glow": 0.8})
    assert a.render(frame()).tobytes() != b.render(frame()).tobytes()
    assert a.render(frame(values=(-0.8, 0.1))).tobytes() != a.render(frame()).tobytes()


@pytest.mark.parametrize(
    "style",
    [
        {"geometry": "cube"},
        {"fov": 0},
        {"camera_distance": 0},
        {"rings": 1},
        {"segments": 3},
        {"glow": 2},
        {"fog": float("nan")},
        {"colours": []},
        {"unexpected": 1},
        {"line_width": 0},
    ],
)
def test_bad_3d_styles_fail_closed(style):
    with pytest.raises(ValueError):
        SpatialWireRenderer((180, 120), style)


def test_no_external_gpu_runtime_or_state_required():
    r = SpatialWireRenderer((160, 100), {})
    later = r.render(frame(4)).tobytes()
    r.render(frame(1))
    assert r.render(frame(4)).tobytes() == later
