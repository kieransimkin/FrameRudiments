from types import SimpleNamespace
from PIL import Image
import pytest
from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_modulation import EffectGraphControls


def graph():
    return EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'},
        {'id': 'a', 'type': 'opacity', 'inputs': ['s'], 'opacity': 0.8},
    ], 'output': 'a'})


def test_invalid_binding_target_fails_before_motion_loading():
    with pytest.raises(ValueError, match='control target'):
        EffectGraphControls(None, graph(), [{'node': 'a', 'property': 'not_a_parameter'}], None)


def test_controls_reuse_native_driver_and_do_not_mutate_graph(monkeypatch):
    class Driver:
        def __init__(self, config, project, context_at):
            assert config['target'] == 'style.value'
            self.combine = 'set'
            self.weight = 1
            self.motion = SimpleNamespace(files=[])
        def output(self, ctx):
            return (ctx.frame / 10, 0)
    monkeypatch.setattr('framerudiments.effect_graph_modulation._Modulator', Driver)
    g = graph()
    c = EffectGraphControls(None, g, [{'node': 'a', 'property': 'opacity', 'motion': {}}], None)
    src = Image.new('RGBA', (1, 1), (255, 0, 0, 255))
    assert g.render({'s': src}, parameter_values=c.values(SimpleNamespace(frame=5), g)).getpixel((0, 0))[3] == 128
    assert g.render({'s': src}, parameter_values=c.values(SimpleNamespace(frame=10), g)).getpixel((0, 0))[3] == 255
    assert g.nodes['a'].config['opacity'] == .8


def test_two_native_motion_controls_drive_independent_branches(monkeypatch):
    class NativeDriver:
        def __init__(self, config, project, context_at):
            self.combine = config.get('combine', 'set')
            self.weight = config.get('weight', 1)
            self.axis = config.get('axis')
            self.motion = SimpleNamespace(files=[])
        def output(self, ctx):
            return ((ctx.frame / 10) if self.axis == 'x' else (1 - ctx.frame / 10), 0)
    monkeypatch.setattr('framerudiments.effect_graph_modulation._Modulator', NativeDriver)
    g = EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'},
        {'id': 'a', 'type': 'opacity', 'inputs': ['s'], 'opacity': 1},
        {'id': 'b', 'type': 'opacity', 'inputs': ['s'], 'opacity': 1},
        {'id': 'out', 'type': 'composite', 'inputs': ['a', 'b']},
    ], 'output': 'out'})
    controls = EffectGraphControls(None, g, [
        {'node': 'a', 'property': 'opacity', 'motion': {}, 'axis': 'x'},
        {'node': 'b', 'property': 'opacity', 'motion': {}, 'axis': 'y'},
    ], None)
    src = Image.new('RGBA', (2, 2), (40, 90, 180, 255))
    at_start = g.render({'s': src}, parameter_values=controls.values(SimpleNamespace(frame=0), g))
    at_end = g.render({'s': src}, parameter_values=controls.values(SimpleNamespace(frame=10), g))
    assert at_start.tobytes() == at_end.tobytes()
    assert src.getpixel((0, 0))[3] == 255
    assert g.nodes['a'].config['opacity'] == 1


def test_reject_duplicate_replacement_target_before_loading_motion():
    bindings = [{'node': 'a', 'property': 'opacity', 'motion': {}}] * 2
    with pytest.raises(ValueError, match='duplicate'):
        EffectGraphControls(None, graph(), bindings, None)
