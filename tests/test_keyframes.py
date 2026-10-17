"""Deterministic keyed alpha/luminance matte transitions."""

from types import SimpleNamespace

import pytest
from PIL import Image
from framerudiments.keyframes import validate_envelope, sample_envelope
from framerudiments.layer_masks import LayerMasks


def test_keyframe_easing_and_clamped_seek():
    keys = validate_envelope(
        {
            "keys": [
                {"time": 0, "value": 0, "ease": "smoothstep"},
                {"time": 2, "value": 1, "ease": "hold"},
                {"time": 4, "value": 0},
            ]
        }
    )
    assert sample_envelope(keys, -1) == 0
    assert sample_envelope(keys, 1) == 0.5
    assert sample_envelope(keys, 3) == 1
    assert sample_envelope(keys, 5) == 0
    assert sample_envelope(keys, 1) == 0.5


@pytest.mark.parametrize(
    "bad",
    [
        {"keys": []},
        {"keys": [{"time": 0, "value": 2}]},
        {"keys": [{"time": 0, "value": 0}, {"time": 0, "value": 1}]},
        {"keys": [{"time": 0, "value": 0, "ease": "bogus"}]},
        {"keys": [{"time": True, "value": 0}]},
    ],
)
def test_bad_keyframes_fail_closed(bad):
    with pytest.raises(ValueError):
        validate_envelope(bad)


def test_keyframed_mask_random_access_and_source_unchanged():
    config = {
        "layer:ink": {
            "source": "layer:matte",
            "opacity": {"keys": [{"time": 0, "value": 0}, {"time": 2, "value": 1}]},
        }
    }
    masks = LayerMasks(config, ["background", "layer:matte", "layer:ink"])
    matte = Image.new("RGBA", (8, 8), "white")
    ink = Image.new("RGBA", (8, 8), "red")

    def at(t):
        return masks.apply(
            "layer:ink", ink, {"layer:matte": matte}, SimpleNamespace(output_seconds=t)
        ).getpixel((3, 3))[3]

    assert [at(t) for t in (2, 0, 1, 2)] == [255, 0, 128, 255]
    assert ink.getpixel((3, 3))[3] == 255
