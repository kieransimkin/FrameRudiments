"""Geometry tests need no audio models or private media; CI builds the Pages gallery."""
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

from framerudiments.rudiment_renderers import default_renderers
from framerudiments.visualisation_catalogue import ALL_VIEWS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from generate_visualisation_gallery import BASE_VIEWS, FRAMES, build_site, fixture  # noqa: E402


@pytest.mark.parametrize("spec", ALL_VIEWS, ids=lambda s: s.name)
def test_every_new_renderer_is_deterministic_and_uses_its_source(spec):
    registry = default_renderers()
    renderer, identity = registry.create(spec.name, (320, 210), {"background": "#142238"})
    assert identity["source"]["sha256"]
    assert identity["source"]["path"].endswith("advanced_renderers.py")
    frame = fixture(95, spectral=spec.family != "motion")
    result = renderer.render(frame)
    assert result.mode == "RGBA" and result.size == (320, 210)
    assert result.tobytes() == renderer.render(frame).tobytes()
    assert result.getbbox() is not None
    assert result.tobytes() != renderer.render(fixture(35, spectral=spec.family != "motion")).tobytes()
    if spec.family != "motion":
        assert renderer.frame_report["kind"] == "stemlab-fourier-band-spectrum"
        with pytest.raises(ValueError, match="calibrated"):
            renderer.render(fixture(35, spectral=False))


@pytest.mark.parametrize("name", ["spectrum-log", "spectrogram-linear", "waveform-area"])
def test_fails_closed_on_unknown_style(name):
    with pytest.raises(ValueError, match="renderer style"):
        default_renderers().create(name, (320, 210), {"unexpected": "foo"})


def test_galleries_cover_all_registered_renderers(tmp_path):
    site = tmp_path / "site"
    manifest = build_site(site)
    names = [view["name"] for view in manifest["views"]]
    assert len(names) == len(set(names))
    assert set(names) == set(default_renderers().names)
    assert len(names) == len(ALL_VIEWS) + len(BASE_VIEWS)
    assert "not_audio_measurements" in manifest and manifest["not_audio_measurements"]
    assert len(list(site.glob("images/*/*.png"))) == len(names) * len(FRAMES)
    saved = json.loads((site / "catalogue.json").read_text())
    assert saved["views"] == manifest["views"]
    assert "waveform" in (site / "index.html").read_text()
    for name in names:
        with Image.open(site / "images" / name / "0.png") as image:
            assert image.size == (480, 270)
