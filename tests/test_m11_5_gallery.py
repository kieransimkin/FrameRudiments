"""M11.5 provenance, screenshots and real effect graph integration."""
from hashlib import sha1
import importlib.util
from pathlib import Path
import sys

import pytest
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_m11_5_gallery.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("generate_m11_5_gallery", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_git_blob_identity_matches_git_protocol():
    data = b"hello\n"
    expected = sha1(b"blob 6\x00hello\n").hexdigest()
    assert mod.git_blob_sha(data) == expected
    assert mod.git_blob_sha(data + b"!") != expected


def test_real_source_is_immutable_commit_not_branch():
    assert len(mod.UPSTREAM_COMMIT) == 40
    assert not any(name in mod.UPSTREAM_COMMIT for name in ("main", "master", "latest"))
    assert set(mod.CAPTURES) == {"rest", "raised"}
    assert all(len(blob_sha) == 40 for _path, blob_sha in mod.CAPTURES.values())


def test_contain_preserves_sides_for_tall_character_image():
    image = Image.new("RGBA", (80, 240), "red")
    output = mod.fit_image(image)
    assert output.size == (480, 270)
    assert output.getpixel((0, 0)) == (9, 16, 34, 255)
    assert output.getpixel((240, 100)) == (255, 0, 0, 255)


def test_matte_changes_alpha_with_frame_position():
    first = mod.mask_at(0, 48)
    last = mod.mask_at(47, 48)
    assert first.getpixel((0, 0))[3] == 0
    assert first.getpixel((240, 135))[3] == 255
    assert last.getpixel((0, 0))[3] == 255
    assert first.tobytes() != last.tobytes()


def test_side_by_side_is_real_rgb_image():
    src = Image.new("RGBA", mod.SIZE, "red")
    native = Image.new("RGBA", mod.SIZE, "blue")
    mixed = Image.new("RGBA", mod.SIZE, "purple")
    result = mod.side_by_side(src, native, mixed, "dissolve")
    assert result.size == (1440, 306) and result.mode == "RGB"
    assert result.getpixel((0, 50)) == (255, 0, 0)
    assert result.getpixel((500, 50)) == (0, 0, 255)


def test_html_marks_synthetic_not_measured_and_poses_not_rigged():
    record = {"asset_source": "upstream_kandi_boom_captures", "scenes": [{
        "id": "dissolve", "poster": "dissolve-poster.png", "video": {"path": "dissolve.mp4"},
    }]}
    output = mod.site_html(record)
    assert "not as animated rig output" in output
    assert "Synthetic spectral display values" in output
    assert '<video controls' in output


def test_receipt_rejects_missing_html_and_wrong_checksums(tmp_path):
    target = tmp_path / "m11-5"
    target.mkdir()
    (target / "catalogue.json").write_text('{"schema":"broken","scenes":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="incomplete"):
        mod.verify(target)


@pytest.mark.parametrize("style", mod.SCENES)
def test_actual_effect_graph_allows_read_only_frame_overrides(style):
    from framerudiments.effect_graph import EffectGraph

    graph = mod.graph_for(style)
    source = Image.new("RGBA", mod.SIZE, (255, 193, 138, 255))
    visual = Image.new("RGBA", mod.SIZE, (66, 214, 245, 255))
    inputs = {"character": source, "visualiser": visual}
    if style == "matte":
        inputs["mask"] = mod.mask_at(12, 48)
    original = {name: image.tobytes() for name, image in inputs.items()}
    first = graph.render(inputs, parameter_values={("blend", "progress"): 0})
    final = graph.render(inputs, parameter_values={("blend", "progress"): 1})
    assert isinstance(graph, EffectGraph)
    assert first.tobytes() == source.tobytes()
    assert final.tobytes() == visual.tobytes()
    assert original == {name: image.tobytes() for name, image in inputs.items()}


def test_real_comparison_gallery_with_offline_explicit_test_poses(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text('<header><h1>Existing catalogue</h1></header>', encoding="utf-8")
    pictures = {"rest": Image.new("RGBA", (128, 200), "#af7a60"),
                "raised": Image.new("RGBA", (128, 200), "#52abde")}
    record = mod.build(site, poses=pictures, frame_count=4, encode=False)
    assert record["asset_source"] == "test_fixture_not_kandi_boom"
    assert mod.verify(site / "m11-5") == record
    assert (site / "m11-5" / "spectral-poster.png").exists()
    assert 'href="m11-5/index.html"' in (site / "index.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        mod.build(site, poses=pictures, frame_count=4, encode=False)
