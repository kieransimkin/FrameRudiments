"""Declarative layer animation and displacement regression coverage."""

from types import SimpleNamespace
from PIL import Image, ImageDraw
import pytest
from framerudiments.layer_animation import LayerAnimations
from framerudiments.layer_displacement import LayerDisplacement

ORDER = ["background", "layer:matte", "layer:ink"]


def frame(seconds):
    return SimpleNamespace(output_seconds=seconds)


def test_animated_opacity_and_translation_seek_are_repeatable():
    animation = LayerAnimations(
        {
            "layer:ink": {
                "opacity": {"keys": [{"time": 0, "value": 0}, {"time": 2, "value": 1}]},
                "x": {"keys": [{"time": 0, "value": 0.5}, {"time": 2, "value": 0.51}]},
            }
        },
        ORDER,
    )
    original = Image.new("RGBA", (36, 24))
    ImageDraw.Draw(original).rectangle((8, 6, 18, 16), fill="red")
    at_one = animation.apply("layer:ink", original, frame(1))
    animation.apply("layer:ink", original, frame(2))
    assert animation.apply("layer:ink", original, frame(1)).tobytes() == at_one.tobytes()
    assert original.getpixel((10, 10))[3] == 255
    assert 0 < at_one.getchannel("A").getextrema()[1] < 255


def test_animated_polygon_vertices_are_seek_stable():
    config = {
        "layer:ink": {
            "clip": {
                "keys": [
                    {"time": 0, "points": [[0, 0], [4, 0], [0, 4]]},
                    {"time": 2, "points": [[0, 0], [22, 0], [0, 22]]},
                ]
            }
        }
    }
    animation = LayerAnimations(config, ORDER)
    image = Image.new("RGBA", (24, 24), "white")
    middle = animation.apply("layer:ink", image, frame(1))
    assert middle.getpixel((4, 4))[3] == 255
    assert middle.getpixel((16, 16))[3] == 0
    animation.apply("layer:ink", image, frame(2))
    assert animation.apply("layer:ink", image, frame(1)).tobytes() == middle.tobytes()


def test_polygon_clip_and_rotation():
    animation = LayerAnimations({"layer:ink": {"clip": [[0, 0], [14, 0], [0, 14]], "rotation": 0}}, ORDER)
    original = Image.new("RGBA", (24, 24), "white")
    result = animation.apply("layer:ink", original, frame(0))
    assert result.getpixel((2, 2))[3] == 255
    assert result.getpixel((20, 20))[3] == 0


def test_displacement_uses_earlier_surface_and_keeps_source_unchanged():
    displacement = LayerDisplacement({"layer:ink": {"source": "layer:matte", "amount": 5}}, ORDER)
    matte = Image.new("RGBA", (20, 10), "white")
    ink = Image.new("RGBA", (20, 10))
    ImageDraw.Draw(ink).rectangle((4, 2, 8, 6), fill="blue")
    moved = displacement.apply("layer:ink", ink, {"layer:matte": matte})
    assert moved.getpixel((9, 4))[3] == 255
    assert ink.getpixel((9, 4))[3] == 0
    assert moved.tobytes() == displacement.apply("layer:ink", ink, {"layer:matte": matte}).tobytes()


@pytest.mark.parametrize(
    "config",
    [
        {"layer:missing": {"x": 1}},
        {"layer:ink": {"unexpected": 1}},
        {"layer:ink": {"clip": [[0, 0], [1, 1]]}},
        {"layer:ink": {"opacity": 1.5}},
    ],
)
def test_animation_invalid_config_fails_closed(config):
    with pytest.raises(ValueError):
        LayerAnimations(config, ORDER)


def test_displacement_cycle_rejected():
    with pytest.raises(ValueError):
        LayerDisplacement({"layer:matte": {"source": "layer:ink", "amount": 3}}, ORDER)
