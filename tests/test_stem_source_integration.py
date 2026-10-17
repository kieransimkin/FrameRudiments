"""Full compositor coverage for StemLab recording aliases and track mattes."""

import os
from pathlib import Path

import pytest
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.project import Project


def example(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font or not Path(font).is_file():
        pytest.skip("CI-provided font required")
    p = Project.load(create_demo(tmp_path / "stem-demo", font, width=180, height=320))
    p.data["render_quality"] = {"enabled": False}
    return p


def test_named_stem_alias_uses_original_native_motion(tmp_path):
    p = example(tmp_path)
    recording = p.data["layers"][1]["motion"]
    p.data["stem_sources"] = {"drums": recording}
    p.data["layers"] = [
        {
            "id": "stem",
            "type": "rudiment",
            "renderer": "waveform",
            "stem_source": "drums",
            "bounds": [5, 5, 175, 155],
        }
    ]
    p = Project(p.data, p.base)
    c = Compositor(p)
    assert c.frame(20)[0].size == (180, 320)
    assert c.layers[0].evidence["stem_source"] == "drums"
    assert c.layers[0].motion.files
    assert c.layers[0].data(c.context(20)).values == c.layers[0].sample(20).values


def test_stem_alias_conflicts_and_unknown_names_fail(tmp_path):
    p = example(tmp_path)
    motion = p.data["layers"][1]["motion"]
    p.data["stem_sources"] = {"drums": motion}
    p = Project(p.data, p.base)
    layer = {"id": "stem", "type": "rudiment", "renderer": "waveform", "bounds": [5, 5, 175, 155]}
    p.data["layers"] = [dict(layer, stem_source="missing")]
    with pytest.raises(ValueError, match="Unknown stem_source"):
        Compositor(p)
    p.data["layers"] = [dict(layer, stem_source="drums", motion=motion)]
    with pytest.raises(ValueError, match="either motion or stem_source"):
        Compositor(p)


def test_masked_compositor_random_access_is_stable(tmp_path):
    p = example(tmp_path)
    p.data["layers"] = [
        {
            "id": "matte",
            "type": "rudiment",
            "renderer": "particles",
            "motion": {"kind": "named", "pattern": "circle"},
            "bounds": [0, 0, 180, 160],
        },
        {
            "id": "ink",
            "type": "rudiment",
            "renderer": "waveform",
            "motion": {"kind": "named", "pattern": "bounce"},
            "bounds": [0, 0, 180, 160],
        },
    ]
    p.data["layer_masks"] = {"layer:ink": {"source": "layer:matte"}}
    p = Project(p.data, p.base)
    c = Compositor(p)
    first, _ = c.frame(30)
    c.frame(5)
    assert c.frame(30)[0].tobytes() == first.tobytes()
    assert c.evidence()["layer_masks"]["layer:ink"]["mode"] == "alpha"
