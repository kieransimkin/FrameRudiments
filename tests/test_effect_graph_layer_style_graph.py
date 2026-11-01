"""Layer styles in real DAG dispatch, controls, forks, mattes and chains."""
from copy import deepcopy
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_layer_styles import STYLE_CONTROLS, STYLE_ENUMS, STYLE_RANGES


def source():
    image = Image.new('RGBA', (24, 20))
    ImageDraw.Draw(image).rectangle((8, 6, 15, 13), fill=(210, 80, 30, 180))
    return image


def graph(**settings):
    return EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'},
        {'id': 'fx', 'type': 'layer_style', 'inputs': ['s'], **settings},
    ], 'output': 'fx'})


def test_all_descriptors_resolve_to_valid_native_binding_targets():
    g = graph()
    descriptors = g.control_descriptors('fx')
    assert set(descriptors) == set(STYLE_CONTROLS) | {'opacity'}
    for name, entry in descriptors.items():
        g.validate_control_target('fx', name)
        assert g.base_control_value('fx', name) == entry['default']
        if entry['type'] == 'enum':
            assert g.is_categorical_control('fx', name)
            g.validate_control_choices('fx', name, entry['choices'])
        else:
            assert not g.is_categorical_control('fx', name)


@pytest.mark.parametrize('prop,bounds', STYLE_RANGES.items())
def test_numeric_controls_are_frame_local_and_range_checked(prop, bounds):
    g = graph(style_type='outline', style_offset=[1, 2], style_color='#123456')
    config = deepcopy(g.nodes['fx'].config)
    image = source()
    before = image.tobytes()
    baseline = g.render({'s': image}).tobytes()
    value = (bounds[0] + bounds[1]) / 2
    g.render({'s': image}, parameter_values={('fx', prop): value})
    assert g.nodes['fx'].config == config
    assert g.render({'s': image}).tobytes() == baseline
    assert image.tobytes() == before
    for invalid in (bounds[0]-1, bounds[1]+1, float('nan'), float('inf'), True, 'bad'):
        with pytest.raises(ValueError):
            g.render({'s': image}, parameter_values={('fx', prop): invalid})


@pytest.mark.parametrize('prop,choices', STYLE_ENUMS.items())
def test_categorical_controls_are_all_valid_and_do_not_mutate(prop, choices):
    g = graph(style_type='outline')
    before = deepcopy(g.nodes['fx'].config)
    for value in choices:
        result = g.render({'s': source()}, parameter_values={('fx', prop): value})
        assert result.size == (24, 20)
    assert g.nodes['fx'].config == before
    with pytest.raises(ValueError):
        g.render({'s': source()}, parameter_values={('fx', prop): 'invalid'})


def test_universal_opacity_is_applied_exactly_once_after_style():
    g = graph(style_output='effect', style_offset=[0, 0])
    a = g.render({'s': source()})
    b = g.render({'s': source()}, parameter_values={('fx', 'opacity'): .5})
    assert a.getpixel((8, 8))[3] == 180
    assert b.getpixel((8, 8))[3] == 90


def test_forks_share_input_without_changing_original_alpha_metadata():
    config = {'nodes': [
        {'id': 's', 'type': 'source'},
        {'id': 'dim', 'type': 'opacity', 'inputs': ['s'], 'opacity': .25},
        {'id': 'fx', 'type': 'layer_style', 'inputs': ['dim'], 'alpha_source': 'original',
         'style_offset': [3, 0], 'style_output': 'effect'},
        {'id': 'out', 'type': 'composite', 'inputs': ['fx', 's']},
    ], 'output': 'fx'}
    g = EffectGraph(config)
    image = source()
    before = image.tobytes()
    assert g.render({'s': image}).getpixel((18, 8))[3] == 180
    assert g.render({'s': image}, parameter_values={('fx', 'alpha_source'): 'current'}).getpixel((18, 8))[3] == 45
    assert image.tobytes() == before
    assert g.nodes['dim'].config['opacity'] == .25


def test_styles_can_be_matted_and_keyed_before_or_after_processing():
    nodes = [
        {'id': 's', 'type': 'source'}, {'id': 'm', 'type': 'source'},
        {'id': 'pre', 'type': 'matte', 'inputs': ['s', 'm']},
        {'id': 'prestyle', 'type': 'layer_style', 'inputs': ['pre'], 'style_type': 'outer_glow',
         'style_spread': 2, 'style_color': '#00ff00'},
        {'id': 'fx', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'outer_glow',
         'style_spread': 2, 'style_color': '#00ff00'},
        {'id': 'post', 'type': 'matte', 'inputs': ['fx', 'm']},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['fx'], 'key_color': '#00ff00',
         'tolerance': .01, 'softness': 0},
    ]
    mask = Image.new('RGBA', (24, 20))
    ImageDraw.Draw(mask).rectangle((0, 0, 11, 19), fill='white')
    providers = {'s': source(), 'm': mask}
    pre = EffectGraph({'nodes': nodes, 'output': 'prestyle'}).render(providers)
    post = EffectGraph({'nodes': nodes, 'output': 'post'}).render(providers)
    assert pre.getpixel((12, 8))[3] > 0
    assert post.getpixel((12, 8))[3] == 0
    keyed = EffectGraph({'nodes': nodes, 'output': 'key'}).render(providers)
    assert keyed.getpixel((7, 8))[3] == 0
    assert keyed.getpixel((10, 8))[3] == 180


def test_style_can_preprocess_a_chain_source_with_stable_controls():
    g = EffectGraph({'nodes': [
        {'id': 's', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'chain', 'type': 'transition_chain', 'inputs': ['s', 'b'],
         'input_effects': [[{'id': 'neon', 'type': 'layer_style', 'style_type': 'outline',
                            'style_width': 2, 'style_color': '#00ffff'}], []],
         'stages': [{'id': 'blend', 'progress': 0}]},
    ], 'output': 'chain'})
    style_id = g.chain_expansions['chain']['inputs'][0][0]
    assert style_id == 'chain::input::0::neon'
    first = g.render({'s': source(), 'b': Image.new('RGBA', (24, 20), 'blue')})
    no_style = g.render({'s': source(), 'b': Image.new('RGBA', (24, 20), 'blue')},
                        parameter_values={(style_id, 'style_width'): 0})
    assert first.getpixel((6, 8))[3] > 0
    assert no_style.getpixel((6, 8))[3] == 0


def test_combined_node_is_a_source_for_following_styles_and_mattes():
    nodes = [{'id': 's', 'type': 'source'},
             {'id': 'glow', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'outer_glow',
              'style_spread': 2},
             {'id': 'copy', 'type': 'copy', 'inputs': ['s']},
             {'id': 'mix', 'type': 'transition', 'inputs': ['glow', 'copy'], 'progress': 0},
             {'id': 'shadow', 'type': 'layer_style', 'inputs': ['mix'], 'style_output': 'effect',
              'alpha_source': 'original', 'style_offset': [0, 0]}]
    g = EffectGraph({'nodes': nodes, 'output': 'shadow'})
    out = g.render({'s': source()})
    assert out.getpixel((6, 8))[3] > 0  # transition resets lineage to its output


def test_auto_mode_accounts_for_cpu_fallback_and_strict_gpu_refuses():
    g = graph(style_type='outline')
    backend = SimpleNamespace(last_dispatch={})
    actual = g.render({'s': source()}, gpu_backend=backend)
    assert actual.tobytes() == g.render({'s': source()}).tobytes()
    assert backend.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}
    with pytest.raises(GPUUnsupported, match='layer_style'):
        g.render({'s': source()}, gpu_backend=backend, gpu_strict=True)
    with pytest.raises(GPUUnsupported, match='layer_style'):
        g.render({'s': source()}, gpu_strict=True)


@pytest.mark.parametrize('node', [
    {'id': 'fx', 'type': 'layer_style', 'inputs': []},
    {'id': 'fx', 'type': 'layer_style', 'inputs': ['s', 's']},
    {'id': 'fx', 'type': 'layer_style', 'inputs': ['s'], 'style_color': 'bogus'},
    {'id': 'fx', 'type': 'layer_style', 'inputs': ['s'], 'key_color': '#00ff00'},
    {'id': 'fx', 'type': 'copy', 'inputs': ['s'], 'style_type': 'outline'},
])
def test_invalid_style_nodes_fail_before_render(node):
    with pytest.raises(ValueError):
        EffectGraph({'nodes': [{'id': 's', 'type': 'source'}, node], 'output': 'fx'})


def test_legacy_identity_path_stays_byte_exact():
    image = source()
    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'}], 'output': 's'})
    assert g.render({'s': image}).tobytes() == image.tobytes()
