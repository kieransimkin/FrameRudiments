"""M10.3 discovery and static editing reuse native graph parameter semantics."""
from copy import deepcopy
from hashlib import sha256
import json

from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph, KINDS


def make(kind, **settings):
    count = (0 if kind == 'source' else 3 if kind == 'transition' and settings.get('transition_style') == 'matte'
             else 2 if kind in {'matte', 'composite', 'transition'} or
             kind == 'spatial_warp' and settings.get('warp_type') == 'displacement' or
             kind == 'layer_style' and settings.get('pattern_kind') == 'texture' else 1)
    sources = [{'id': f's{index}', 'type': 'source'} for index in range(count)]
    if kind == 'spectral_displacement' or kind == 'transition' and settings.get('transition_style') == 'spectral':
        settings.setdefault('bands', [{'id': 'warm', 'source_color': '#ff0000'}])
    return EffectGraph({'nodes': sources + [
        {'id': 'effect', 'type': kind, 'inputs': [entry['id'] for entry in sources], **settings},
    ], 'output': 'effect'})


@pytest.mark.parametrize('kind', sorted(KINDS))
def test_all_fourteen_kinds_have_detached_native_descriptors_and_editable_values(kind):
    graph = make(kind)
    original = deepcopy(graph.nodes['effect'].config)
    rows = graph.control_descriptors('effect')
    assert rows and rows['opacity']['range'] == [0, 1]
    json.dumps(rows, allow_nan=False)
    for name, row in rows.items():
        assert row['value'] == graph.base_control_value('effect', name)
        assert row['type'] in {'number', 'integer', 'enum', 'boolean'}
        assert type(row['active']) is bool and type(row['bindable']) is bool
        assert row['group'] and row['editable']
        proposed = graph.apply_control_values({('effect', name): row['value']})
        assert proposed.base_control_value('effect', name) == row['value']
        assert graph.nodes['effect'].config == original
    rows['opacity']['range'][1] = -1
    assert graph.control_descriptors('effect')['opacity']['range'] == [0, 1]


@pytest.mark.parametrize(('kind', 'settings'), [
    ('transition', {}), ('transition', {'transition_style': 'wipe'}),
    ('transition', {'transition_style': 'matte'}),
    ('transition', {'transition_style': 'spectral'}),
    ('transition', {'transition_style': 'zoom', 'strength': .4}),
    ('transition', {'transition_style': 'wash', 'wash_variant': 'droplets'}),
    ('transition', {'timing': {'repeat': 'loop', 'period_beats': 8}}),
    ('spatial_warp', {'warp_type': 'displacement'}),
    ('layer_style', {'style_type': 'pattern_overlay', 'pattern_kind': 'texture'}),
    ('layer_style', {'style_type': 'gradient_overlay'}),
    ('procedural_field', {'field_type': 'julia'}),
    ('raster_effect', {'raster_type': 'crt'}),
    ('filter', {'filter_type': 'bloom'}),
])
def test_every_advertised_categorical_choice_preserves_native_fixed_ports(kind, settings):
    graph = make(kind, **settings)
    for name, row in graph.control_descriptors('effect').items():
        if 'choices' not in row:
            continue
        assert row['value'] in row['choices']
        assert row['bindable'] == (row.get('animatable', True) and len(row['choices']) > 1)
        for value in row['choices']:
            changed = graph.apply_control_values({('effect', name): value})
            assert changed.nodes['effect'].inputs == graph.nodes['effect'].inputs
            assert changed.base_control_value('effect', name) == value


def test_transition_applicability_excludes_structurally_invalid_fields_and_marks_dormant_values():
    graph = make('transition')
    rows = graph.control_descriptors('effect')
    assert not {'threshold', 'matte_channel', 'key_color.r', 'wash_seed', 'time_seconds'} & rows.keys()
    assert not rows['center.x']['active'] and not rows['feather']['active']
    assert 'center.x' not in graph.control_descriptors('effect', active_only=True)
    assert {'dissolve', 'wipe', 'radial'} <= set(rows['transition_style']['choices'])
    assert not {'matte', 'spectral', 'gl_transition'} & set(rows['transition_style']['choices'])
    matte = make('transition', transition_style='matte').control_descriptors('effect')
    assert matte['transition_style']['choices'] == ['matte']
    assert not matte['transition_style']['bindable']
    assert matte['key_color.g']['value'] == 255 and not matte['key_color.g']['active']
    chroma = make('transition', transition_style='matte', matte_channel='chroma').control_descriptors('effect')
    assert chroma['key_color.g']['active']


def test_matting_and_transition_feather_keep_different_native_units_and_bounds():
    matte = make('matte').control_descriptors('effect')['feather']
    transition = make('transition', transition_style='wipe').control_descriptors('effect')['feather']
    assert (matte['range'], matte['units']) == ([0, 128], 'pixels')
    assert (transition['range'], transition['units']) == ([0, 1], 'ratio')
    with pytest.raises(ValueError):
        make('transition').apply_control_values({('effect', 'feather'): 2})
    assert make('matte').apply_control_values({('effect', 'feather'): 2}).nodes['effect'].config['feather'] == 2


def test_colour_alias_and_vector_components_use_the_native_resolver_without_mutation():
    graph = make('chroma_key', key_colors=['#00ff00', '#ff00ff'])
    original = deepcopy(graph.nodes['effect'].config)
    rows = graph.control_descriptors('effect')
    assert rows['key_colors.1.b']['type'] == 'integer'
    assert rows['key_colors.1.b']['factory_default'] is None
    changed = graph.apply_control_values({('effect', 'key_color.r'): 128, ('effect', 'key_colors.1.b'): 64})
    assert changed.nodes['effect'].config['key_colors'] == ['#80ff00', '#ff0040']
    assert 'key_color' not in changed.nodes['effect'].config
    assert graph.nodes['effect'].config == original
    vectors = make('chromatic_displacement', red_offset=[2, 3])
    changed = vectors.apply_control_values({('effect', 'red_offset.x'): -4.5})
    assert changed.nodes['effect'].config['red_offset'] == [-4.5, 3]
    assert vectors.nodes['effect'].config['red_offset'] == [2, 3]
    assert vectors.control_descriptors('effect')['red_offset.y']['default'] == 0


@pytest.mark.parametrize('transition', [False, True])
def test_spectral_dynamic_paths_use_stable_band_ids_and_native_bounds(transition):
    graph = make('transition', transition_style='spectral') if transition else make('spectral_displacement')
    rows = graph.control_descriptors('effect')
    assert rows['bands.warm.source_color.r']['value'] == 255
    assert rows['bands.warm.space']['choices'] == ['hsv', 'oklab', 'rgb']
    offset = 'bands.warm.offset_to.x' if transition else 'bands.warm.offset.x'
    assert rows[offset]['range'] == [-4096, 4096]
    assert rows[offset]['group'] == 'bands.warm'
    changed = graph.apply_control_values({('effect', offset): 7.5})
    assert changed.base_control_value('effect', offset) == 7.5
    if transition:
        assert rows['bands.warm.echoes']['type'] == 'integer'
        assert rows['bands.warm.echoes']['range'] == [0, 4]
        with pytest.raises(ValueError):
            graph.apply_control_values({('effect', 'bands.warm.start'): 1})


def test_timing_exposes_existing_native_timer_only_and_rejects_joint_constraint_failures():
    untimed = make('transition')
    assert not any(name.startswith('timing.') for name in untimed.control_descriptors('effect'))
    graph = make('transition', progress=.2, timing={'duration_beats': 4, 'repeat': 'loop', 'period_beats': 8})
    rows = graph.control_descriptors('effect')
    assert rows['timing.duration_beats']['range'] == [1 / 64, 4096]
    assert rows['timing.period_beats']['value'] == 8
    assert rows['timing.steps']['type'] == 'integer'
    assert rows['timing.steps']['editable'] and not rows['timing.steps']['bindable']
    assert 'integer' in rows['timing.steps']['reason']
    assert 'once' not in rows['timing.repeat']['choices']
    changed = graph.apply_control_values({('effect', 'timing.duration_beats'): 6})
    assert changed.nodes['effect'].config['progress'] == .2
    assert changed.nodes['effect'].config['timing']['duration_beats'] == 6
    assert graph.nodes['effect'].config['timing']['duration_beats'] == 4
    for name, value in [('timing.duration_beats', 9), ('timing.steps', 1), ('timing.steps', 2.5)]:
        with pytest.raises(ValueError):
            graph.apply_control_values({('effect', name): value})
    keyed = make('transition', timing={'keys': [{'at': 0, 'value': 0}, {'at': 1, 'value': 1}]})
    assert 'timing.preset' not in keyed.control_descriptors('effect')
    assert 'timing.period_beats' not in keyed.control_descriptors('effect')


def test_gradient_stops_preserve_registry_factory_semantics_and_joint_validation():
    graph = make('layer_style', style_type='gradient_overlay', gradient_stops=[
        {'id': 'cold', 'position': .1, 'color': '#123456'},
        {'id': 'hot', 'position': .9, 'color': '#ffeedd'},
    ])
    row = graph.control_descriptors('effect')['gradient_stops.cold.position']
    assert row['default'] == row['value'] == .1
    assert row['factory_default'] is None and row['default_semantics'] == 'configured_base'
    assert row['constraints']
    with pytest.raises(ValueError):
        graph.apply_control_values({('effect', 'gradient_stops.cold.position'): .95})
    assert graph.nodes['effect'].config['gradient_stops'][0]['position'] == .1


def test_chain_generated_addresses_and_mapping_survive_immutable_static_application():
    graph = EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'chain', 'type': 'transition_chain', 'inputs': ['a', 'b'],
         'stages': [{'id': 'reveal', 'progress': .2}],
         'input_effects': [[{'id': 'dim', 'type': 'opacity', 'opacity': .5}], []]},
    ], 'output': 'chain'})
    stage = graph.chain_expansions['chain']['stages']['reveal']
    effect = graph.chain_expansions['chain']['inputs'][0][0]
    assert graph.control_descriptors(stage)['progress']['value'] == .2
    changed = graph.apply_control_values({(stage, 'progress'): .75, (effect, 'opacity'): .8})
    assert changed.chain_expansions == graph.chain_expansions
    assert changed.base_control_value(stage, 'progress') == .75
    changed.chain_expansions['chain']['stages'].clear()
    assert graph.chain_expansions['chain']['stages']['reveal'] == stage
    assert graph.base_control_value(effect, 'opacity') == .5


def test_static_edit_matches_frame_local_native_render():
    graph = make('chromatic_displacement', red_offset=[0, 0])
    image = Image.new('RGBA', (7, 5))
    image.putpixel((2, 2), (240, 120, 50, 255))
    overrides = {('effect', 'red_offset.x'): 2, ('effect', 'opacity'): .75}
    expected = graph.render({'s0': image}, parameter_values=overrides)
    changed = graph.apply_control_values(overrides)
    assert changed.render({'s0': image}).tobytes() == expected.tobytes()
    assert graph.render({'s0': image}).tobytes() != expected.tobytes()


def test_gl_discovery_exposes_time_uniforms_but_no_shader_authority(tmp_path):
    shader = tmp_path / 'transition.frag'
    shader.write_text('vec4 transition(vec2 uv) { return mix(getFromColor(uv), getToColor(uv), progress); }')
    graph = make('transition', transition_style='gl_transition', shader_path=str(shader),
                 shader_sha256=sha256(shader.read_bytes()).hexdigest(), trusted_shader=True)
    rows = graph.control_descriptors('effect')
    assert set(rows) == {'progress', 'opacity', 'transition_style', 'time_seconds', 'time_delta_seconds'}
    assert rows['transition_style']['choices'] == ['gl_transition']
    assert not rows['transition_style']['bindable']
    assert rows['time_seconds']['range'] == [0, 1e8]
    changed = graph.apply_control_values({('effect', 'time_seconds'): 3})
    assert changed.nodes['effect'].config['time_seconds'] == 3
    assert changed.nodes['effect'].config['shader_sha256'] == graph.nodes['effect'].config['shader_sha256']
    with pytest.raises(ValueError):
        graph.apply_control_values({('effect', 'trusted_shader'): False})


@pytest.mark.parametrize('value', [None, [], 1, 'opacity'])
def test_public_application_rejects_non_mapping_inputs(value):
    with pytest.raises(ValueError):
        make('opacity').apply_control_values(value)


def test_discovery_flags_and_unknown_node_reject_without_guessing():
    graph = make('source')
    with pytest.raises(ValueError):
        graph.control_descriptors('missing')
    with pytest.raises(ValueError):
        graph.control_descriptors('effect', active_only=1)
