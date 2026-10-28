"""Integration of M4 spectral displacement with graph and DanceRudiment controls."""

from types import SimpleNamespace

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_modulation import EffectGraphControls


def make_graph(**spec):
    defaults = {
        "id": "colour", "type": "spectral_displacement", "inputs": ["source"],
        "spectral_mode": "replace", "bands": [{
            "id": "reds", "source_color": "#ff0000", "target_color": "#00ffff",
            "space": "rgb", "tolerance": .02, "softness": 0, "recolor": 1,
        }],
    }
    defaults.update(spec)
    return EffectGraph({"nodes": [
        {"id": "source", "type": "source"}, defaults,
    ], "output": "colour"})


def image():
    src = Image.new("RGBA", (4, 1))
    src.putpixel((0, 0), (255, 0, 0, 255))
    return src


def test_spectral_graph_forks_preserve_input_and_stable_controls():
    g = make_graph()
    src = image()
    controls = {("colour", "bands.reds.offset.x"): 2}
    moved = g.render({"source": src}, parameter_values=controls)
    assert moved.getpixel((2, 0)) == (0, 255, 255, 255)
    assert g.render({"source": src}).getpixel((0, 0)) == (0, 255, 255, 255)
    assert src.getpixel((0, 0)) == (255, 0, 0, 255)
    assert g.base_control_value("colour", "bands.reds.offset.x") == 0


def test_spectral_colour_controls_resolve_independently():
    g = make_graph()
    src = image()
    out = g.render({"source": src}, parameter_values={
        ("colour", "bands.reds.target_color.b"): 0,
        ("colour", "bands.reds.offset.x"): 1,
    })
    assert out.getpixel((1, 0)) == (0, 255, 0, 255)
    assert g.base_control_value("colour", "bands.reds.target_color.b") == 255


def test_spectral_enum_choices_and_modes():
    g = make_graph()
    for prop, choices in [
        ("spectral_mode", ["overlay", "replace"]),
        ("alpha_mode", ["source", "expanded"]),
        ("bands.reds.space", ["rgb", "oklab", "hsv"]),
    ]:
        assert g.is_categorical_control("colour", prop)
        g.validate_control_choices("colour", prop, choices)
    src = image()
    a = g.render({"source": src}, parameter_values={
        ("colour", "bands.reds.space"): "oklab", ("colour", "spectral_mode"): "overlay"})
    assert a.getpixel((0, 0))[3] == 255


def test_spectral_integration_rejects_invalid_configs_and_bindings():
    g = make_graph()
    for prop in ("bands.missing.offset.x", "bands.reds.target_color.q", "nope"):
        with pytest.raises(ValueError):
            g.validate_control_target("colour", prop)
    with pytest.raises(ValueError):
        g.validate_control_choices("colour", "bands.reds.space", ["cmyk", "rgb"])
    with pytest.raises(ValueError):
        g.render({"source": image()}, parameter_values={("colour", "bands.reds.offset.x"): 5000})
    with pytest.raises(ValueError):
        g.render({"source": image()}, parameter_values={("colour", "bands.reds.tolerance"): float("nan")})
    with pytest.raises(ValueError):
        make_graph(bands=[])


def test_native_motion_driver_bindings_are_frame_local(monkeypatch):
    class NativeDriver:
        def __init__(self, cfg, project, context_at):
            self.combine = cfg.get("combine", "set")
            self.weight = cfg.get("weight", 1)
            self.motion = SimpleNamespace(files=[])
        def output(self, ctx):
            return (ctx.frame / 2, 0)
    monkeypatch.setattr("framerudiments.effect_graph_modulation._Modulator", NativeDriver)
    g = make_graph()
    controls = EffectGraphControls(None, g, [{
        "node": "colour", "property": "bands.reds.offset.x", "motion": {},
    }], None)
    src = image()
    at0 = g.render({"source": src}, parameter_values=controls.values(SimpleNamespace(frame=0), g))
    at2 = g.render({"source": src}, parameter_values=controls.values(SimpleNamespace(frame=2), g))
    assert at0.getpixel((0, 0))[3] == 255
    assert at2.getpixel((1, 0)) == (0, 255, 255, 255)
    assert g.base_control_value("colour", "bands.reds.offset.x") == 0


def test_explicit_gpu_mode_rejects_missing_spectral_shader():
    class Backend:
        last_dispatch = None
    backend = Backend()
    g = make_graph()
    with pytest.raises(ValueError, match="spectral.*GPU"):
        g.render({"source": image()}, gpu_backend=backend, gpu_strict=True)
    result = g.render({"source": image()}, gpu_backend=backend, gpu_strict=False)
    assert result.getpixel((0, 0)) == (0, 255, 255, 255)
    assert backend.last_dispatch["cpu_fallbacks"] == 1


def test_spectral_output_feeds_two_input_transition_with_alpha_correctness():
    source = image()
    g = EffectGraph({"nodes": [
        {"id": "source", "type": "source"},
        {"id": "colour", "type": "spectral_displacement", "inputs": ["source"],
         "spectral_mode": "replace", "bands": [{
             "id": "reds", "source_color": "#ff0000", "target_color": "#00ffff",
             "recolor": 1, "space": "rgb", "tolerance": .01,
             "softness": 0, "offset": [1, 0],
         }]},
        {"id": "transition", "type": "transition", "inputs": ["source", "colour"],
         "progress": .5},
    ], "output": "transition"})
    out = g.render({"source": source})
    assert out.getpixel((0, 0)) == (255, 0, 0, 128)
    assert out.getpixel((1, 0)) == (0, 255, 255, 128)
    assert source.getpixel((0, 0)) == (255, 0, 0, 255)


def test_two_independent_spectral_forks_can_be_composited():
    source = image()
    source.putpixel((2, 0), (0, 0, 255, 255))
    g = EffectGraph({"nodes": [
        {"id": "source", "type": "source"},
        {"id": "warm", "type": "spectral_displacement", "inputs": ["source"],
         "spectral_mode": "replace", "bands": [{
             "id": "red", "source_color": "#ff0000", "target_color": "#00ffff",
             "recolor": 1, "space": "rgb", "softness": 0, "tolerance": .01,
             "offset": [1, 0],
         }]},
        {"id": "cool", "type": "spectral_displacement", "inputs": ["source"],
         "spectral_mode": "replace", "bands": [{
             "id": "blue", "source_color": "#0000ff", "target_color": "#ff00ff",
             "recolor": 1, "space": "rgb", "softness": 0, "tolerance": .01,
             "offset": [1, 0],
         }]},
        {"id": "out", "type": "composite", "inputs": ["warm", "cool"]},
    ], "output": "out"})
    result = g.render({"source": source})
    assert result.getpixel((1, 0)) == (0, 255, 255, 255)
    assert result.getpixel((3, 0)) == (255, 0, 255, 255)
    assert source.getpixel((0, 0)) == (255, 0, 0, 255)
    assert source.getpixel((2, 0)) == (0, 0, 255, 255)
