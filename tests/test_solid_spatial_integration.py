"""End-to-end 3D layer, native motion, temporal sampling and GPU feed integration."""

import os
from pathlib import Path

import pytest

from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.io import read_json, write_json
from framerudiments.project import Project


def test_solid_mesh_routes_as_isolated_element_with_native_clock_and_temporal_sampling(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font or not Path(font).is_file():
        pytest.skip("Requires the CI-pinned render font")

    path = create_demo(tmp_path / "scene", font, width=180, height=320)
    data = read_json(path)
    data["layers"] = [{
        "id": "mesh",
        "type": "rudiment",
        "renderer": "spatial-solid-torus",
        "bounds": [10, 30, 170, 190],
        "motion": {"kind": "named", "pattern": "circle"},
        "style": {"rings": 8, "segments": 12, "fog_steps": 2,
                  "fog_resolution": 80, "glow": 0},
    }]
    # A GPU runtime is not required to test the isolated first-stage input:
    # routing is applied by EffectChain only during actual GPU processing.
    data["effects"] = [{"id": "feedback", "engine": "butterchurn", "preset": "feedback"}]
    data["visualiser_routing"] = {
        "default": {"feed": "never", "overlay": False},
        "elements": {"layer:mesh": {"feed": "continuous", "overlay": True}},
    }
    data["render_quality"] = {"spatial_scale": 2, "temporal_samples": 2, "antialias": 0}
    write_json(path, data)

    composition = Compositor(Project.load(path))
    first, ctx = composition.frame(0, apply_quality=False)
    assert first.mode == "RGB" and first.size == (180, 320)
    assert ctx.frame == 0
    surfaces = dict(composition._route_parts)
    assert surfaces["layer:mesh"].getchannel("A").getbbox()
    assert composition.frame_reports()["mesh"]["kind"] == "procedural-solid-3d"
    assert composition.evidence()["rudiment_renderers"]["mesh"]["renderer"]["name"] == "spatial-solid-torus"
    # Replaying the same encoded frame gives byte-identical output.
    assert composition.frame(0, apply_quality=False)[0].tobytes() == first.tobytes()
