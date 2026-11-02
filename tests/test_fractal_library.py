"""Expanded fractal families, normalized motion and compositor contracts."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from framerudiments import (
    ESCAPE_FRACTAL_FAMILIES, FRACTAL_FAMILIES, escape_time_field,
    newton_fractal_field,
)
from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_fields import (
    field_capabilities, field_parameter_descriptors, render_procedural_field,
    validate_field,
)


PALETTE = [
    {"id": "ink", "position": 0, "color": "#030712"},
    {"id": "violet", "position": .28, "color": "#6d28d9"},
    {"id": "cyan", "position": .57, "color": "#06b6d4"},
    {"id": "amber", "position": .81, "color": "#f59e0b"},
    {"id": "paper", "position": 1, "color": "#fff7ed"},
]


def reference(size=(84, 60)):
    return Image.new("RGBA", size, (8, 12, 22, 255))


def config(family, **overrides):
    value = {
        "field_type": family,
        "field_output": "color",
        "field_scale": [24, 24],
        "field_palette": PALETTE,
    }
    if family == "julia":
        value.update(field_julia_c=[-.745, .113], field_julia_orbit_radius=.14,
                     field_julia_iterations=72)
    elif family == "newton":
        value.update(field_newton_iterations=40, field_newton_degree=3)
    else:
        value.update(field_fractal_iterations=72)
    value.update(overrides)
    return value


@pytest.mark.parametrize("family", sorted(ESCAPE_FRACTAL_FAMILIES))
def test_escape_family_public_api_is_bounded_finite_and_immutable(family):
    y, x = np.indices((19, 27), dtype=np.float64)
    u, v = x / 9 - 1.45, y / 9 - 1.0
    before = u.copy(), v.copy()
    kwargs = dict(family=family, iterations=56, power=2, zoom=1.15,
                  rotation=.19, phase=-.33, orbit_radius=.08)
    if family == "julia":
        kwargs["center"] = (-.745, .113)
    field = escape_time_field(u, v, **kwargs)
    assert field.shape == u.shape and np.isfinite(field).all()
    assert 0 <= field.min() <= field.max() <= 1 and field.max() > 0
    assert np.array_equal(u, before[0]) and np.array_equal(v, before[1])


def test_newton_public_api_is_bounded_finite_and_immutable():
    y, x = np.indices((19, 27), dtype=np.float64)
    u, v = x / 9 - 1.45, y / 9 - 1.0
    before = u.copy(), v.copy()
    field = newton_fractal_field(
        u, v, iterations=40, degree=5, zoom=1.2, rotation=-.2,
        phase=.4, relaxation=.9,
    )
    assert field.shape == u.shape and np.isfinite(field).all()
    assert 0 <= field.min() <= field.max() <= 1 and field.max() > 0
    assert np.array_equal(u, before[0]) and np.array_equal(v, before[1])


def test_all_families_are_visually_distinct_and_tiled_deterministically():
    outputs = {}
    for family in sorted(FRACTAL_FAMILIES):
        first = render_procedural_field(reference(), config(family), tile_rows=1)
        assert render_procedural_field(reference(), config(family), tile_rows=17).tobytes() == first.tobytes()
        outputs[family] = first.tobytes()
    assert len(set(outputs.values())) == len(FRACTAL_FAMILIES)


@pytest.mark.parametrize("family", ["mandelbrot", "tricorn", "burning_ship", "celtic", "buffalo"])
@pytest.mark.parametrize("control", ["field_fractal_phase", "field_fractal_rotation"])
def test_escape_family_period_endpoints_are_byte_identical(family, control):
    negative = render_procedural_field(reference(), config(family, **{control: -1}), tile_rows=3)
    positive = render_procedural_field(reference(), config(family, **{control: 1}), tile_rows=19)
    assert negative.tobytes() == positive.tobytes()


@pytest.mark.parametrize("control", ["field_newton_phase", "field_newton_rotation"])
def test_newton_period_endpoints_are_byte_identical(control):
    negative = render_procedural_field(reference(), config("newton", **{control: -1}), tile_rows=3)
    positive = render_procedural_field(reference(), config("newton", **{control: 1}), tile_rows=19)
    assert negative.tobytes() == positive.tobytes()


@pytest.mark.parametrize("family", sorted(FRACTAL_FAMILIES))
def test_each_family_exposes_native_motion_and_shared_gradient(family):
    authored = config(family)
    descriptors = field_parameter_descriptors(authored)
    prefix = "field_julia" if family == "julia" else "field_newton" if family == "newton" else "field_fractal"
    for name in (f"{prefix}_phase", f"{prefix}_rotation"):
        assert descriptors[name]["active"] and descriptors[name]["animatable"]
        assert descriptors[name]["range"] == [-1, 1]
        assert descriptors[name]["units"] == "normalized_turn"
    assert descriptors["field_palette.cyan.color.b"]["active"]
    capabilities = field_capabilities(authored)["fractal"]
    assert capabilities["family"] == family
    assert capabilities["available_families"] == sorted(FRACTAL_FAMILIES)


@pytest.mark.parametrize("authored", [
    {"field_type": "mandelbrot", "field_fractal_phase": 1.01},
    {"field_type": "buffalo", "field_fractal_power": 2.5},
    {"field_type": "celtic", "field_fractal_center": [0]},
    {"field_type": "newton", "field_newton_degree": 2},
    {"field_type": "newton", "field_newton_tolerance": 0},
    {"field_type": "newton", "field_newton_relaxation": 2.01},
])
def test_invalid_fractal_configuration_is_rejected(authored):
    with pytest.raises(ValueError):
        validate_field(authored)


def test_fractal_controls_flow_through_effect_graph_without_mutation():
    authored = config("burning_ship")
    graph = EffectGraph({
        "nodes": [
            {"id": "frame", "type": "source"},
            {"id": "fractal", "type": "procedural_field", "inputs": ["frame"], **authored},
        ],
        "output": "fractal",
    })
    saved = deepcopy(graph.nodes["fractal"].config)
    a = graph.render({"frame": reference()}, parameter_values={
        ("fractal", "field_fractal_phase"): -.5,
        ("fractal", "field_fractal_zoom"): .9,
    })
    b = graph.render({"frame": reference()}, parameter_values={
        ("fractal", "field_fractal_phase"): .5,
        ("fractal", "field_fractal_zoom"): 1.4,
    })
    assert a.tobytes() != b.tobytes()
    assert graph.nodes["fractal"].config == saved


def test_public_fractal_library_example_targets_real_controls():
    path = Path(__file__).parents[1] / "examples" / "effect-graph-fractal-library.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    graph = EffectGraph(document["effect_graph"])
    assert graph.nodes["fractal"].config["field_type"] in FRACTAL_FAMILIES
    assert len(graph.nodes["fractal"].config["field_palette"]) == 5
    for control in document["effect_graph_controls"]:
        graph.validate_control_target(control["node"], control["property"])
