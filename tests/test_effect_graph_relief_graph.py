"""M6.3 registration, branching, alpha lineage and resolved-control integration."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_layer_relief import RELIEF_TYPES, RELIEF_ENUMS, RELIEF_RANGES


def image():
    result = Image.new('RGBA', (44, 32))
    ImageDraw.Draw(result).rectangle((10, 7, 30, 24), fill=(60, 120, 170, 180))
    return result


def graph(kind='bevel', **extra):
    return EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'},
        {'id': 'relief', 'type': 'layer_style', 'inputs': ['s'], 'style_type': kind, **extra},
    ], 'output': 'relief'})


@pytest.mark.parametrize('kind', sorted(RELIEF_TYPES))
def test_graph_binds_new_style_and_independent_opacity(kind):
    g = graph(kind, style_color='#ff8040')
    providers = {'s': image()}
    saved = providers['s'].tobytes()
    base = g.render(providers)
    dim = g.render(providers, parameter_values={('relief', 'opacity'): .25})
    assert np.abs(np.asarray(dim)[..., 3].astype(int) - np.rint(np.asarray(base)[..., 3] * .25)).max() <= 1
    assert g.render(providers).tobytes() == base.tobytes()
    assert providers['s'].tobytes() == saved


@pytest.mark.parametrize('name', sorted(RELIEF_RANGES))
def test_every_numeric_control_can_be_resolved_through_real_graph(name):
    g = graph()
    g.validate_control_target('relief', name)
    assert not g.is_categorical_control('relief', name)
    low, high = RELIEF_RANGES[name]
    config = deepcopy(g.nodes['relief'].config)
    g.render({'s': image()}, parameter_values={('relief', name): (low + high) / 2})
    assert g.nodes['relief'].config == config
    assert g.control_descriptors('relief')[name]['animatable'] is True
    for bad in (float('nan'), True, high + 1):
        with pytest.raises(ValueError):
            g.render({'s': image()}, parameter_values={('relief', name): bad})


@pytest.mark.parametrize('name', sorted(RELIEF_ENUMS))
def test_discrete_native_control_contract(name):
    g = graph()
    choices = sorted(RELIEF_ENUMS[name])
    assert g.is_categorical_control('relief', name)
    g.validate_control_choices('relief', name, choices)
    for value in choices:
        g.render({'s': image()}, parameter_values={('relief', name): value})
    with pytest.raises(ValueError):
        g.validate_control_choices('relief', name, [choices[0], 'invalid'])


def test_bevel_control_changes_pixels_and_can_seek_back():
    g = graph(light_elevation=0)
    values = {('relief', 'light_angle'): 0}
    right = g.render({'s': image()}, parameter_values=values)
    left = g.render({'s': image()}, parameter_values={('relief', 'light_angle'): 180})
    assert right.tobytes() != left.tobytes()
    assert g.render({'s': image()}, parameter_values=values).tobytes() == right.tobytes()


def test_styles_in_transition_chain_input_effects_keep_stable_ids():
    g = EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'chain', 'type': 'transition_chain', 'inputs': ['a', 'b'],
         'input_effects': [[{'id': 'relief', 'type': 'layer_style', 'style_type': 'bevel'}],
                           [{'id': 'depth', 'type': 'layer_style', 'style_type': 'extrusion',
                             'style_color': '#00ffff'}]],
         'stages': [{'id': 'mix', 'transition_style': 'dissolve', 'progress': .5}]},
    ], 'output': 'chain'})
    g.validate_control_target('chain::input::0::relief', 'bevel_depth')
    g.validate_control_target('chain::input::1::depth', 'extrude_length')
    out = g.render({'a': image(), 'b': image()}, parameter_values={
        ('chain::input::0::relief', 'light_angle'): 170,
        ('chain::input::1::depth', 'extrude_angle'): 225,
    })
    assert out.size == image().size and out.getchannel('A').getbbox()


def test_shared_source_forks_are_not_mutated_by_relief_and_matte():
    g = EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'}, {'id': 'm', 'type': 'source'},
        {'id': 'relief', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'emboss'},
        {'id': 'body', 'type': 'matte', 'inputs': ['relief', 'm']},
        {'id': 'depth', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'extrusion',
         'style_output': 'effect', 'style_color': '#003366'},
        {'id': 'out', 'type': 'composite', 'inputs': ['depth', 'body']},
    ], 'output': 'out'})
    src = image()
    saved = src.tobytes()
    out = g.render({'s': src, 'm': Image.new('RGBA', src.size, (255, 255, 255, 180))})
    assert src.tobytes() == saved and out.getchannel('A').getbbox()


@pytest.mark.parametrize('kind', sorted(RELIEF_TYPES))
def test_explicit_gpu_policy_is_not_silently_changed(kind):
    g = graph(kind)
    gpu = SimpleNamespace()
    providers = {'s': image()}
    auto = g.render(providers, gpu_backend=gpu)
    assert auto.tobytes() == g.render(providers).tobytes()
    assert gpu.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}
    with pytest.raises(GPUUnsupported):
        g.render(providers, gpu_backend=gpu, gpu_strict=True)


def test_original_lineage_is_kept_through_new_style():
    g = EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'}, {'id': 'mask', 'type': 'source'},
        {'id': 'dim', 'type': 'copy', 'inputs': ['s'], 'opacity': .2},
        {'id': 'bevel', 'type': 'layer_style', 'inputs': ['dim'], 'style_type': 'bevel'},
        {'id': 'restored', 'type': 'matte', 'inputs': ['bevel', 'mask'], 'alpha_source': 'original'},
    ], 'output': 'restored'})
    src = image()
    out = g.render({'s': src, 'mask': Image.new('RGBA', src.size, 'white')})
    assert out.getchannel('A').tobytes() == src.getchannel('A').tobytes()


def test_documented_example_registers_all_sources_and_controls():
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / 'examples' / 'effect-graph-m6-3-relief.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    g = EffectGraph(data['effect_graph'])
    for control in data['effect_graph_controls']:
        g.validate_control_target(control['node'], control['property'])
    assert g.source_ids == frozenset({'layer:native'})
    assert g.render({'layer:native': image()}).getchannel('A').getbbox()
