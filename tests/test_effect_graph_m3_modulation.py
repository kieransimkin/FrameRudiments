"""M3 categorical/numeric DanceRudiment graph bindings (fake Motion driver)."""
from types import SimpleNamespace

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_modulation import EffectGraphControls


def graph():
    return EffectGraph({"nodes": [
        {"id": "foreground", "type": "source"},
        {"id": "mask", "type": "source"},
        {"id": "masked", "type": "matte", "inputs": ["foreground", "mask"]},
    ], "output": "masked"})


class Driver:
    def __init__(self, config, project, context_at):
        assert config["target"] == "style.value"
        self.combine = config.get("combine", "set")
        self.weight = config.get("weight", 1)
        self.lower, self.upper = config.get("range", [-1, 1])
        self.motion = SimpleNamespace(files=[])

    def output(self, ctx):
        return (self.lower + (self.upper - self.lower) * ctx.phase, 0)


def test_categorical_dancenotion_choice_uses_native_driver(monkeypatch):
    monkeypatch.setattr("framerudiments.effect_graph_modulation._Modulator", Driver)
    g = graph()
    controls = EffectGraphControls(None, g, [{
        "node": "masked", "property": "invert", "choices": [False, True], "motion": {},
    }, {
        "node": "masked", "property": "mask_opacity", "range": [0, 1], "motion": {},
    }], None)
    fg = Image.new("RGBA", (1, 1), "red")
    mask = Image.new("RGBA", (1, 1))
    sources = {"foreground": fg, "mask": mask}
    at_zero = controls.values(SimpleNamespace(phase=0), g)
    at_one = controls.values(SimpleNamespace(phase=1), g)
    assert at_zero == {("masked", "invert"): False, ("masked", "mask_opacity"): 0}
    assert at_one == {("masked", "invert"): True, ("masked", "mask_opacity"): 1}
    assert g.render(sources, parameter_values=at_zero).getpixel((0, 0))[3] == 255
    assert g.render(sources, parameter_values=at_one).getpixel((0, 0))[3] == 255
    assert g.nodes["masked"].config == {"id": "masked", "type": "matte", "inputs": ["foreground", "mask"]}


@pytest.mark.parametrize("row,match", [
    ({"property": "mask_combine", "choices": ["max", "max"]}, "choices"),
    ({"property": "mask_combine", "choices": ["max", "bogus"]}, "choices"),
    ({"property": "invert"}, "require choices"),
    ({"property": "invert", "choices": [True, False], "combine": "add"}, "require set"),
    ({"property": "invert", "choices": [True, False], "range": [0, 1]}, "range is derived"),
])
def test_categorical_invalid_bindings_fail_before_driver_init(row, match, monkeypatch):
    def fail_driver(*args):
        raise AssertionError("native Motion should not load on invalid control")
    monkeypatch.setattr("framerudiments.effect_graph_modulation._Modulator", fail_driver)
    with pytest.raises(ValueError, match=match):
        EffectGraphControls(None, graph(), [{"node": "masked", "motion": {}, **row}], None)
