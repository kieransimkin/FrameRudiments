import json
import os
import pytest
from PIL import Image, ImageDraw
from framerudiments.project import Project
from framerudiments.demo import create_demo
from framerudiments.compositor import Compositor
from framerudiments.io import sha256


def fixture(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        pytest.skip("Set FRAMERUDIMENTS_TEST_FONT")
    path = create_demo(tmp_path / "demo", font)
    return path, json.loads(path.read_text())


def test_canonical_text_requires_exact_words(tmp_path):
    path, data = fixture(tmp_path)
    canonical = path.parent / "canonical.txt"
    canonical.write_text("Signals into motion\nWords into light\nKeep the clock in sight\n", encoding="utf-8")
    data["canonical_lyrics"] = dict(path=str(canonical), sha256=sha256(canonical))
    Project(data, path.parent)
    canonical.write_text("Changed word\n", encoding="utf-8")
    data["canonical_lyrics"]["sha256"] = sha256(canonical)
    with pytest.raises(ValueError, match="do not match"):
        Project(data, path.parent)


def test_lyric_art_exact_text_lookup_and_required_assets(tmp_path):
    path, data = fixture(tmp_path)
    art = Image.new("RGBA", (100, 30))
    ImageDraw.Draw(art).rectangle((0, 0, 99, 29), fill="#ff0000")
    artpath = path.parent / "lyric-art.png"
    art.save(artpath)
    data["lyric_art"] = dict(items=[dict(text="Signals into motion", asset=dict(path=str(artpath), sha256=sha256(artpath)))],
                             placement=dict(center=[180, 220], width=100, bounds=[60, 160, 300, 280]), fallback="text")
    project = Project(data, path.parent)
    compositor = Compositor(project)
    frame, _ = compositor.frame(0)
    assert frame.getpixel((180, 220)) == (255, 0, 0)
    data["lyric_art"]["fallback"] = "error"
    with pytest.raises(ValueError, match="Missing lyric"):
        Compositor(Project(data, path.parent))
