"""M10.3 immutable native proposals, expanded-node mapping and source authority."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from framerudiments.compositor import Compositor
from framerudiments.effect_graph import EffectGraph
from framerudiments.graph_authoring_controls import (
    AuthoringControlError, CATALOG_SCHEMA, EDIT_SCHEMA, check_control_sources,
    control_catalog_draft, edit_graph_draft,
)
from framerudiments.modulation import modulation_descriptors
from framerudiments.motion import Motion, named_motion_descriptors, native_motion_names
from framerudiments.preview_stream_contract import canonical_digest
from framerudiments.project import Project
from test_authoring_preview import author
from test_preview_native_project import fixture as native_project


@pytest.fixture
def native(tmp_path):
    path, data = native_project(tmp_path)
    data['effect_graph'] = {'nodes': [
        {'id': 'frame', 'type': 'source'}, {'id': 'background', 'type': 'source'},
        {'id': 'fx', 'type': 'opacity', 'inputs': ['frame'], 'opacity': .5},
    ], 'output': 'fx'}
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


def catalogue(native, document=None, node='fx', sidecars=None):
    path, original = native
    return control_catalog_draft(path, document or original, sidecars=sidecars or {}, node=node,
                                 validate_draft=author.check_draft, temp_parent=path.parent / 'owned')


def proposal(native, operation, document=None, node='fx', sidecars=None):
    path, original = native
    return edit_graph_draft(path, document or original, sidecars=sidecars or {}, node=node,
                            operation=operation, validate_draft=author.check_draft,
                            temp_parent=path.parent / 'owned')


def source_id(catalog, kind='named', pattern='circle'):
    return next(row['id'] for row in catalog['motions'] if row['kind'] == kind
                and (kind != 'named' or row['motion'] == {'kind': 'named', 'pattern': pattern}))


def put(catalog, prop='opacity', settings=None, **extra):
    return {'kind': 'put_binding', 'index': None, 'property': prop,
            'motion_id': source_id(catalog), 'settings': settings or {}, **extra}


def test_catalog_uses_every_released_native_motion_without_sampling_rendering_or_writing(native, monkeypatch):
    path, document = native
    before = {file: file.read_bytes() for file in path.parent.iterdir() if file.is_file()}
    def forbidden(*_args, **_kwargs):
        raise AssertionError('metadata must not sample motion, render pixels or open GPU')
    monkeypatch.setattr(Compositor, '__init__', forbidden)
    monkeypatch.setattr('framerudiments.compositor.create_gpu_backend', forbidden)
    monkeypatch.setattr(Motion, 'sample', forbidden)
    monkeypatch.setattr(Motion, 'values', forbidden)
    catalog = catalogue(native)
    assert catalog['schema'] == CATALOG_SCHEMA
    assert catalog['request_sha256'] == canonical_digest({'project': document, 'sidecars': {}})
    assert {row['motion']['pattern'] for row in catalog['motions']} == set(native_motion_names())
    assert all(row['axes'] == ['x', 'y', 'z'] and len(row['motion_fields']) == 5 for row in catalog['motions'])
    assert catalog['scope'] == {'rendered': False, 'gpu_context_opened': False}
    assert catalog['limits'] == {'bindings': 64}
    opacity = next(row for row in catalog['parameters'] if row['property'] == 'opacity')
    assert opacity['value'] == .5 and opacity['default'] == 1
    assert opacity['binding_defaults'] == {'range': [0, 1]}
    fields = {row['property']: row for row in catalog['binding_fields']}
    assert fields['lag_frames']['maximum'] == 600 and fields['smoothing_frames']['maximum'] == 120
    assert fields['sample_stride']['minimum'] == 1 and fields['steps']['excluded'] == [1]
    assert fields['id']['max_length'] == 64
    assert fields['axis']['default'] == 0
    fields['range']['default'][0] = 999
    assert modulation_descriptors()['range']['default'] == [-1, 1]
    assert named_motion_descriptors()['rate']['minimum'] == .00001
    assert {file: file.read_bytes() for file in before} == before
    assert not list((path.parent / 'owned').iterdir())


@pytest.mark.parametrize('node,prop,value,expected', [
    ({'type': 'chromatic_displacement', 'red_offset': [2, 3]}, 'red_offset.x', -4.5,
     {'red_offset': [-4.5, 3]}),
    ({'type': 'chroma_key', 'key_colors': ['#00ff00', '#ff00ff']}, 'key_colors.1.b', 64,
     {'key_colors': ['#00ff00', '#ff0040']}),
    ({'type': 'spectral_displacement', 'bands': [{'id': 'warm', 'source_color': '#ff0000'}]},
     'bands.warm.offset.x', 7, {'bands': [{'id': 'warm', 'source_color': '#ff0000', 'offset': [7, 0]}]}),
    ({'type': 'layer_style', 'style_type': 'gradient_overlay', 'gradient_stops': [
        {'id': 'cold', 'position': .1, 'color': '#123456'},
        {'id': 'hot', 'position': .9, 'color': '#ffeedd'}]}, 'gradient_stops.cold.color.r', 120,
     {'gradient_stops': [{'id': 'cold', 'position': .1, 'color': '#783456'},
                         {'id': 'hot', 'position': .9, 'color': '#ffeedd'}]}),
    ({'type': 'procedural_field', 'field_palette': [
        {'id': 'cold', 'position': 0, 'color': '#123456'},
        {'id': 'hot', 'position': 1, 'color': '#ffeedd'}]}, 'field_palette.hot.color.b', 64,
     {'field_palette': [{'id': 'cold', 'position': 0, 'color': '#123456'},
                        {'id': 'hot', 'position': 1, 'color': '#ffee40'}]}),
    ({'type': 'transition', 'inputs': ['frame', 'background'],
      'timing': {'repeat': 'loop', 'duration_beats': 4, 'period_beats': 8}},
     'timing.duration_beats', 6, {'timing': {'repeat': 'loop', 'duration_beats': 6, 'period_beats': 8}}),
])
def test_native_component_edit_preserves_unrelated_authored_fields_and_snapshot(native, node, prop, value, expected):
    path, original = native
    document = deepcopy(original)
    document['effect_graph']['nodes'][-1] = {'id': 'fx', 'inputs': ['frame'], **node}
    before = deepcopy(document)
    result = proposal(native, {'kind': 'set_parameter', 'property': prop, 'value': value}, document)
    assert document == before and json.loads(path.read_text()) == original
    candidate = deepcopy(document)
    candidate['effect_graph']['nodes'][-1].update(expected)
    assert result['project'] == candidate
    assert result['schema'] == EDIT_SCHEMA
    assert result['request_sha256'] == canonical_digest({'project': document, 'sidecars': {}})
    identity = canonical_digest({'project': candidate, 'sidecars': {}})
    assert result['validation']['valid'] and result['validation']['request_sha256'] == identity
    assert result['catalog']['request_sha256'] == identity


def test_expanded_chain_stage_preprocessor_and_output_map_back_without_parsing_generated_ids(native):
    _path, original = native
    document = deepcopy(original)
    chain = 'sequence::stage::owned'
    document['effect_graph']['nodes'][-1] = {
        'id': chain, 'type': 'transition_chain', 'inputs': ['frame', 'background'],
        'stages': [{'id': 'reveal', 'transition_style': 'matte', 'matte_input': 'frame', 'progress': .2}],
        'input_effects': [[{'id': 'key', 'type': 'chroma_key', 'key_color': '#00ff00'}], []], 'opacity': .8}
    document['effect_graph']['output'] = chain
    original_chain = deepcopy(document['effect_graph']['nodes'][-1])
    graph = EffectGraph(document['effect_graph'])
    stage = graph.chain_expansions[chain]['stages']['reveal']
    effect = graph.chain_expansions[chain]['inputs'][0][0]
    catalog = catalogue(native, document, stage)
    assert {row['id'] for row in catalog['targets']} == set(graph.nodes)
    assert catalog['node']['authored_id'] == chain
    document = proposal(native, {'kind': 'set_parameter', 'property': 'progress', 'value': .7},
                        document, stage)['project']
    document = proposal(native, {'kind': 'set_parameter', 'property': 'key_color.r', 'value': 100},
                        document, effect)['project']
    document = proposal(native, {'kind': 'set_parameter', 'property': 'opacity', 'value': .6},
                        document, chain)['project']
    expected = deepcopy(original_chain)
    expected['stages'][0]['progress'] = .7
    expected['input_effects'][0][0]['key_color'] = '#64ff00'
    expected['opacity'] = .6
    assert document['effect_graph']['nodes'][-1] == expected
    assert all('inputs' not in row and 'type' not in row for row in expected['stages'])
    assert 'inputs' not in expected['input_effects'][0][0]
    catalog = catalogue(native, document, stage)
    document = proposal(native, put(catalog, 'progress'), document, stage)['project']
    assert document['effect_graph_controls'][0]['node'] == stage


def test_named_binding_settings_and_native_clock_options_are_atomic(native):
    catalog = catalogue(native)
    settings = {'id': 'pulse', 'axis': 'y', 'curve': 'smoothstep', 'range': [.2, .8], 'steps': 4,
                'power': 2, 'invert': True, 'combine': 'set', 'weight': .75,
                'lag_frames': 3, 'smoothing_frames': 4, 'deadzone': .1, 'sample_stride': 2}
    motion_settings = {'rate': 1.5, 'phase_beats': .25, 'origin_beat': 2,
                       'repeat_clock': 'source', 'interpolate': True}
    result = proposal(native, put(catalog, settings=settings, motion_settings=motion_settings))
    expected = {'node': 'fx', 'property': 'opacity', 'motion': {
        'kind': 'named', 'pattern': 'circle', **motion_settings}, **settings}
    assert result['project']['effect_graph_controls'] == [expected]
    binding = result['catalog']['bindings'][0]
    assert binding == {'index': 0, 'config': expected}
    configured = next(row for row in result['catalog']['motions'] if row['motion'] == expected['motion'])
    assert 'configured' in configured['label']
    assert next(row for row in configured['motion_fields'] if row['property'] == 'rate')['value'] == 1.5
    before = deepcopy(result['project'])
    with pytest.raises(AuthoringControlError):
        proposal(native, put(result['catalog'], settings={'steps': 1}, index=0), before)
    assert result['project'] == before


def test_native_categorical_binding_uses_ordered_typed_boolean_choices(native):
    document = deepcopy(native[1])
    document['effect_graph']['nodes'][-1] = {'id': 'fx', 'type': 'matte', 'inputs': ['frame', 'background']}
    catalog = catalogue(native, document)
    result = proposal(native, put(catalog, 'invert', {'choices': [True, False]}), document)
    assert result['project']['effect_graph_controls'][0]['choices'] == [True, False]
    for settings in ({'choices': [True, False], 'range': [0, 1]},
                     {'choices': [True, False], 'combine': 'add'}, {'choices': [True, False], 'weight': .5}):
        with pytest.raises(AuthoringControlError):
            proposal(native, put(catalog, 'invert', settings), document)


def test_binding_order_and_remove_preserve_other_node_rows_and_enforce_native_collisions(native):
    document = deepcopy(native[1])
    def row(name, node='fx'):
        return {'id': name, 'node': node, 'property': 'opacity', 'motion': {'kind': 'named', 'pattern': 'circle'},
                'range': [0, 1], 'combine': 'add', 'weight': .1}
    document['effect_graph_controls'] = [row('first'), row('other', 'frame'), row('last')]
    moved = proposal(native, {'kind': 'move_binding', 'index': 2, 'direction': -1}, document)['project']
    assert [row['id'] for row in moved['effect_graph_controls']] == ['last', 'other', 'first']
    removed = proposal(native, {'kind': 'remove_binding', 'index': 0}, moved)['project']
    assert [row['id'] for row in removed['effect_graph_controls']] == ['other', 'first']
    with pytest.raises(AuthoringControlError):
        proposal(native, {'kind': 'remove_binding', 'index': 0}, removed)
    document['effect_graph_controls'][0].pop('combine')
    catalog = catalogue(native, document)
    with pytest.raises(AuthoringControlError):
        proposal(native, put(catalog), document)
    with pytest.raises(AuthoringControlError):
        proposal(native, {'kind': 'move_binding', 'index': 0, 'direction': 1}, document)


@pytest.mark.parametrize('change', [
    {'property': 'not_native'}, {'motion_id': 'motion:' + 'f' * 64}, {'index': True},
    {'settings': {'node': 'frame'}}, {'settings': {'range': [-1, 1]}},
    {'settings': {'lag_frames': 601}}, {'settings': {'axis': 'missing'}},
    {'motion_settings': {'file': {'path': '/private/secret', 'sha256': 'f' * 64}}},
    {'motion_settings': {'interpolate': 'yes'}},
])
def test_invalid_binding_proposal_leaves_original_untouched(native, change):
    original = deepcopy(native[1])
    catalog = catalogue(native)
    with pytest.raises(AuthoringControlError) as error:
        proposal(native, {**put(catalog), **change})
    assert 'private' not in str(error.value) and native[1] == original


def test_sixty_four_bindings_are_admitted_but_a_sixty_fifth_is_atomic_rejection(native):
    document = deepcopy(native[1])
    document['effect_graph_controls'] = [
        {'node': 'fx', 'property': 'opacity', 'motion': {'kind': 'named', 'pattern': 'circle'},
         'range': [0, 1], 'combine': 'add', 'weight': 0} for _ in range(64)]
    catalog = catalogue(native, document)
    assert len(catalog['bindings']) == 64
    with pytest.raises(AuthoringControlError):
        proposal(native, put(catalog, settings={'combine': 'add', 'weight': 0}), document)
    assert len(document['effect_graph_controls']) == 64


def test_recording_uses_all_native_axes_and_preserves_pinned_configuration(native):
    from dancerudiments_authoring.recording import Recording, RecordingClock, write_recording

    path, original = native
    recording = path.parent / 'native-recording.json'
    write_recording(recording, Recording('synthetic_control_source', ('low', 'mid', 'high', 'air'),
        tuple((i / 511, 0, -.5, .5) for i in range(512)),
        RecordingClock(120, 'synthetic source capture', 'confirmed', 0, 'supplied', 0)))
    source = {'kind': 'recording', 'file': {'path': recording.name, 'sha256': sha256(recording.read_bytes()).hexdigest()},
              'axes': ['low', 'mid', 'high'], 'outside': 'neutral'}
    original['stem_sources'] = {'spectrum': source}
    path.write_text(json.dumps(original), encoding='utf-8')
    catalog = catalogue(native)
    choice = next(row for row in catalog['motions'] if row['kind'] == 'recording')
    assert choice['axes'] == ['low', 'mid', 'high', 'air'] and choice['motion_fields'] == []
    operation = put(catalog, settings={'axis': 'air'}, motion_id=choice['id'])
    candidate = proposal(native, operation)['project']
    assert candidate['effect_graph_controls'][0]['motion'] == source
    assert candidate['effect_graph_controls'][0]['range'] == [0, 1]
    with pytest.raises(AuthoringControlError):
        proposal(native, {**operation, 'motion_settings': {'rate': 2}})
    candidate['effect_graph_controls'][0]['motion']['outside'] = 'error'
    with pytest.raises(AuthoringControlError):
        check_control_sources(original, candidate)
    assert source['outside'] == 'neutral' and 'rate' not in source


def test_unadmitted_pinned_file_cannot_become_a_recording_source(native):
    document = deepcopy(native[1])
    motion = {'kind': 'recording', 'file': document['layers'][0]['asset']}
    document['layers'][0]['motion'] = motion
    assert not any(row['kind'] == 'recording' for row in catalogue(native, document)['motions'])
    document['effect_graph_controls'] = [{'node': 'fx', 'property': 'opacity', 'motion': motion, 'range': [0, 1]}]
    with pytest.raises(AuthoringControlError):
        author.check_draft(native[1], document)


@pytest.mark.parametrize('config,prop', [
    ({'type': 'transition', 'inputs': ['frame', 'background'],
      'timing': {'duration_beats': 4, 'period_beats': 8, 'repeat': 'loop'}}, 'timing.duration_beats'),
    ({'type': 'transition', 'inputs': ['frame', 'background'],
      'timing': {'duration_beats': 4, 'period_beats': 8, 'repeat': 'loop'}}, 'timing.period_beats'),
    ({'type': 'transition', 'inputs': ['frame', 'background'], 'transition_style': 'spectral',
      'bands': [{'id': 'warm', 'source_color': '#ff0000'}]}, 'bands.warm.start'),
    ({'type': 'transition', 'inputs': ['frame', 'background'], 'transition_style': 'spectral',
      'bands': [{'id': 'warm', 'source_color': '#ff0000'}]}, 'bands.warm.end'),
    ({'type': 'layer_style', 'style_type': 'gradient_overlay', 'gradient_stops': [
        {'id': 'cold', 'position': .1, 'color': '#123456'},
        {'id': 'hot', 'position': .9, 'color': '#ffeedd'}]}, 'gradient_stops.cold.position'),
    ({'type': 'procedural_field', 'field_output': 'color', 'field_palette': [
        {'id': 'cold', 'position': 0, 'color': '#123456'},
        {'id': 'hot', 'position': .8, 'color': '#ffeedd'}]}, 'field_palette.cold.position'),
])
def test_generated_binding_defaults_respect_native_joint_constraints_in_real_frames(native, config, prop):
    path, original = native
    document = deepcopy(original)
    document['effect_graph']['nodes'][-1] = {'id': 'fx', 'inputs': ['frame'], **config}
    catalog = catalogue(native, document)
    descriptor = next(row for row in catalog['parameters'] if row['property'] == prop)
    assert descriptor['bindable'] and descriptor['binding_defaults']['range'][0] < descriptor['binding_defaults']['range'][1]
    candidate = proposal(native, put(catalog, prop), document)['project']
    compositor = Compositor(Project(candidate, path.parent))
    try:
        for frame in (0, 1, 7, 15, 30, 59, 75):
            image, _ = compositor.frame(frame, apply_quality=False)
            assert image.size == (64, 48)
    finally:
        compositor.close()


def test_exact_integer_timing_steps_are_static_editable_and_have_no_unsafe_continuous_binding(native):
    document = deepcopy(native[1])
    document['effect_graph']['nodes'][-1] = {'id': 'fx', 'type': 'transition', 'inputs': ['frame', 'background'],
                                            'timing': {'duration_beats': 4}}
    catalog = catalogue(native, document)
    steps = next(row for row in catalog['parameters'] if row['property'] == 'timing.steps')
    assert steps['editable'] and not steps['bindable'] and 'integer' in steps['reason']
    result = proposal(native, {'kind': 'set_parameter', 'property': 'timing.steps', 'value': 4}, document)
    assert result['project']['effect_graph']['nodes'][-1]['timing']['steps'] == 4


def test_edited_sidecars_never_replace_original_pins_in_returned_project(native):
    path, original = native
    cues = path.parent / 'cues.lrc'
    cues.write_text('[00:00.250][SECTION: ORIGINAL]\n', encoding='utf-8')
    original['cues'] = {'path': cues.name, 'sha256': sha256(cues.read_bytes()).hexdigest()}
    path.write_text(json.dumps(original), encoding='utf-8')
    before = path.read_bytes(), cues.read_bytes()
    sidecars = {'cues': [{'start': .5, 'label': '[SECTION: EDITED]'}]}
    result = proposal(native, {'kind': 'set_parameter', 'property': 'opacity', 'value': .3}, sidecars=sidecars)
    assert result['project']['cues'] == original['cues']
    assert str(path.parent / 'owned') not in json.dumps(result)
    assert result['catalog']['request_sha256'] == canonical_digest({'project': result['project'], 'sidecars': sidecars})
    assert (path.read_bytes(), cues.read_bytes()) == before
    assert not list((path.parent / 'owned').iterdir())


def test_admitted_gl_uniform_edit_keeps_relative_shader_path_and_all_execution_authority(native):
    from test_gl_transitions import EXAMPLE

    path, original = native
    shader = path.parent / 'admitted.glsl'
    shader.write_bytes(EXAMPLE.read_bytes())
    original['effect_graph']['nodes'][-1] = {
        'id': 'fx', 'type': 'transition', 'inputs': ['frame', 'background'],
        'transition_style': 'gl_transition', 'shader_path': shader.name,
        'shader_sha256': sha256(shader.read_bytes()).hexdigest(), 'trusted_shader': True,
        'progress': .25, 'time_seconds': 1, 'time_delta_seconds': 1 / 60, 'frame_index': 30}
    path.write_text(json.dumps(original), encoding='utf-8')
    before = deepcopy(original['effect_graph']['nodes'][-1])
    result = proposal(native, {'kind': 'set_parameter', 'property': 'time_seconds', 'value': 2})
    assert result['project']['effect_graph']['nodes'][-1] == {**before, 'time_seconds': 2}
    assert result['project']['effect_graph']['nodes'][-1]['shader_path'] == shader.name
    assert result['catalog']['scope']['gpu_context_opened'] is False
    with pytest.raises(AuthoringControlError) as error:
        proposal(native, {'kind': 'set_parameter', 'property': 'shader_path', 'value': '/private/untrusted'})
    assert error.value.code == 'parameter_readonly'


def test_catalog_rechecks_source_pins_after_discovery_and_always_cleans_owned_files(native, monkeypatch):
    from framerudiments import graph_authoring_controls

    path, _document = native
    original = graph_authoring_controls.authoring_control_catalog
    def change_pin(*args, **kwargs):
        result = original(*args, **kwargs)
        (path.parent / 'source.wav').write_bytes(b'changed after discovery')
        return result
    monkeypatch.setattr(graph_authoring_controls, 'authoring_control_catalog', change_pin)
    with pytest.raises(AuthoringControlError) as error:
        catalogue(native)
    assert error.value.code == 'input_changed'
    assert not list((path.parent / 'owned').iterdir())


def test_catalog_bounds_fail_closed_and_leave_no_sidecars(native, monkeypatch):
    monkeypatch.setattr('framerudiments.graph_authoring_controls.MAX_MOTIONS', 1)
    with pytest.raises(AuthoringControlError) as error:
        catalogue(native)
    assert error.value.code == 'catalog_limit'
    assert not list((native[0].parent / 'owned').iterdir())
