"""M5.1: three-input matte/luma/chroma transitions and native-ready controls."""

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph


def scene(mask, **configuration):
    nodes = [
        {"id": "a", "type": "source"},
        {"id": "b", "type": "source"},
        {"id": "mask", "type": "source"},
        {"id": "out", "type": "transition", "transition_style": "matte",
         "inputs": ["a", "b", "mask"], "progress": .5, **configuration},
    ]
    return EffectGraph({"nodes": nodes, "output": "out"})


def sources(mask):
    return {
        "a": Image.new("RGBA", mask.size, (255, 0, 0, 255)),
        "b": Image.new("RGBA", mask.size, (0, 0, 255, 255)),
        "mask": mask,
    }


def mask_pixels(*pixels):
    image = Image.new("RGBA", (len(pixels), 1))
    for x, color in enumerate(pixels):
        image.putpixel((x, 0), color)
    return image


def rgb(image):
    return [image.getpixel((x, 0)) for x in range(image.width)]


def test_alpha_matte_reveals_high_alpha_before_low_alpha():
    mask = mask_pixels((0, 0, 0, 255), (0, 0, 0, 160), (0, 0, 0, 70), (0, 0, 0, 0))
    image = scene(mask).render(sources(mask))
    assert [pixel[2] for pixel in rgb(image)] == [255, 255, 0, 0]


def test_luminance_matte_multiplies_alpha_and_preserves_transparent_pixels():
    mask = mask_pixels((255, 255, 255, 255), (255, 255, 255, 0),
                       (0, 0, 0, 255), (255, 255, 255, 64))
    out = scene(mask, matte_channel="luminance").render(sources(mask))
    assert [pixel[2] for pixel in rgb(out)] == [255, 0, 0, 0]


def test_inverted_matte_reveals_the_opposite_mask():
    mask = mask_pixels((0, 0, 0, 255), (0, 0, 0, 0))
    out = scene(mask, invert=True).render(sources(mask))
    assert [pixel[2] for pixel in rgb(out)] == [0, 255]


def test_feathered_matte_blends_near_threshold_and_preserves_alpha():
    mask = mask_pixels((0, 0, 0, 128))
    out = scene(mask, feather=.5).render(sources(mask))
    r, g, b, a = out.getpixel((0, 0))
    assert 124 <= r <= 130 and 124 <= b <= 131 and g == 0 and a == 255


def test_threshold_and_gamma_controls_change_spatial_reveal_without_mutation():
    mask = mask_pixels((0, 0, 0, 153))
    graph = scene(mask)
    original = graph.render(sources(mask)).getpixel((0, 0))
    higher = graph.render(sources(mask), parameter_values={("out", "threshold"): .8})
    lower = graph.render(sources(mask), parameter_values={("out", "threshold"): .2})
    gamma = graph.render(sources(mask), parameter_values={("out", "mask_gamma"): 2})
    assert original == (0, 0, 255, 255)
    assert higher.getpixel((0, 0)) == (255, 0, 0, 255)
    assert lower.getpixel((0, 0)) == (0, 0, 255, 255)
    assert gamma.getpixel((0, 0)) == (255, 0, 0, 255)
    assert graph.render(sources(mask)).getpixel((0, 0)) == original
    assert graph.base_control_value("out", "mask_gamma") == 1
    assert graph.base_control_value("out", "threshold") == .5


def test_chroma_matte_reveals_keyed_colour_not_background():
    mask = mask_pixels((0, 255, 0, 255), (255, 0, 0, 255), (0, 255, 0, 0))
    graph = scene(mask, matte_channel="chroma", key_color="#00ff00", softness=0, tolerance=.02)
    image = graph.render(sources(mask))
    assert [pixel[2] for pixel in rgb(image)] == [255, 0, 0]


@pytest.mark.parametrize('space', ['rgb', 'oklab'])
def test_chroma_matte_rgb_and_oklab_with_soft_edges(space):
    mask = mask_pixels((0, 255, 0, 255), (255, 0, 0, 255))
    graph = scene(mask, matte_channel="chroma", key_space=space, softness=.05, tolerance=.05)
    image = graph.render(sources(mask))
    assert image.getpixel((0, 0))[2] == 255
    assert image.getpixel((1, 0))[2] == 0


def test_chroma_key_colour_components_can_be_music_driven():
    mask = mask_pixels((255, 0, 0, 255))
    graph = scene(mask, matte_channel="chroma", key_color="#00ff00", softness=0, tolerance=.02)
    s = sources(mask)
    before = graph.render(s)
    after = graph.render(s, parameter_values={
        ("out", "key_color.r"): 255, ("out", "key_color.g"): 0,
    })
    assert before.getpixel((0, 0)) == (255, 0, 0, 255)
    assert after.getpixel((0, 0)) == (0, 0, 255, 255)
    assert graph.render(s).tobytes() == before.tobytes()
    assert graph.base_control_value("out", "key_color.g") == 255


def test_categorical_matte_controls_and_choice_validation():
    mask = mask_pixels((255, 255, 255, 0))
    graph = scene(mask)
    graph.validate_control_choices("out", "matte_channel", ["alpha", "luminance", "chroma"])
    graph.validate_control_choices("out", "invert", [False, True])
    changed = graph.render(sources(mask), parameter_values={("out", "invert"): True})
    assert changed.getpixel((0, 0)) == (0, 0, 255, 255)
    assert graph.render(sources(mask)).getpixel((0, 0)) == (255, 0, 0, 255)


def test_progress_exact_endpoints_and_random_access():
    mask = mask_pixels((0, 0, 0, 0), (0, 0, 0, 255))
    graph = scene(mask)
    s = sources(mask)
    unchanged = {k: im.tobytes() for k, im in s.items()}
    at_end = graph.render(s, parameter_values={("out", "progress"): 1})
    at_beginning = graph.render(s, parameter_values={("out", "progress"): 0})
    midway = graph.render(s, parameter_values={("out", "progress"): .5})
    assert at_end.tobytes() == unchanged["b"]
    assert at_beginning.tobytes() == unchanged["a"]
    assert graph.render(s, parameter_values={("out", "progress"): .5}).tobytes() == midway.tobytes()
    assert {k: im.tobytes() for k, im in s.items()} == unchanged


def test_three_input_matte_can_be_processed_fork_or_branch_output():
    mask = mask_pixels((255, 255, 255, 255), (255, 255, 255, 0))
    g = EffectGraph({"nodes": [
        {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
        {"id": "mask", "type": "source"},
        {"id": "dim", "type": "opacity", "inputs": ["mask"], "opacity": .2},
        {"id": "t", "type": "transition", "inputs": ["a", "b", "dim"],
         "transition_style": "matte", "threshold": .15, "progress": .5},
        {"id": "out", "type": "composite", "inputs": ["a", "t"]},
    ], "output": "out"})
    src = sources(mask)
    changed = g.render(src)
    assert changed.size == mask.size
    assert g.render(src).tobytes() == changed.tobytes()
    assert src["mask"].getpixel((0, 0))[3] == 255


@pytest.mark.parametrize('setting', [
    {"matte_channel": "depth"}, {"matte_channel": []}, {"invert": 1},
    {"threshold": -1}, {"threshold": 1.1}, {"threshold": float('nan')},
    {"mask_gamma": 0}, {"mask_gamma": 8.1}, {"mask_gamma": True},
    {"key_color": "not-a-colour", "matte_channel": "chroma"},
    {"key_space": "invalid", "matte_channel": "chroma"},
    {"tolerance": -1, "matte_channel": "chroma"},
])
def test_invalid_matte_transition_settings_rejected_early(setting):
    mask = mask_pixels((0, 0, 0, 255))
    with pytest.raises(ValueError):
        scene(mask, **setting)


def test_matte_transition_requires_three_inputs_and_other_styles_require_two():
    with pytest.raises(ValueError, match="wrong input count"):
        EffectGraph({"nodes": [
            {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
            {"id": "t", "type": "transition", "transition_style": "matte", "inputs": ["a", "b"]},
        ], "output": "t"})
    with pytest.raises(ValueError, match="wrong input count"):
        EffectGraph({"nodes": [
            {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
            {"id": "m", "type": "source"},
            {"id": "t", "type": "transition", "inputs": ["a", "b", "m"]},
        ], "output": "t"})


def test_style_choices_cannot_change_graph_input_arity():
    mask = mask_pixels((0, 0, 0, 255))
    graph = scene(mask)
    with pytest.raises(ValueError, match="input count"):
        graph.validate_control_choices("out", "transition_style", ["wipe", "matte"])


def test_disallowed_matte_settings_on_two_input_transitions():
    with pytest.raises(ValueError, match="matte-only"):
        EffectGraph({"nodes": [
            {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
            {"id": "t", "type": "transition", "inputs": ["a", "b"], "threshold": .3},
        ], "output": "t"})


@pytest.mark.parametrize('property_name,value', [
    ("threshold", 2), ("mask_gamma", -.5), ("mask_gamma", 9),
    ("invert", 1), ("matte_channel", "not-real"), ("softness", -1),
    ("key_color.r", 256),
])
def test_invalid_modulated_matte_parameters_fail_closed(property_name, value):
    mask = mask_pixels((0, 255, 0, 255))
    graph = scene(mask, matte_channel="chroma", key_color="#00ff00")
    with pytest.raises(ValueError):
        graph.render(sources(mask), parameter_values={("out", property_name): value})
