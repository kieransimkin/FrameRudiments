"""Native DanceRudiments motion and GUI-independent compositor integration."""

import os
from pathlib import Path

import pytest

from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.io import read_json, write_json
from framerudiments.project import Project


def test_native_motion_modulates_mesh_material_and_geometry(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font or not Path(font).is_file():
        pytest.skip("Requires the CI-pinned render font")

    path = create_demo(tmp_path / "music-3d", font, width=180, height=320)
    data = read_json(path)
    data["layers"] = [{
        "id": "bass-landscape",
        "type": "rudiment",
        "renderer": "spatial-music-terrain",
        "bounds": [10, 30, 170, 190],
        "motion": {"kind": "named", "pattern": "circle"},
        "style": {"backend": "cpu", "rings": 7, "segments": 11, "fog": 0, "glow": 0},
        "modulations": [{
            "id": "snow", "motion": {"kind": "named", "pattern": "bounce"},
            "axis": "y", "target": "style.ice_coverage", "range": [0, 1],
        }, {
            "id": "kick", "motion": {"kind": "named", "pattern": "circle"},
            "axis": "x", "target": "style.audio_displacement", "range": [0.1, 0.65],
        }],
    }]
    data["render_quality"] = {"spatial_scale": 1, "temporal_samples": 1, "antialias": 0}
    write_json(path, data)

    composite = Compositor(Project.load(path))
    later, context = composite.frame(50, apply_quality=False)
    assert later.mode == "RGB" and later.size == (180, 320)
    assert context.frame == 50
    report = composite.frame_reports()["bass-landscape"]
    assert report["backend"] == "cpu" and report["kind"] == "music-reactive-3d"
    assert {"snow", "kick"} == set(report["modulations"])
    assert 0 <= report["modulations"]["snow"]["value"] <= 1
    assert composite.frame(14, apply_quality=False)[0].tobytes() != later.tobytes()
    assert composite.frame(50, apply_quality=False)[0].tobytes() == later.tobytes()
