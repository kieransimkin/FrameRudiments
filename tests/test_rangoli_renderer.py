from types import SimpleNamespace

import pytest

from framerudiments.rangoli_renderer import RangoliRenderer
from framerudiments.rudiment_renderers import default_renderers


def frame(values):
    return SimpleNamespace(values=values, context=SimpleNamespace(source_seconds=42))


def test_registered_and_independent_channels():
    assert "rangoli" in default_renderers().names
    r = RangoliRenderer((400, 400), dict(radius=.45, scale_gain=.6, rotation_gain=180,
                                        scale_axis=0, rotation_axis=1))
    a = r.render(frame((0, 0)))
    b = r.render(frame((1, .25)))
    assert a.mode == "RGBA" and a.size == (400, 400)
    assert a.tobytes() != b.tobytes()
    assert r.frame_report["scale"] == 1.6
    assert r.frame_report["rotation_degrees"] == 45
    assert r.frame_report["source_seconds"] == 42
    assert r.render(frame((0, 0))).tobytes() == a.tobytes()


def test_no_clipping_or_guessed_axis():
    r = RangoliRenderer((100, 100), dict(radius=1, scale_gain=.6))
    with pytest.raises(ValueError, match="exceed viewport"):
        r.render(frame((1,)))
    r = RangoliRenderer((100, 100), dict(rotation_axis=2))
    with pytest.raises(ValueError, match="axis"):
        r.render(frame((0,)))


@pytest.mark.parametrize("style", [{"petals": True}, {"petals": 1000},
                                  {"radius": float("nan")}, {"unknown": 1}])
def test_style_validation(style):
    with pytest.raises(ValueError):
        RangoliRenderer((100, 100), style)


def test_supersampled_geometry():
    r = RangoliRenderer((800, 800), dict(radius=.45, line_width=8))
    image = r.render(frame((0,)))
    bbox = image.getchannel("A").getbbox()
    assert bbox[0] > 200 and bbox[2] < 600
