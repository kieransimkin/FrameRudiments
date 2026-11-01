"""M6.5 discoverable controls share the real EffectGraph validation contract."""
from copy import deepcopy
import json

from PIL import Image, ImageDraw
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_layer_styles import STYLE_TYPES
from framerudiments.effect_graph_style_contract import (
    graph_style_manifest, style_capabilities, style_catalogue, style_control_schema,
)


def make(style='outline', **extra):
    config = {'id': 'styled', 'type': 'layer_style', 'inputs': ['s'], 'style_type': style, **extra}
    sources = [{'id': 's', 'type': 'source'}]
    if style == 'pattern_overlay' and extra.get('pattern_kind') == 'texture':
        sources.append({'id': 'texture', 'type': 'source'})
        config['inputs'].append('texture')
    return EffectGraph({'nodes': sources + [config], 'output': 'styled'})


def ink():
    image = Image.new('RGBA', (32, 32))
    ImageDraw.Draw(image).rectangle((8, 8, 23, 23), fill=(30, 120, 210, 140))
    return image


def test_complete_detached_catalogue_has_no_fake_gpu_capabilities():
    catalogue = style_catalogue()
    assert set(catalogue['styles']) == STYLE_TYPES
    assert len(catalogue['styles']) == 14
    for entry in catalogue['styles'].values():
        assert entry['backend']['cpu'] and not entry['backend']['gpu_native']
        assert entry['backend']['strict_gpu'] == 'reject'
        assert entry['random_access'] and not entry['temporal_history']
    assert catalogue['styles']['pattern_overlay']['variants']['texture']['input_count'] == 2
    json.dumps(catalogue, allow_nan=False)
    catalogue['styles']['bevel']['limits'].clear()
    assert style_catalogue()['styles']['bevel']['limits']


@pytest.mark.parametrize('style', sorted(STYLE_TYPES))
def test_every_discovered_property_is_a_valid_native_target_and_override(style):
    graph = make(style)
    image = ink()
    before = graph.render({'s': image}).tobytes()
    descriptors = graph.control_descriptors('styled')
    values = {}
    for name, item in descriptors.items():
        graph.validate_control_target('styled', name)
        assert graph.base_control_value('styled', name) == item['value'] == item['default']
        assert item['animatable']
        if item['type'] == 'enum' and len(item['choices']) > 1:
            graph.validate_control_choices('styled', name, item['choices'])
        values[('styled', name)] = item['value']
    assert graph.render({'s': image}, parameter_values=values).tobytes() == before
    json.dumps(graph.style_manifest(), allow_nan=False)


def test_two_input_texture_excludes_topology_changing_choices():
    graph = make('pattern_overlay', pattern_kind='texture')
    controls = graph.control_descriptors('styled')
    assert controls['pattern_kind']['choices'] == ['texture']
    assert controls['style_type']['choices'] == ['pattern_overlay']
    assert not controls['style_type']['can_vary']
    active = graph.style_manifest(active_only=True)['nodes']['styled']['controls']
    assert 'pattern_wrap' in active
    assert 'pattern_duty' not in active
    assert 'style_color.r' not in active
    unary = make('pattern_overlay').control_descriptors('styled')
    assert 'texture' not in unary['pattern_kind']['choices']
    assert 'texture' not in make('outline').control_descriptors('styled')['pattern_kind']['choices']


def test_gradient_custom_stop_paths_values_and_joint_constraints():
    graph = make('gradient_overlay', gradient_phase=.25, gradient_stops=[
        {'id': 'cool', 'position': .1, 'color': '#203060'},
        {'id': 'hot', 'position': .8, 'color': '#ff8800', 'opacity': .6},
    ])
    controls = graph.style_manifest(active_only=True)['nodes']['styled']['controls']
    assert controls['gradient_stops.hot.color.g']['value'] == 136
    assert controls['gradient_stops.hot.opacity']['value'] == .6
    assert controls['gradient_stops.hot.position']['factory_default'] is None
    assert controls['gradient_stops.hot.position']['constraints']
    assert controls['gradient_center.x']['units'] == 'normalized'
    assert controls['gradient_phase']['units'] == 'cycles'
    assert controls['gradient_phase']['factory_default'] == 0
    assert controls['gradient_phase']['value'] == .25
    controls['gradient_stops.hot.opacity']['value'] = 0
    assert graph.base_control_value('styled', 'gradient_stops.hot.opacity') == .6


def test_effect_only_controls_do_not_offer_fill_or_combined_blend_as_active():
    graph = make('color_overlay', style_output='effect')
    all_controls = graph.control_descriptors('styled')
    assert not all_controls['fill_opacity']['active']
    assert not all_controls['style_blend']['active']
    assert all_controls['opacity']['active']


def test_chain_preprocessing_discovery_preserves_internal_ids_and_public_output():
    graph = EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'mix', 'type': 'transition_chain', 'inputs': ['a', 'b'],
         'input_effects': [[{'id': 'edge', 'type': 'layer_style', 'style_type': 'outline'}], []],
         'stages': [{'id': 'reveal', 'progress': .5}]},
    ], 'output': 'mix'})
    key = graph.chain_expansions['mix']['inputs'][0][0]
    node = graph.style_manifest()['nodes'][key]
    assert node['chain'] == {'chain': 'mix', 'role': 'input_effect', 'input': 0}
    assert node['controls']['style_width']['address'] == [key, 'style_width']
    assert graph.style_manifest()['output'] == 'mix'


def test_unreachable_styles_optional_and_static_discovery_does_not_render():
    graph = EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
        {'id': 'unused', 'type': 'layer_style', 'inputs': ['s']}], 'output': 's'})
    assert graph.style_manifest()['nodes'] == {}
    row = graph.style_manifest(include_unreachable=True)['nodes']['unused']
    assert not row['reachable']
    assert graph.layer_style_ids == frozenset()
    with pytest.raises(ValueError):
        graph_style_manifest(graph, include_unreachable=1)


def test_manifest_is_independent_and_discovery_does_not_mutate_authored_config():
    graph = make('outline')
    config = deepcopy(graph.nodes['styled'].config)
    manifest = graph.style_manifest()
    manifest['nodes']['styled']['inputs'].clear()
    manifest['nodes']['styled']['controls']['style_width']['range'][1] = 0
    assert graph.nodes['styled'].config == config
    assert graph.control_descriptors('styled')['style_width']['range'][1] == 64


@pytest.mark.parametrize('bad', [True, '1', -1, 2, float('nan')])
def test_invalid_capability_opacity_is_rejected(bad):
    with pytest.raises(ValueError):
        style_capabilities({'opacity': bad})


def test_static_metadata_budget_matches_existing_style_families():
    assert style_capabilities({'style_type': 'bevel'})['limits']['distance_workspace_pixels'] == 1_048_576
    assert style_capabilities({'style_type': 'extrusion'})['limits']['sweep_samples'] == 257
    assert style_capabilities({'style_type': 'gradient_overlay'})['limits']['gradient_stops'] == 16
    with pytest.raises(ValueError):
        style_control_schema({}, input_count=1, active_only='yes')


@pytest.mark.parametrize('interval', [[-10000, 10000], [0, 1], [-4, 8], [-1, 1]])
def test_existing_native_ranges_are_not_remapped(interval):
    from framerudiments.effect_graph_style_contract import style_driver_interval, remap_style_driver
    assert style_driver_interval(interval) is None
    assert remap_style_driver(.375, None) == .375


@pytest.mark.parametrize('interval', [[12000, 24000], [-65536, 65536], [0, 36000], [-36000, -20000]])
@pytest.mark.parametrize('value', [0, .25, .5, 1])
def test_extended_native_style_ranges_map_after_the_native_transfer_curve(interval, value):
    from framerudiments.effect_graph_style_contract import style_driver_interval, remap_style_driver
    mapped = style_driver_interval(interval)
    assert mapped == tuple(interval)
    assert remap_style_driver(value, mapped) == interval[0] + value * (interval[1] - interval[0])


@pytest.mark.parametrize('bad', [None, [], [1, 1], [1, 0], ['0', 1], [True, 3],
                                [0, float('nan')], [-65537, 1], [0, 65537]])
def test_invalid_native_style_range_is_rejected_before_loading_motion(bad):
    from framerudiments.effect_graph_style_contract import style_driver_interval
    with pytest.raises(ValueError):
        style_driver_interval(bad)


@pytest.mark.parametrize('style,settings,ignored', [
    ('gradient_overlay', {}, 'style_color.r'),
    ('gradient_overlay', {'gradient_kind': 'radial'}, 'gradient_angle'),
    ('color_overlay', {}, 'overlay_space'),
    ('pattern_overlay', {'pattern_kind': 'texture'}, 'pattern_foreground_opacity'),
    ('pattern_overlay', {'pattern_kind': 'checker'}, 'pattern_duty'),
    ('pattern_overlay', {'pattern_kind': 'stripes'}, 'pattern_scale.y'),
])
def test_configuration_inactive_controls_remain_addressable_but_hidden_from_active_view(style, settings, ignored):
    graph = make(style, **settings)
    graph.validate_control_target('styled', ignored)
    assert not graph.control_descriptors('styled')[ignored]['active']
    assert ignored not in graph.style_manifest(active_only=True)['nodes']['styled']['controls']
