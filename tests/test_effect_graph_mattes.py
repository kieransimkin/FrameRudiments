"""M3 CPU reference and integration tests: alpha mattes, chroma, controls."""

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph


def make(nodes, output="out"):
    return EffectGraph({"nodes": nodes, "output": output})


def matte_graph(**params):
    return make([
        {"id": "fg", "type": "source"},
        {"id": "a", "type": "source"},
        {"id": "b", "type": "source"},
        {"id": "out", "type": "matte", "inputs": ["fg", "a", "b"], **params},
    ])


def rgba(colour, alpha=255):
    return Image.new("RGBA", (1, 1), (*colour, alpha))


@pytest.mark.parametrize("operation, expected", [
    ("multiply", 25), ("min", 50), ("max", 100), ("add", 151),
    ("subtract", 50), ("difference", 50), ("screen", 125),
])
def test_multiple_alpha_matte_combinations(operation, expected):
    fg, a, b = rgba((230, 60, 20), 200), rgba((20, 0, 0), 128), rgba((0, 20, 0), 64)
    originals = [im.tobytes() for im in (fg, a, b)]
    result = matte_graph(mask_combine=operation).render({"fg": fg, "a": a, "b": b})
    assert abs(result.getpixel((0, 0))[3] - expected) <= 2
    assert result.getpixel((0, 0))[:3] == (230, 60, 20)
    assert [im.tobytes() for im in (fg, a, b)] == originals


def test_matte_soft_mask_opacity_and_independent_branch_opacity():
    g = matte_graph(mask_opacity=0.5, opacity=0.5)
    sources = {"fg": rgba((255, 0, 0)), "a": rgba((0, 0, 0), 0), "b": rgba((0, 0, 0), 0)}
    assert g.render(sources).getpixel((0, 0))[3] == 64
    assert g.render(sources, parameter_values={("out", "mask_opacity"): 0}).getpixel((0, 0))[3] in (127, 128)
    assert g.render(sources, parameter_values={("out", "opacity"): 1}).getpixel((0, 0))[3] in (127, 128)
    assert g.base_control_value("out", "mask_opacity") == .5


def test_matte_invert_and_source_alpha_weighted_luminance():
    fg = Image.new("RGBA", (2, 1), "red")
    a = Image.new("RGBA", (2, 1))
    a.putpixel((0, 0), (255, 255, 255, 0))
    a.putpixel((1, 0), (255, 255, 255, 255))
    g = make([{"id": "fg", "type": "source"}, {"id": "a", "type": "source"},
              {"id": "out", "type": "matte", "inputs": ["fg", "a"],
               "matte_channel": "luminance", "invert": True}])
    assert [g.render({"fg": fg, "a": a}).getpixel((i, 0))[3] for i in range(2)] == [255, 0]


def test_morphological_expansion_and_erosion_can_be_modulated():
    fg = Image.new("RGBA", (7, 7), "red")
    a = Image.new("RGBA", (7, 7))
    a.putpixel((3, 3), (0, 0, 0, 255))
    g = make([{"id": "fg", "type": "source"}, {"id": "a", "type": "source"},
              {"id": "out", "type": "matte", "inputs": ["fg", "a"]}])
    sources = {"fg": fg, "a": a}
    assert g.render(sources).getpixel((3, 2))[3] == 0
    spread = g.render(sources, parameter_values={("out", "expand"): 1})
    assert spread.getpixel((3, 2))[3] == 255
    assert spread.getpixel((0, 0))[3] == 0
    half_spread = g.render(sources, parameter_values={("out", "expand"): .5})
    assert abs(half_spread.getpixel((3, 2))[3] - 128) <= 1
    assert g.render(sources).getpixel((3, 2))[3] == 0
    eroded = g.render(sources, parameter_values={("out", "expand"): -1})
    assert eroded.getpixel((3, 3))[3] == 0


def test_feather_is_bounded_and_softens_edges():
    fg = Image.new("RGBA", (11, 11), "red")
    a = Image.new("RGBA", (11, 11))
    a.putpixel((5, 5), (255, 255, 255, 255))
    g = make([{"id": "fg", "type": "source"}, {"id": "a", "type": "source"},
              {"id": "out", "type": "matte", "inputs": ["fg", "a"], "feather": 2}])
    out = g.render({"fg": fg, "a": a})
    assert 0 < out.getpixel((5, 5))[3] < 255
    assert out.getpixel((5, 5))[3] > out.getpixel((5, 4))[3]


def key_graph(**params):
    return make([{"id": "s", "type": "source"},
                 {"id": "out", "type": "chroma_key", "inputs": ["s"], **params}])


def test_multiple_chroma_colours_remove_either_green_or_blue():
    img = Image.new("RGBA", (3, 1))
    img.putpixel((0, 0), (0, 255, 0, 200))
    img.putpixel((1, 0), (0, 0, 255, 255))
    img.putpixel((2, 0), (255, 0, 0, 255))
    g = key_graph(key_colors=["#00ff00", "#0000ff"], tolerance=0, softness=0)
    out = g.render({"s": img})
    assert [out.getpixel((i, 0))[3] for i in range(3)] == [0, 0, 255]
    assert img.getpixel((0, 0))[3] == 200


def test_oklab_perceptual_colour_key_and_backward_compatible_rgb():
    img = Image.new("RGBA", (2, 1))
    img.putpixel((0, 0), (0, 255, 5, 180))
    img.putpixel((1, 0), (0, 0, 255, 190))
    for space in ("oklab", "rgb"):
        g = key_graph(key_space=space, key_color="#00ff00", tolerance=.04, softness=0)
        out = g.render({"s": img})
        assert [out.getpixel((i, 0))[3] for i in range(2)] == [0, 190]


def test_chroma_edge_expand_keeps_source_alpha_limit():
    image = Image.new("RGBA", (5, 1))
    image.putpixel((0, 0), (255, 0, 0, 0))
    image.putpixel((1, 0), (0, 255, 0, 255))
    image.putpixel((2, 0), (255, 0, 0, 255))
    image.putpixel((3, 0), (0, 255, 0, 255))
    image.putpixel((4, 0), (255, 0, 0, 0))
    g = key_graph(tolerance=0, softness=0, edge_expand=1)
    out = g.render({"s": image})
    assert out.getpixel((1, 0))[3] > 0
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((4, 0))[3] == 0


def test_chroma_key_edge_decontamination_respects_opaque_interior():
    img = Image.new("RGBA", (7, 7), (0, 255, 0, 255))
    for y in range(2, 5):
        for x in range(2, 5):
            img.putpixel((x, y), (245, 30, 20, 255))
    img.putpixel((1, 3), (120, 160, 20, 255))
    cfg = dict(tolerance=.15, softness=.5, edge_radius=2)
    basic = key_graph(**cfg).render({"s": img})
    enhanced = key_graph(**cfg, edge_decontaminate=1).render({"s": img})
    assert basic.getpixel((1, 3))[:3] != enhanced.getpixel((1, 3))[:3]
    assert basic.getpixel((3, 3)) == enhanced.getpixel((3, 3))
    assert img.getpixel((1, 3)) == (120, 160, 20, 255)


def test_key_parameters_controlled_per_frame_without_persistent_change():
    g = key_graph(key_color="#00ff00", edge_feather=0, tolerance=0, softness=0)
    img = rgba((0, 250, 0))
    baseline = g.render({"s": img})
    shifted = g.render({"s": img}, parameter_values={
        ("out", "key_color.r"): 255, ("out", "key_color.g"): 0,
        ("out", "opacity"): .5,
    })
    assert baseline.getpixel((0, 0))[3] == 255
    assert shifted.getpixel((0, 0))[3] in (127, 128)
    assert g.render({"s": img}).tobytes() == baseline.tobytes()


@pytest.mark.parametrize("node", [
    {"type": "matte", "mask_combine": "unknown"},
    {"type": "matte", "mask_combine": ["max"]},
    {"type": "matte", "matte_channel": []},
    {"type": "matte", "mask_opacity": 2},
    {"type": "matte", "expand": -65},
    {"type": "matte", "expand": float('nan')},
    {"type": "chroma_key", "key_space": "bogus"},
    {"type": "chroma_key", "key_space": []},
    {"type": "chroma_key", "key_colors": []},
    {"type": "chroma_key", "key_colors": ["#ff0000"] * 9},
    {"type": "chroma_key", "edge_expand": -70},
    {"type": "chroma_key", "edge_decontaminate": True},
    {"type": "chroma_key", "edge_radius": 0},
])
def test_invalid_m3_configurations_fail_during_graph_construction(node):
    kind = node.pop("type")
    inputs = ["s", "m"] if kind == "matte" else ["s"]
    with pytest.raises(ValueError):
        make([{"id": "s", "type": "source"}, {"id": "m", "type": "source"},
              {"id": "out", "type": kind, "inputs": inputs, **node}])


def test_any_node_and_transition_output_support_opacity():
    nodes = [
        {"id": "a", "type": "source", "opacity": .5},
        {"id": "b", "type": "source"},
        {"id": "mix", "type": "transition", "inputs": ["a", "b"],
         "progress": 0, "opacity": .4},
        {"id": "out", "type": "copy", "inputs": ["mix"], "opacity": .5},
    ]
    g = make(nodes)
    src = {"a": rgba((255, 0, 0)), "b": rgba((0, 0, 255))}
    assert abs(g.render(src).getpixel((0, 0))[3] - 26) <= 1
    assert g.render(src, parameter_values={("mix", "opacity"): 1}).getpixel((0, 0))[3] == 64


@pytest.mark.parametrize("property_name,value", [
    ("expand", 65), ("mask_opacity", -1), ("edge_expand", 65),
    ("edge_feather", 129), ("edge_decontaminate", 1.5), ("edge_radius", 16.5),
])
def test_m3_control_ranges_are_enforced(property_name, value):
    kind = "matte" if property_name in ("expand", "mask_opacity") else "chroma_key"
    nodes = [{"id": "s", "type": "source"}, {"id": "m", "type": "source"},
             {"id": "out", "type": kind,
              "inputs": ["s", "m"] if kind == "matte" else ["s"]}]
    g = make(nodes)
    with pytest.raises(ValueError, match="out of range"):
        g.render({"s": rgba((255, 0, 0)), "m": rgba((0, 0, 0))},
                 parameter_values={("out", property_name): value})


def test_discrete_matte_choice_override_and_validation():
    g = matte_graph(mask_combine="multiply")
    src = {"fg": rgba((10, 20, 30)), "a": rgba((0, 0, 0), 200),
           "b": rgba((0, 0, 0), 20)}
    default = g.render(src).getpixel((0, 0))[3]
    maximum = g.render(src, parameter_values={("out", "mask_combine"): "max"})
    assert maximum.getpixel((0, 0))[3] > default
    assert g.render(src).getpixel((0, 0))[3] == default
    for value in (1, True, "invalid"):
        with pytest.raises(ValueError, match="categorical"):
            g.render(src, parameter_values={("out", "mask_combine"): value})


def test_per_key_colour_channels_are_separately_controllable():
    g = key_graph(key_colors=["#00ff00", "#0000ff"], tolerance=0, softness=0)
    src = {"s": rgba((255, 0, 0))}
    assert g.render(src).getpixel((0, 0))[3] == 255
    keyed = g.render(src, parameter_values={
        ("out", "key_colors.0.r"): 255, ("out", "key_colors.0.g"): 0,
    })
    assert keyed.getpixel((0, 0))[3] == 0
    assert g.render(src).getpixel((0, 0))[3] == 255
    assert g.base_control_value("out", "key_colors.0.g") == 255
    with pytest.raises(ValueError, match="control target"):
        g.validate_control_target("out", "key_colors.2.r")


def test_discrete_key_space_can_be_switched_without_rebuilding():
    g = key_graph(key_color="#00ff00", key_space="rgb")
    src = {"s": rgba((0, 250, 0))}
    assert g.render(src, parameter_values={("out", "key_space"): "oklab"}).mode == "RGBA"
    assert g.nodes["out"].config["key_space"] == "rgb"


def test_categorical_controls_require_known_values_and_distinct_choices():
    g = matte_graph()
    g.validate_control_choices("out", "mask_combine", ["max", "min"])
    g.validate_control_choices("out", "invert", [False, True])
    for prop, choices in (("mask_combine", ["max", "max"]),
                          ("invert", [False, 0]),
                          ("mask_combine", ["max", "bogus"]),
                          ("feather", [1, 2])):
        with pytest.raises(ValueError):
            g.validate_control_choices("out", prop, choices)


def test_transition_and_composite_can_take_enum_control_overrides():
    a, b = rgba((255, 0, 0)), rgba((0, 0, 255))
    g = make([
        {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
        {"id": "t", "type": "transition", "inputs": ["a", "b"], "progress": .5},
        {"id": "out", "type": "composite", "inputs": ["a", "t"]},
    ])
    result = g.render({"a": a, "b": b}, parameter_values={
        ("t", "transition_style"): "wipe", ("t", "direction"): "left",
        ("out", "mode"): "add"})
    assert result.mode == "RGBA"
    assert g.base_control_value("out", "mode") == "over"
    assert g.base_control_value("t", "transition_style") == "dissolve"


def test_primary_key_colour_controls_alias_first_multikey_when_no_primary():
    g = key_graph(key_colors=["#00ff00", "#0000ff"], tolerance=0, softness=0)
    src = {"s": rgba((255, 0, 0))}
    assert g.base_control_value("out", "key_color.g") == 255
    result = g.render(src, parameter_values={
        ("out", "key_color.r"): 255, ("out", "key_color.g"): 0})
    assert result.getpixel((0, 0))[3] == 0
    assert g.render(src).getpixel((0, 0))[3] == 255


def test_forked_matte_key_and_transition_chain_is_random_access_deterministic():
    g = make([
        {"id": "a", "type": "source"},
        {"id": "b", "type": "source"},
        {"id": "mask", "type": "source"},
        {"id": "keyed", "type": "chroma_key", "inputs": ["b"],
         "key_color": "#00ff00", "tolerance": 0, "softness": 0},
        {"id": "masked", "type": "matte", "inputs": ["keyed", "mask"],
         "mask_opacity": 1, "opacity": .5},
        {"id": "out", "type": "transition", "inputs": ["a", "masked"],
         "progress": 0},
    ])
    background = Image.new("RGBA", (2, 1), "blue")
    candidate = Image.new("RGBA", (2, 1))
    candidate.putpixel((0, 0), (0, 255, 0, 255))
    candidate.putpixel((1, 0), (255, 0, 0, 255))
    track = Image.new("RGBA", (2, 1), (0, 0, 0, 255))
    providers = {"a": background, "b": candidate, "mask": track}
    end = g.render(providers, parameter_values={("out", "progress"): 1})
    beginning = g.render(providers, parameter_values={("out", "progress"): 0})
    assert [end.getpixel((x, 0))[3] for x in range(2)] == [0, 128]
    assert beginning.tobytes() == background.tobytes()
    assert g.render(providers, parameter_values={("out", "progress"): 1}).tobytes() == end.tobytes()
    assert candidate.getpixel((1, 0)) == (255, 0, 0, 255)
