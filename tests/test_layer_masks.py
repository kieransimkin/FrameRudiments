"""Coverage of pinned StemLab recording aliases and deterministic track mattes."""

import pytest
from PIL import Image, ImageDraw
from framerudiments.layer_masks import LayerMasks

SIZE = (12, 12)


def paint(colour, bounds):
    image = Image.new("RGBA", SIZE)
    ImageDraw.Draw(image).rectangle(bounds, fill=colour)
    return image


def test_alpha_mask_preserves_colour_and_scales_alpha():
    matte = paint((255, 255, 255, 128), (0, 0, 5, 11))
    ink = paint((255, 40, 10, 255), (0, 0, 11, 11))
    masks = LayerMasks({"layer:ink": {"source": "layer:matte"}}, ["background", "layer:matte", "layer:ink"])
    result = masks.apply("layer:ink", ink, {"layer:matte": matte})
    assert result.getpixel((2, 2)) == (255, 40, 10, 128)
    assert result.getpixel((9, 2))[3] == 0
    assert ink.getpixel((9, 2))[3] == 255


def test_luma_inverted_and_missing_source():
    masks = LayerMasks(
        {"layer:ink": {"source": "layer:matte", "mode": "luminance", "invert": True}},
        ["background", "layer:matte", "layer:ink"],
    )
    ink = Image.new("RGBA", SIZE, "red")
    black = Image.new("RGBA", SIZE, "black")
    assert masks.apply("layer:ink", ink, {"layer:matte": black}).getpixel((3, 3))[3] == 255
    assert masks.apply("layer:ink", ink, {}).getpixel((3, 3))[3] == 255
    white = Image.new("RGBA", SIZE, "white")
    assert masks.apply("layer:ink", ink, {"layer:matte": white}).getpixel((3, 3))[3] == 0


@pytest.mark.parametrize(
    "config",
    [
        {"layer:matte": {"source": "layer:ink"}},  # forward reference
        {"layer:ink": {"source": "layer:ink"}},  # cycle
        {"layer:missing": {"source": "layer:matte"}},
        {"layer:ink": {"source": "layer:missing"}},
        {"layer:ink": {"source": "layer:matte", "mode": "nope"}},
        {"layer:ink": {"source": "layer:matte", "invert": 1}},
    ],
)
def test_reject_invalid_mask_graphs(config):
    with pytest.raises(ValueError):
        LayerMasks(config, ["background", "layer:matte", "layer:ink"])
