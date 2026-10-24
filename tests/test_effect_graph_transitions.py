"""Contract and regression tests for two-input deterministic graph transitions."""

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph


def make_graph(style="dissolve", **settings):
    return EffectGraph({
        "nodes": [
            {"id": "a", "type": "source"},
            {"id": "b", "type": "source"},
            {"id": "transition", "type": "transition", "inputs": ["a", "b"],
             "transition_style": style, **settings},
        ],
        "output": "transition",
    })


def sources(size=(5, 3)):
    return {
        "a": Image.new("RGBA", size, (255, 0, 0, 255)),
        "b": Image.new("RGBA", size, (0, 0, 255, 255)),
    }


def test_transition_endpoints_are_exact_and_do_not_mutate_inputs():
    input_images = sources()
    snapshots = {key: image.tobytes() for key, image in input_images.items()}
    graph = make_graph(progress=0)
    assert graph.render(input_images).tobytes() == snapshots["a"]
    assert graph.render(input_images, parameter_values={("transition", "progress"): 1}).tobytes() == snapshots["b"]
    assert {key: image.tobytes() for key, image in input_images.items()} == snapshots
    assert graph.nodes["transition"].config["progress"] == 0


def test_halfway_dissolve_is_symmetric_and_stable():
    graph = make_graph(progress=.5)
    images = sources((3, 2))
    first = graph.render(images).tobytes()
    assert first == graph.render(images).tobytes()
    pixel = graph.render(images).getpixel((0, 0))
    assert pixel == (128, 0, 128, 255)


def test_transparent_source_cannot_bleed_hidden_rgb():
    graph = make_graph(progress=.5)
    images = {
        "a": Image.new("RGBA", (1, 1), (0, 255, 0, 0)),
        "b": Image.new("RGBA", (1, 1), (240, 32, 16, 255)),
    }
    # Premultiplied interpolation must retain the visible source's colour.
    assert graph.render(images).getpixel((0, 0)) == (240, 32, 16, 128)


@pytest.mark.parametrize("direction,expected", [
    ("right", (0, 0)), ("left", (4, 0)),
    ("down", (0, 0)), ("up", (0, 2)),
])
def test_wipe_reveals_from_requested_edge(direction, expected):
    graph = make_graph("wipe", direction=direction, progress=.22)
    output = graph.render(sources())
    assert output.getpixel(expected) == (0, 0, 255, 255)
    assert output.getpixel((2, 1)) == (255, 0, 0, 255)


def test_feathered_wipe_is_soft_near_boundary():
    graph = make_graph("wipe", direction="right", progress=.5, feather=.5)
    pix = graph.render(sources()).getpixel((2, 1))
    assert 0 < pix[0] < 255
    assert 0 < pix[2] < 255


def test_radial_transition_reveals_center_before_corners():
    graph = make_graph("radial", progress=.25, center=[.5, .5])
    output = graph.render(sources((7, 7)))
    assert output.getpixel((3, 3)) == (0, 0, 255, 255)
    assert output.getpixel((0, 0)) == (255, 0, 0, 255)


def test_native_parameter_overrides_are_frame_local():
    graph = make_graph("radial", progress=.25)
    images = sources((7, 7))
    original = graph.render(images).tobytes()
    changed = graph.render(images, parameter_values={
        ("transition", "progress"): .8,
        ("transition", "center.x"): 0,
        ("transition", "center.y"): 0,
    }).tobytes()
    assert changed != original
    assert graph.render(images).tobytes() == original
    assert graph.base_control_value("transition", "center.x") == .5
    graph.validate_control_target("transition", "feather")


@pytest.mark.parametrize("properties,diagnostic", [
    ({"progress": -0.1}, "progress"),
    ({"progress": float("nan")}, "progress"),
    ({"feather": 2}, "feather"),
    ({"transition_style": "spiral"}, "style"),
    ({"direction": "diagonal"}, "direction"),
    ({"center": [1.1, .5]}, "center"),
    ({"center": [0, True]}, "center"),
])
def test_invalid_transition_configuration_rejected(properties, diagnostic):
    with pytest.raises(ValueError, match=diagnostic):
        make_graph(**properties)


def test_input_count_and_control_bounds_are_validated():
    with pytest.raises(ValueError, match="wrong input count"):
        EffectGraph({"nodes": [
            {"id": "a", "type": "source"},
            {"id": "t", "type": "transition", "inputs": ["a"]},
        ], "output": "t"})
    g = make_graph()
    with pytest.raises(ValueError, match="out of range"):
        g.render(sources(), parameter_values={("transition", "progress"): 1.2})
    with pytest.raises(ValueError, match="out of range"):
        g.render(sources(), parameter_values={("transition", "center.x"): -1})
    with pytest.raises(ValueError, match="inapplicable"):
        g.validate_control_target("a", "progress")


def test_wipe_forks_preserve_isolated_sources():
    g = EffectGraph({"nodes": [
        {"id": "a", "type": "source"},
        {"id": "b", "type": "source"},
        {"id": "t", "type": "transition", "inputs": ["a", "b"], "progress": .5},
        {"id": "out", "type": "composite", "inputs": ["t", "a"], "mode": "over"},
    ], "output": "out"})
    images = sources()
    before = images["a"].tobytes()
    result = g.render(images)
    assert result.tobytes() == before
    assert images["a"].tobytes() == before
