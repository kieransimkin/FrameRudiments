"""S5 pure dependency discovery. No media rendering or clock substitution."""
from copy import deepcopy
from fractions import Fraction
from types import SimpleNamespace

import pytest

from framerudiments.preview_dependencies import (
    TemporalReach, build_dependency_index, layer_frame_ranges,
)
from framerudiments.preview_stream_contract import canonical_digest, quality_profiles

NAMESPACE = 'a' * 64
IMPLEMENTATION = 'b' * 64


def project(*, repeats=1, cropped=False):
    start = 1000 if cropped else 0
    data = {'schema': 'framerudiments.project.v1', 'width': 16, 'height': 16,
            'background': {'colour': '#102030'}, 'clock': {'bpm': 120},
            'layers': [{'id': 'middle', 'type': 'sprite', 'width': 4, 'center': [8, 8],
                        'start_seconds': start/1000 + 1, 'end_seconds': start/1000 + 2,
                        'opacity': .5}],
            'render_quality': {'spatial_scale': 1, 'temporal_samples': 1}}
    p = SimpleNamespace(data=data, width=16, height=16, fps=10, sample_rate=1000,
                        start_sample=start, end_sample=start+3000, repeats=repeats,
                        frame_count=30*repeats, inputs=[], canonical_inputs=None,
                        output_quality=SimpleNamespace(enabled=True, spatial_scale=1, temporal_samples=1),
                        cues=[{'frame': n, 'text': f'Section {n}'} for n in range(0, 30*repeats, 10)])
    p.source_time = lambda frame: float(Fraction(start, 1000) + Fraction(frame, 10) % 3)
    p.continuous_beat = lambda frame: frame / 5
    p.verify_inputs = lambda: None
    return p


def manifest(p, data=None, *, reach=None, implementation=IMPLEMENTATION, capabilities=None):
    return build_dependency_index(p, data or p.data, {}, namespace=NAMESPACE,
                                  renderer_abi='test-s5', implementation=implementation,
                                  reach=reach, capabilities=capabilities)


def changed(a, b):
    prior = {s.id: s.fingerprint for s in a.sections}
    return [s.start_frame for s in b.sections if prior.get(s.id) != s.fingerprint]


def test_local_layer_effect_invalidates_complete_own_section_only():
    p = project()
    before = deepcopy(p.data)
    a = manifest(p)
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .7
    b = manifest(p, draft)
    assert changed(a, b) == [10]
    assert p.data == before
    assert a.global_fingerprint == b.global_fingerprint
    assert a.digest != b.digest
    layer = next(key for key, _ in a.components if key.startswith('layer:'))
    assert a.affected_by(layer) == (a.sections[1].id,)


@pytest.mark.parametrize('field,value', [
    ('effect_graph', {'nodes': [{'id': 'frame', 'type': 'source'}], 'output': 'frame'}),
    ('background', {'colour': '#ffff00'}),
    ('clock', {'bpm': 122}),
    ('render_quality', {'spatial_scale': 2}),
    ('unrecognised_future_pipeline_field', {'mode': 'anything'}),
    ('effect_graph_controls', [{'target': 'blur', 'property': 'filter_radius'}]),
])
def test_shared_or_unknown_render_dependencies_fail_conservatively(field, value):
    p = project()
    a = manifest(p)
    draft = deepcopy(p.data)
    draft[field] = value
    assert changed(a, manifest(p, draft)) == [0, 10, 20]


def test_native_implementation_change_invalidates_all_sections():
    p = project()
    assert changed(manifest(p), manifest(p, implementation='c'*64)) == [0, 10, 20]


def test_declared_temporal_windows_expand_both_directions():
    p = project()
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .8
    reach = TemporalReach('bounded', past_frames=3, future_frames=3)
    a, b = manifest(p, reach=reach), manifest(p, draft, reach=reach)
    assert changed(a, b) == [0, 10, 20]
    assert (a.sections[1].id, a.sections[0].id) in a.temporal_edges
    assert (a.sections[1].id, a.sections[2].id) in a.temporal_edges


def test_feedback_invalidates_downstream_until_a_verified_reset():
    p = project()
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .8
    reach = TemporalReach('feedback')
    assert changed(manifest(p, reach=reach), manifest(p, draft, reach=reach)) == [10, 20]
    reset = TemporalReach('feedback', reset_frames=(20,))
    assert changed(manifest(p, reach=reset), manifest(p, draft, reach=reset)) == [10]
    unknown = TemporalReach('unknown')
    assert changed(manifest(p, reach=unknown), manifest(p, draft, reach=unknown)) == [0, 10, 20]


def test_source_window_repeats_are_not_interpreted_as_output_seconds():
    p = project(repeats=2, cropped=True)
    layer = p.data['layers'][0]
    assert layer_frame_ranges(p, layer) == ((10, 20), (40, 50))
    for frame in range(p.frame_count):
        active = layer['start_seconds'] <= p.source_time(frame) < layer['end_seconds']
        assert active == any(a <= frame < b for a, b in layer_frame_ranges(p, layer))
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .9
    assert changed(manifest(p), manifest(p, draft)) == [10, 40]


def test_fractional_time_gates_include_supersample_boundary_frames():
    p = project()
    layer = {**p.data['layers'][0], 'start_seconds': 1.025, 'end_seconds': 1.975}
    assert layer_frame_ranges(p, layer) == ((10, 20),)
    assert layer_frame_ranges(p, {**layer, 'start_seconds': 9, 'end_seconds': 10}) == ()


def test_moving_layer_invalidates_old_and_new_section_not_only_destination():
    p = project()
    draft = deepcopy(p.data)
    draft['layers'][0].update(start_seconds=2, end_seconds=3)
    assert changed(manifest(p), manifest(p, draft)) == [10, 20]


def test_shared_asset_reverse_index_covers_each_consumer():
    p = project()
    p.data['layers'][0]['asset'] = {'path': 'private/source.png', 'sha256': 'd'*64}
    p.data['layers'].append({**deepcopy(p.data['layers'][0]), 'id': 'later', 'start_seconds': 2,
                             'end_seconds': 3})
    a = manifest(p)
    key = next(k for k, _ in a.components if k.startswith('asset:'))
    assert a.affected_by(key) == (a.sections[1].id, a.sections[2].id)
    draft = deepcopy(p.data)
    for layer in draft['layers']:
        layer['asset']['sha256'] = 'e'*64
    assert changed(a, manifest(p, draft)) == [10, 20]
    assert 'private/source.png' not in str(a.document())


def test_layer_source_reference_is_followed_transitively():
    p = project()
    p.data['layers'].append({**deepcopy(p.data['layers'][0]), 'id': 'consumer',
                             'start_seconds': 2, 'end_seconds': 3})
    p.data['layer_masks'] = {'layer:consumer': {'source': 'layer:middle'}}
    a = manifest(p)
    draft = deepcopy(p.data)
    draft['layers'][0]['opacity'] = .1
    assert changed(a, manifest(p, draft)) == [10, 20]


def test_global_capability_changes_invalidate_all_not_whole_draft_hash_changes():
    p = project()
    caps = [{'id': f'Q{n}', 'supported': True, 'settings_sha256': 'c'*64} for n in range(6)]
    a = manifest(p, capabilities=caps)
    modified = deepcopy(caps)
    for row in modified:
        row['settings_sha256'] = 'd'*64
    assert changed(a, manifest(p, capabilities=modified)) == []
    modified[0]['supported'] = False
    assert changed(a, manifest(p, capabilities=modified)) == [0, 10, 20]


def test_canonical_identity_and_frozen_returned_descriptors():
    p = project()
    a = manifest(p)
    reverse = dict(reversed(list(p.data.items())))
    assert a == manifest(p, reverse)
    document = a.document()
    assert canonical_digest(document) == a.digest
    document['namespace'] = 'x'
    assert a.namespace == NAMESPACE
    assert a.segments()[1].start_frame == 10
    assert len(quality_profiles(p)) == 6


@pytest.mark.parametrize('layer', [
    {'id': 'x', 'start_seconds': True}, {'id': 'x', 'end_seconds': float('nan')},
    {'id': 'x', 'start_seconds': -1}, {'id': ''},
])
def test_malformed_layer_domains_are_not_cached_as_independent(layer):
    p = project()
    p.data['layers'] = [layer]
    with pytest.raises(ValueError):
        manifest(p)


@pytest.mark.parametrize('field,before,after', [
    ('layer_animations', {'opacity': {'keys': [{'time': 0, 'value': 1}]}},
                         {'opacity': {'keys': [{'time': 0, 'value': .2}]}}),
    ('layer_masks', {'source': 'background', 'invert': False},
                    {'source': 'background', 'invert': True}),
    ('layer_displacement', {'source': 'background', 'strength': 1},
                           {'source': 'background', 'strength': 2}),
])
def test_known_layer_pipeline_edit_has_the_same_section_scope_as_layer(field, before, after):
    p = project()
    p.data[field] = {'layer:middle': before}
    draft = deepcopy(p.data)
    draft[field]['layer:middle'] = after
    assert changed(manifest(p), manifest(p, draft)) == [10]


def test_scoped_post_effects_are_local_but_first_isolation_enable_is_global():
    p = project()
    p.data['post_effects'] = {'elements': {'layer:middle': [{'id': 'local-glow', 'visual': {'glow': .1}}]}}
    a = manifest(p)
    draft = deepcopy(p.data)
    draft['post_effects']['elements']['layer:middle'][0]['visual']['glow'] = .7
    assert changed(a, manifest(p, draft)) == [10]
    # Adding the first isolated pipeline may change final alpha handling;
    # do not pretend only the active target can change in that case.
    empty = deepcopy(p.data)
    del empty['post_effects']
    assert changed(manifest(p, empty), a) == [0, 10, 20]
    global_edit = deepcopy(p.data)
    global_edit['post_effects']['frame'] = [{'id': 'global', 'visual': {'glow': .2}}]
    assert changed(a, manifest(p, global_edit)) == [0, 10, 20]


def test_reverse_edges_include_asset_to_layer_and_graph_node_dependencies():
    p = project()
    p.data['layers'][0]['asset'] = {'path': 'image.png', 'sha256': 'd'*64}
    p.data['effect_graph'] = {'nodes': [{'id': 'layer:middle', 'type': 'source'},
        {'id': 'fade', 'type': 'opacity', 'inputs': ['layer:middle']}], 'output': 'fade'}
    index = manifest(p)
    assert any(a.startswith('asset:') and b.startswith('layer:') for a, b in index.component_edges)
    assert any(a.startswith('layer:') and b.startswith('graph-node:') for a, b in index.component_edges)
    assert any(a.startswith('graph-node:') and b.startswith('graph-node:') for a, b in index.component_edges)


@pytest.mark.parametrize('post', [None, [], {'elements': []}])
def test_invalid_shared_post_pipeline_fails_closed(post):
    p = project()
    p.data['post_effects'] = post
    with pytest.raises(ValueError, match='must be an object'):
        manifest(p)
