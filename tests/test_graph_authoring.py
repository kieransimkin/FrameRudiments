"""M10.2 canonical authoring admission using real pinned native Projects.

Pixel qualification of actual TypeScript exports lives in integration/. These
tests target native rejection, metadata and dry-run resource/pin boundaries.
"""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from framerudiments.compositor import Compositor
from framerudiments.graph_authoring import (
    MAX_SOURCES, SCHEMA, available_graph_sources, validate_authoring_graph, validate_graph_draft,
)
from framerudiments.preview_stream_contract import canonical_digest
from framerudiments.project import Project
from test_authoring_preview import author
from test_preview_native_project import fixture as native_project


@pytest.fixture
def native(tmp_path):
    path, data = native_project(tmp_path)
    return path, data


def report_for(native, nodes, output='frame', **fields):
    path, data = native
    document = deepcopy(data)
    document.update(fields)
    document['effect_graph'] = {'nodes': nodes, 'output': output}
    return validate_authoring_graph(Project(document, path.parent))


def source(name='frame'):
    return {'id': name, 'type': 'source'}


def assert_invalid(report, code):
    assert report['valid'] is False
    assert report['errors'][0]['code'] == code
    assert len(report['errors']) == 1
    assert len(report['errors'][0]['message']) <= 160
    assert report['nodes'] == []


def test_native_project_and_named_controls_validate_without_pixels_or_gpu(native, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('dry-run must not construct or evaluate a compositor')
    monkeypatch.setattr(Compositor, '__init__', forbidden)
    monkeypatch.setattr('framerudiments.compositor.create_gpu_backend', forbidden)
    path, data = native
    before = path.read_bytes()
    nodes = [source(), source('background'),
             {'id': 'a', 'type': 'opacity', 'inputs': ['frame'], 'opacity': .5},
             {'id': 'b', 'type': 'copy', 'inputs': ['frame']},
             {'id': 'merge', 'type': 'composite', 'inputs': ['a', 'b', 'background']}]
    controls = [{'id': 'pulse', 'node': 'a', 'property': 'opacity',
                 'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': [0, 1]}]
    result = report_for(native, nodes, 'merge', effect_graph_controls=controls, effect_graph_backend='gpu')
    assert result['schema'] == SCHEMA
    assert result['valid'] and not result['errors']
    assert result['scope'] == {'rendered': False, 'gpu_context_opened': False, 'all_nodes': True,
                               'source_names': True, 'control_bindings': True}
    assert result['nodes'][-1]['inputs'] == [
        {'index': index, 'role': 'layer', 'source': name, 'format': 'RGBA8'}
        for index, name in enumerate(['a', 'b', 'background'])]
    assert result['sources'] == [{'id': name, 'format': 'RGBA8'}
                                  for name in ['background', 'frame', 'layer:square']]
    assert path.read_bytes() == before and data == json.loads(before)


@pytest.mark.parametrize('config,inputs,roles,bounds', [
    ({'type': 'copy'}, ['frame'], ['image'], (1, 1)),
    ({'type': 'matte'}, ['frame', 'background', 'layer:square'], ['image', 'mask', 'mask'], (2, 9)),
    ({'type': 'transition'}, ['frame', 'background'], ['from', 'to'], (2, 2)),
    ({'type': 'transition', 'transition_style': 'matte'},
     ['frame', 'background', 'layer:square'], ['from', 'to', 'mask'], (3, 3)),
    ({'type': 'spatial_warp', 'warp_type': 'displacement'},
     ['frame', 'background'], ['image', 'displacement'], (2, 2)),
    ({'type': 'spatial_warp', 'warp_type': 'wave'}, ['frame'], ['image'], (1, 1)),
    ({'type': 'layer_style', 'style_type': 'pattern_overlay', 'pattern_kind': 'texture'},
     ['frame', 'background'], ['image', 'texture'], (2, 2)),
    ({'type': 'layer_style', 'style_type': 'pattern_overlay'}, ['frame'], ['image'], (1, 1)),
    ({'type': 'layer_style', 'style_type': 'gradient_overlay'}, ['frame'], ['image'], (1, 1)),
    ({'type': 'procedural_field'}, ['frame'], ['canvas'], (1, 1)),
    ({'type': 'transition_chain', 'stages': [{'id': 'one', 'progress': .5}]},
     ['frame', 'background'], ['from', 'to'], (2, 8)),
])
def test_native_port_roles_preserve_order_and_one_rgba_format(native, config, inputs, roles, bounds):
    nodes = [source(), source('background'), source('layer:square'),
             {'id': 'fx', **config, 'inputs': inputs}]
    result = report_for(native, nodes, 'fx')
    assert result['valid'], result
    node = result['nodes'][-1]
    assert (node['min_inputs'], node['max_inputs']) == bounds
    assert [port['role'] for port in node['inputs']] == roles
    assert [port['source'] for port in node['inputs']] == inputs
    assert {port['format'] for port in node['inputs']} == {'RGBA8'}
    assert node['output'] == {'format': 'RGBA8'}


@pytest.mark.parametrize('invalid', [
    {'id': 'bad', 'type': 'unknown'},
    {'id': 'bad', 'type': []},
    {'id': 'bad', 'type': 'copy', 'inputs': []},
    {'id': 'bad', 'type': 'source', 'inputs': ['frame']},
    {'id': 'bad', 'type': 'opacity', 'inputs': ['frame'], 'opacity': 1.01},
    {'id': 'bad', 'type': 'filter', 'inputs': ['frame'], 'filter_radius': -1},
    {'id': 'bad', 'type': 'matte', 'inputs': ['frame']},
    {'id': 'bad', 'type': 'transition', 'transition_style': 'matte', 'inputs': ['frame', 'frame']},
    {'id': 'bad', 'type': 'spatial_warp', 'warp_type': 'displacement', 'inputs': ['frame']},
    {'id': 'bad', 'type': 'layer_style', 'style_type': 'pattern_overlay',
     'pattern_kind': 'texture', 'inputs': ['frame']},
    {'id': 'bad', 'type': 'layer_style', 'style_type': 'gradient_overlay', 'inputs': ['frame', 'frame']},
    {'id': 'bad', 'type': 'copy', 'inputs': ['frame'], 'private_text': 'must not echo'},
])
def test_disconnected_native_schema_and_arity_still_rejected(native, invalid):
    result = report_for(native, [source(), invalid])
    assert_invalid(result, 'graph_schema')
    assert 'must not echo' not in json.dumps(result)


@pytest.mark.parametrize('nodes,output,code', [
    ([source(), {'id': 'hidden', 'type': 'copy', 'inputs': ['missing']}], 'frame', 'graph_topology'),
    ([source(), {'id': 'a', 'type': 'copy', 'inputs': ['b']},
      {'id': 'b', 'type': 'copy', 'inputs': ['a']}], 'frame', 'graph_topology'),
    ([source(), source('private-missing-provider')], 'frame', 'source_unavailable'),
    ([source('scene')], 'scene', 'source_unavailable'),
    ([source()], 'missing', 'graph_topology'),
    ([source(), source()], 'frame', 'graph_schema'),
    ([source()] + [{'id': f'n{i}', 'type': 'copy', 'inputs': ['frame']} for i in range(256)],
     'frame', 'graph_schema'),
    ([source(), {'id': 'many', 'type': 'composite', 'inputs': ['frame'] * 33}], 'frame', 'graph_schema'),
])
def test_all_nodes_use_native_limits_and_available_sources(native, nodes, output, code):
    assert_invalid(report_for(native, nodes, output), code)


@pytest.mark.parametrize('matte_input,code', [('sequence', 'graph_topology'), ('missing', 'graph_topology')])
def test_disconnected_chain_matte_dependencies_are_checked_after_native_expansion(native, matte_input, code):
    nodes = [source(), source('background'), {'id': 'sequence', 'type': 'transition_chain',
             'inputs': ['frame', 'background'], 'stages': [
                 {'id': 'hidden', 'transition_style': 'matte', 'matte_input': matte_input}]}]
    assert_invalid(report_for(native, nodes), code)


def test_chain_expansion_limit_is_canonical_even_if_authored_count_fits(native):
    nodes = [source(), {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['frame', 'frame'],
                      'stages': [{'id': 'one'}]}]
    nodes += [{'id': f'n{i}', 'type': 'copy', 'inputs': ['frame']} for i in range(254)]
    assert len(nodes) == 256
    assert_invalid(report_for(native, nodes), 'graph_schema')


@pytest.mark.parametrize('changes', [
    {'node': 'missing'}, {'property': 'private-unknown-property'}, {'id': 1}, {'id': 'x' * 65},
    {'choices': ['wrong']}, {'motion': {'kind': 'named', 'pattern': 'private-missing-motion'}},
])
def test_native_binding_targets_ids_choices_and_motion_are_authoritative(native, changes):
    control = {'id': 'pulse', 'node': 'dim', 'property': 'opacity',
               'motion': {'kind': 'named', 'pattern': 'circle'}, 'axis': 'x', 'range': [0, 1], **changes}
    result = report_for(native, [source(), {'id': 'dim', 'type': 'opacity', 'inputs': ['frame']}],
                        effect_graph_controls=[control])
    assert_invalid(result, 'control_binding')
    assert 'private-' not in json.dumps(result)


def test_duplicate_replacement_control_and_dangling_graph_control_rejected(native):
    control = {'node': 'frame', 'property': 'opacity',
               'motion': {'kind': 'named', 'pattern': 'circle'}, 'range': [0, 1]}
    assert_invalid(report_for(native, [source()], effect_graph_controls=[control, control]), 'control_binding')
    path, data = native
    data['effect_graph_controls'] = [control]
    assert_invalid(validate_authoring_graph(Project(data, path.parent)), 'control_binding')


def test_source_inventory_is_bounded_and_excludes_private_text_or_scene():
    document = {'layers': [{'id': 'one'}], 'identity': [{'text': 'PRIVATE CAPTION'}],
                'lyric_art': {'items': [{'text': 'PRIVATE LYRIC'}]}, 'captions': {'colour': 'white'},
                'lyrics': {}, 'word_captions': {}, 'word_indicator': {}}
    assert available_graph_sources(document) == [
        'background', 'caption', 'frame', 'identity:0', 'layer:one', 'lyric-art:0',
        'word-caption', 'word-indicator']
    document = {'layers': [{'id': str(i)} for i in range(MAX_SOURCES - 2)]}
    assert len(available_graph_sources(document)) == MAX_SOURCES
    document['layers'].append({'id': 'over-limit'})
    with pytest.raises(ValueError, match='limit'):
        available_graph_sources(document)
    with pytest.raises(ValueError, match='duplicate'):
        available_graph_sources({'layers': [{'id': 'same'}, {'id': 'same'}]})


def test_native_draft_sidecars_identity_pins_and_cleanup(native, tmp_path, monkeypatch):
    path, data = native
    cue = tmp_path / 'private-cues.lrc'
    cue.write_text('[00:00.250][SECTION: PRIVATE ORIGINAL]\n', encoding='utf-8')
    data['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    path.write_text(json.dumps(data), encoding='utf-8')
    before = {item: item.read_bytes() for item in tmp_path.iterdir() if item.is_file()}
    draft = deepcopy(data)
    draft['effect_graph'] = {'nodes': [source()], 'output': 'frame'}
    sidecars = {'cues': [{'start': .5, 'label': '[SECTION: PRIVATE EDITED]'}]}
    def forbidden(*_args, **_kwargs):
        raise AssertionError('validation constructed a compositor')
    monkeypatch.setattr(Compositor, '__init__', forbidden)
    result = validate_graph_draft(path, draft, sidecars=sidecars, validate_draft=author.check_draft,
                                  temp_parent=tmp_path / 'owned')
    assert result['valid'], result
    assert result['request_sha256'] == canonical_digest({'project': draft, 'sidecars': sidecars})
    assert 'PRIVATE' not in json.dumps(result)
    assert {item: item.read_bytes() for item in before} == before
    assert not list((tmp_path / 'owned').iterdir())
    bad = validate_graph_draft(path, draft, sidecars={'cues': [{'start': .2, 'label': 'not a cue'}]},
                              validate_draft=author.check_draft, temp_parent=tmp_path / 'owned')
    assert_invalid(bad, 'project_invalid')
    assert not list((tmp_path / 'owned').iterdir())


def test_validation_detects_pin_change_during_native_control_admission(native, tmp_path, monkeypatch):
    from framerudiments import graph_authoring

    path, data = native
    data['effect_graph'] = {'nodes': [source()], 'output': 'frame'}
    original = graph_authoring.validate_authoring_graph
    def modify_after_validation(project, **kwargs):
        result = original(project, **kwargs)
        (tmp_path / 'source.wav').write_bytes(b'changed')
        return result
    monkeypatch.setattr(graph_authoring, 'validate_authoring_graph', modify_after_validation)
    result = validate_graph_draft(path, data, sidecars={}, validate_draft=author.check_draft,
                                  temp_parent=tmp_path / 'owned')
    assert result['valid'] is False and result['errors'][0]['code'] == 'input_changed'
    assert not list((tmp_path / 'owned').iterdir())


def test_no_graph_result_has_no_claimed_graph_hash(native):
    path, data = native
    result = validate_authoring_graph(Project(data, path.parent))
    assert result['valid'] and result['graph_present'] is False
    assert result['graph_sha256'] is None and result['nodes'] == []
