"""M6.4 graph ports, control discovery, alpha lineage and branch isolation."""
from copy import deepcopy
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_layer_overlays import OVERLAY_CONTROLS, OVERLAY_ENUMS, OVERLAY_RANGES
from framerudiments.effect_graph_layer_styles import render_layer_style


def graph(**config):
    return EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'},
        {'id': 'p', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'gradient_overlay', **config},
    ], 'output': 'p'})


@pytest.mark.parametrize('name,bounds', sorted(OVERLAY_RANGES.items()))
def test_numeric_parameters_share_native_control_addressing(name, bounds):
    g = graph()
    before = deepcopy(g.nodes['p'].config)
    image = Image.new('RGBA', (8, 9), (50, 100, 150, 128))
    value = sum(bounds)/2
    assert name in g.control_descriptors('p')
    g.render({'s': image}, parameter_values={('p', name): value})
    assert g.nodes['p'].config == before
    with pytest.raises(ValueError):
        g.render({'s': image}, parameter_values={('p', name): bounds[1]+1})
    with pytest.raises(ValueError):
        g.render({'s': image}, parameter_values={('p', name): True})


@pytest.mark.parametrize('name,values', sorted(OVERLAY_ENUMS.items()))
def test_enum_parameters_are_discoverable_frame_local(name, values):
    g = graph()
    config = deepcopy(g.nodes['p'].config)
    g.validate_control_choices('p', name, sorted(values))
    assert g.is_categorical_control('p', name)
    for value in values:
        # A dormant texture selector on a gradient does not change its ports.
        g.render({'s': Image.new('RGBA', (7, 8), 'white')}, parameter_values={('p', name): value})
    assert g.nodes['p'].config == config


def test_stable_stop_ids_discover_and_control_independently():
    cfg = {'gradient_stops': [{'id': 'cold', 'position': .1, 'color': 'blue'},
                             {'id': 'hot', 'position': .9, 'color': 'red'}]}
    g = graph(**cfg)
    before = deepcopy(cfg)
    assert g.base_control_value('p', 'gradient_stops.hot.color.r') == 255
    assert g.base_control_value('p', 'gradient_stops.cold.opacity') == 1
    meta = g.control_descriptors('p')
    assert 'gradient_stops.cold.color.b' in meta
    assert meta['gradient_stops.hot.position']['range'] == [0, 1]
    params = {('p', 'gradient_stops.hot.color.r'): 0, ('p', 'gradient_stops.hot.color.g'): 255,
              ('p', 'gradient_stops.cold.opacity'): .5}
    image = Image.new('RGBA', (12, 2), 'white')
    out = g.render({'s': image}, parameter_values=params)
    assert out.getpixel((11, 0)) == (0, 255, 0, 255)
    assert cfg == before
    meta['gradient_stops.hot.color.r']['default'] = 99
    assert g.control_descriptors('p')['gradient_stops.hot.color.r']['default'] == 255


@pytest.mark.parametrize('target', ['gradient_stops.missing.color.r', 'gradient_stops.0.color.r',
                                   'gradient_stops.start.color.a', 'gradient_stops.start.position.x'])
def test_invalid_stop_paths_rejected(target):
    with pytest.raises(ValueError, match='control target'):
        graph().validate_control_target('p', target)


def test_simultaneous_stop_updates_are_validated_as_complete_table():
    g = graph(gradient_stops=[{'id': 'a', 'position': .2, 'color': 'red'},
                             {'id': 'b', 'position': .3, 'color': 'blue'}])
    image = Image.new('RGBA', (9, 1), 'white')
    pairs = [(('p', 'gradient_stops.a.position'), .7), (('p', 'gradient_stops.b.position'), .8)]
    a = g.render({'s': image}, parameter_values=dict(pairs))
    b = g.render({'s': image}, parameter_values=dict(reversed(pairs)))
    assert a.tobytes() == b.tobytes()
    with pytest.raises(ValueError, match='increasing'):
        g.render({'s': image}, parameter_values={('p', 'gradient_stops.a.position'): .4})
    assert g.nodes['p'].config['gradient_stops'][0]['position'] == .2


def test_texture_port_sizes_are_independent_and_neither_input_is_modified():
    g = EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'}, {'id': 'tile', 'type': 'source'},
        {'id': 'p', 'type': 'layer_style', 'style_type': 'pattern_overlay', 'pattern_kind': 'texture',
         'inputs': ['s', 'tile'], 'pattern_scale': [2, 1]},
    ], 'output': 'p'})
    source = Image.new('RGBA', (8, 6), 'white')
    tile = Image.new('RGBA', (2, 1), 'blue')
    tile.putpixel((0, 0), (255, 0, 0, 255))
    before = source.tobytes(), tile.tobytes()
    out = g.render({'s': source, 'tile': tile})
    assert out.size == source.size
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)
    assert out.getpixel((1, 0)) == (0, 0, 255, 255)
    assert before == (source.tobytes(), tile.tobytes())
    with pytest.raises(ValueError, match='requires 1'):
        g.render({'s': source, 'tile': tile}, parameter_values={('p', 'pattern_kind'): 'checker'})
    with pytest.raises(ValueError, match='requires 1'):
        g.validate_control_choices('p', 'style_type', ['color_overlay', 'pattern_overlay'])


def test_texture_arity_rejected_at_preparation_and_after_controls():
    with pytest.raises(ValueError, match='requires 2'):
        graph(style_type='pattern_overlay', pattern_kind='texture')
    g = graph(style_type='pattern_overlay')
    with pytest.raises(ValueError, match='requires 2'):
        g.validate_control_choices('p', 'pattern_kind', ['checker', 'texture'])
    with pytest.raises(ValueError, match='requires 2'):
        g.render({'s': Image.new('RGBA', (2, 2))}, parameter_values={('p', 'pattern_kind'): 'texture'})


def test_forked_blended_overlays_matte_and_transition_preserve_inputs():
    nodes = [{'id': 's', 'type': 'source'}, {'id': 'mask', 'type': 'source'},
             {'id': 'a', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'color_overlay', 'style_color': 'red'},
             {'id': 'b', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'pattern_overlay', 'style_color': 'blue'},
             {'id': 'blend', 'type': 'composite', 'inputs': ['a', 'b'], 'mode': 'screen'},
             {'id': 'cut', 'type': 'matte', 'inputs': ['blend', 'mask']},
             {'id': 't', 'type': 'transition', 'inputs': ['s', 'cut'], 'progress': .5}]
    g = EffectGraph({'nodes': nodes, 'output': 't'})
    sources = {'s': Image.new('RGBA', (9, 7), (30, 70, 100, 128)),
               'mask': Image.new('RGBA', (9, 7), (255, 255, 255, 64))}
    originals = {k: v.tobytes() for k, v in sources.items()}
    first = g.render(sources)
    g.render(sources, parameter_values={('a', 'style_color.g'): 255, ('t', 'progress'): .2})
    assert g.render(sources).tobytes() == first.tobytes()
    assert {k: v.tobytes() for k, v in sources.items()} == originals


def test_chain_unary_preprocessing_uses_the_same_overlay_renderer():
    g = EffectGraph({'nodes': [{'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
                             {'id': 'chain', 'type': 'transition_chain', 'inputs': ['a', 'b'],
                              'input_effects': [[{'id': 'ink', 'type': 'layer_style', 'style_type': 'color_overlay',
                                                 'style_color': '#ffaa00'}], []],
                              'stages': [{'id': 'fade', 'progress': 0}]}], 'output': 'chain'})
    src = Image.new('RGBA', (3, 2), 'blue')
    assert g.render({'a': src, 'b': src}).tobytes() == render_layer_style(src, {
        'style_type': 'color_overlay', 'style_color': '#ffaa00'}).tobytes()
    g.validate_control_target('chain::input::0::ink', 'style_color.r')


def test_cpu_fallback_is_explicit_and_strict_gpu_rejects():
    g = graph()
    gpu = SimpleNamespace(last_dispatch={})
    sources = {'s': Image.new('RGBA', (3, 2), 'white')}
    assert g.render(sources, gpu_backend=gpu).tobytes() == g.render(sources).tobytes()
    assert gpu.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}
    with pytest.raises(GPUUnsupported):
        g.render(sources, gpu_backend=gpu, gpu_strict=True)


def test_descriptor_contract_exposes_every_overlay_control():
    g = graph()
    descriptors = g.control_descriptors('p')
    assert OVERLAY_CONTROLS <= descriptors.keys()
    for path in descriptors:
        g.validate_control_target('p', path)
        g.base_control_value('p', path)


def test_documented_graph_fragment_has_valid_controls():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1]/'examples/effect-graph-m6-4-overlays.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    g = EffectGraph(data['effect_graph'])
    for binding in data['effect_graph_controls']:
        g.validate_control_target(binding['node'], binding['property'])
    output = g.render({'layer:native': Image.new('RGBA', (32, 24), (60, 90, 140, 128))})
    assert output.size == (32, 24)
