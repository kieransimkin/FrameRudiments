"""Julia-set field generation, periodic controls and compositor integration."""
from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_fields import (
    field_capabilities,
    field_parameter_descriptors,
    render_procedural_field,
    validate_field,
)
from framerudiments.fractals import (
    julia_constant,
    julia_escape_field,
    normalized_turn_radians,
)


def reference(size=(96, 64)):
    return Image.new("RGBA", size, (12, 18, 28, 255))


def julia_config(**overrides):
    config = {
        "field_type": "julia",
        "field_output": "color",
        "field_scale": [28, 28],
        "field_julia_c": [-0.745, 0.113],
        "field_julia_orbit_radius": 0.16,
        "field_julia_iterations": 80,
        "field_palette": [
            {"id": "void", "position": 0, "color": "#05040f"},
            {"id": "indigo", "position": 0.3, "color": "#312e81"},
            {"id": "cyan", "position": 0.62, "color": "#22d3ee"},
            {"id": "gold", "position": 0.86, "color": "#fbbf24"},
            {"id": "white", "position": 1, "color": "#fff7ed"},
        ],
    }
    config.update(overrides)
    return config


def test_normalized_turn_joins_two_pi_endpoints_exactly():
    assert normalized_turn_radians(-1) == normalized_turn_radians(1) == -math.pi
    assert normalized_turn_radians(0) == 0
    assert julia_constant((-.8, .156), .2, -1) == julia_constant((-.8, .156), .2, 1)
    for invalid in (True, -1.001, 1.001, float("nan"), float("inf"), "0"):
        with pytest.raises(ValueError):
            normalized_turn_radians(invalid)


def test_julia_field_is_finite_bounded_and_preserves_inputs():
    y, x = np.indices((17, 23), dtype=np.float64)
    u, v = x / 8 - 1.4, y / 8 - 1
    before_u, before_v = u.copy(), v.copy()
    field = julia_escape_field(
        u, v, constant=(-.745, .113), orbit_radius=.16, phase=.25,
        zoom=1.2, rotation=-.15, iterations=64, escape_radius=4, power=2,
    )
    assert field.shape == u.shape and np.isfinite(field).all()
    assert 0 <= field.min() <= field.max() <= 1 and field.max() > 0
    assert np.array_equal(u, before_u) and np.array_equal(v, before_v)


def test_julia_period_endpoints_are_byte_identical_across_tiles():
    image = reference()
    negative = render_procedural_field(image, julia_config(field_julia_phase=-1), tile_rows=1)
    positive = render_procedural_field(image, julia_config(field_julia_phase=1), tile_rows=23)
    assert negative.tobytes() == positive.tobytes()
    assert render_procedural_field(image, julia_config(field_julia_phase=-1), tile_rows=7).tobytes() == negative.tobytes()


def test_multi_point_gradient_and_fractal_controls_are_exposed():
    config = julia_config()
    output = np.asarray(render_procedural_field(reference(), config))
    colours = np.unique(output[..., :3].reshape(-1, 3), axis=0)
    assert len(colours) > len(config["field_palette"])
    descriptors = field_parameter_descriptors(config)
    for name in (
        "field_julia_c.x", "field_julia_c.y", "field_julia_orbit_radius",
        "field_julia_phase", "field_julia_zoom", "field_julia_rotation",
        "field_julia_iterations", "field_julia_escape_radius", "field_julia_power",
        "field_palette.cyan.color.g",
    ):
        assert descriptors[name]["animatable"] and descriptors[name]["active"]
    assert descriptors["field_julia_phase"]["range"] == [-1, 1]
    assert descriptors["field_julia_rotation"]["units"] == "normalized_turn"
    capabilities = field_capabilities(config)
    fractal = capabilities["fractal"]
    assert fractal["family"] == "julia" and fractal["escape_time"]
    assert fractal["available_families"] == [
        "buffalo", "burning_ship", "celtic", "julia", "mandelbrot", "newton", "tricorn",
    ]
    assert fractal["motion_normalized_range"] == [-1, 1]
    assert fractal["periodic_control_span"] == 2
    assert fractal["periodic_control_radians"] == 2 * math.pi
    assert fractal["joined_endpoints"]
    assert fractal["periodic_controls"] == ["field_julia_phase", "field_julia_rotation"]


@pytest.mark.parametrize("config", [
    {"field_type": "julia", "field_julia_phase": -1.01},
    {"field_type": "julia", "field_julia_rotation": 1.01},
    {"field_type": "julia", "field_julia_iterations": 0},
    {"field_type": "julia", "field_julia_iterations": 3.5},
    {"field_type": "julia", "field_julia_power": 9},
    {"field_type": "julia", "field_julia_c": [0]},
    {"field_type": "julia", "field_julia_escape_radius": 1.9},
])
def test_invalid_julia_configuration_is_rejected(config):
    with pytest.raises(ValueError):
        validate_field(config)


def test_julia_is_a_generated_surface_inside_the_effect_graph():
    authored = julia_config()
    spec = {
        "nodes": [
            {"id": "frame", "type": "source"},
            {"id": "julia", "type": "procedural_field", "inputs": ["frame"], **authored},
            {"id": "fade", "type": "opacity", "inputs": ["julia"], "opacity": .75},
        ],
        "output": "fade",
    }
    graph = EffectGraph(spec)
    saved = deepcopy(graph.nodes["julia"].config)
    a = graph.render({"frame": reference()}, parameter_values={
        ("julia", "field_julia_phase"): -.4,
        ("julia", "field_julia_zoom"): 1.1,
        ("julia", "field_julia_rotation"): -.12,
    })
    b = graph.render({"frame": reference()}, parameter_values={
        ("julia", "field_julia_phase"): .4,
        ("julia", "field_julia_zoom"): 1.35,
        ("julia", "field_julia_rotation"): .12,
    })
    assert graph.source_ids == {"frame"} and a.tobytes() != b.tobytes()
    assert a.getchannel("A").getextrema() == (191, 191)
    assert graph.nodes["julia"].config == saved


def test_public_example_targets_real_julia_controls():
    path = Path(__file__).parents[1] / "examples" / "effect-graph-julia-fractal.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    graph = EffectGraph(document["effect_graph"])
    assert graph.nodes["julia"].config["field_type"] == "julia"
    assert len(graph.nodes["julia"].config["field_palette"]) == 5
    for control in document["effect_graph_controls"]:
        graph.validate_control_target(control["node"], control["property"])
