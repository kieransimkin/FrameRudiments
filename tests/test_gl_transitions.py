import json
from types import SimpleNamespace

import pytest

from framerudiments import gl_transitions
from framerudiments.cli import main
from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported


def transition(**overrides):
    return {'id': 'mix', 'type': 'gl_transition', 'inputs': ['a', 'b'],
            'shader': gl_transitions.shader_catalogue()[0], **overrides}


def graph(node, *, output='mix'):
    return EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'}, node,
    ], 'output': output})


def test_cli_lists_pins_and_every_packaged_source_matches(capsys):
    assert main(['gl-transitions']) == 0
    shaders = json.loads(capsys.readouterr().out)
    assert shaders == gl_transitions.shader_catalogue()
    assert len(shaders) == 2
    for shader in shaders:
        assert 'vec4 transition(vec2 uv)' in gl_transitions._fragment(shader['id'])


def test_shader_source_integrity(monkeypatch, tmp_path):
    assets = tmp_path / 'gl_transition_shaders'
    assets.mkdir()
    (assets / 'circular-reveal.glsl').write_text('changed source', encoding='ascii')
    monkeypatch.setattr(gl_transitions, 'files', lambda _: tmp_path)
    gl_transitions._fragment.cache_clear()
    try:
        with pytest.raises(ValueError, match='SHA-256 mismatch'):
            gl_transitions._fragment('circular-reveal')
    finally:
        gl_transitions._fragment.cache_clear()


@pytest.mark.parametrize('overrides, message', [
    ({'inputs': ['a']}, 'two inputs'),
    ({'inputs': ['a', 'b', 'b']}, 'two inputs'),
    ({'shader': None}, 'SHA-256'),
    ({'shader': {'id': '../source', 'sha256': '0' * 64}}, 'SHA-256'),
    ({'shader': {'id': 'circular-reveal', 'sha256': '0' * 64}}, 'SHA-256'),
    ({'shader': {**gl_transitions.shader_catalogue()[0], 'source': 'some GLSL'}}, 'SHA-256'),
    ({'progress': True}, 'progress'),
    ({'progress': float('nan')}, 'progress'),
    ({'progress': 1.001}, 'progress'),
    ({'timing': {'duration_beats': 0}}, 'duration_beats'),
    ({'transition_style': 'wipe'}, 'property'),
])
def test_gl_transition_schema(overrides, message):
    with pytest.raises(ValueError, match=message):
        graph(transition(**overrides))


def test_graph_requires_gpu_only_for_reachable_shader_nodes():
    node = transition()
    reachable = graph(node)
    with pytest.raises(GPUUnsupported, match='active OpenGL'):
        reachable.render({})
    for mode in ('cpu', 'auto'):
        plan = reachable.backend_plan({'a': (8, 4), 'b': (8, 4)}, mode=mode)
        assert not plan['eligible']
        assert plan['nodes']['mix']['decision_reason'] == 'gpu_required'
    assert graph(node, output='a').backend_plan({'a': (8, 4)})['eligible']


def test_native_timing_controls_and_progress_override():
    effect = graph(transition(timing={'clock': 'source', 'duration_beats': 4}))
    context = SimpleNamespace(beat=3, source_beat=1, output_seconds=.25)
    assert effect.base_control_value('mix', 'timing.duration_beats') == 4
    assert effect.is_categorical_control('mix', 'timing.clock')
    effect.validate_control_choices('mix', 'timing.clock', ['source', 'continuous'])
    timed = effect._resolved_configs({('mix', 'timing.duration_beats'): 2}, context)
    assert timed['mix']['progress'] == .5
    direct = effect._resolved_configs({('mix', 'progress'): .8}, context)
    assert direct['mix']['progress'] == .8
    assert 'progress' not in effect.nodes['mix'].config
