"""M10.3 live HTTP framing, atomic rejection and native preview admission."""
from copy import deepcopy
from http.client import HTTPConnection, HTTPResponse
import json
import socket

import pytest

from framerudiments.compositor import Compositor
from framerudiments.graph_authoring_controls import CATALOG_SCHEMA, EDIT_SCHEMA
from framerudiments.preview_stream_contract import canonical_digest
from framerudiments.project import Project
from test_authoring_graph_http import PRIVATE, loopback as native_loopback
from test_authoring_preview import author


CATALOG = '/api/graph/catalog'
EDIT = '/api/graph/edit'
loopback = native_loopback


def test_native_catalog_and_parameter_edit_bind_their_exact_full_snapshots(loopback, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('catalogue/proposal must not construct a compositor or GPU')
    monkeypatch.setattr(Compositor, '__init__', forbidden)
    monkeypatch.setattr('framerudiments.compositor.create_gpu_backend', forbidden)
    draft = loopback.draft()
    original = deepcopy(draft)
    sidecars = {'cues': [{'start': .5, 'label': f'[SECTION: {PRIVATE} EDIT]'}]}
    body = {'project': draft, 'sidecars': sidecars, 'node': 'fork-a'}
    code, catalog, headers = loopback.request(CATALOG, body)
    assert code == 200 and catalog['schema'] == CATALOG_SCHEMA
    assert catalog['node']['id'] == 'fork-a'
    assert catalog['request_sha256'] == canonical_digest({'project': draft, 'sidecars': sidecars})
    assert catalog['scope'] == {'rendered': False, 'gpu_context_opened': False}
    assert PRIVATE not in json.dumps(catalog) and str(loopback.directory) not in json.dumps(catalog)
    assert headers['Cache-Control'] == 'no-store' and 'Access-Control-Allow-Origin' not in headers
    code, result, _ = loopback.request(EDIT, {**body,
        'operation': {'kind': 'set_parameter', 'property': 'opacity', 'value': .6}})
    assert code == 200 and result['schema'] == EDIT_SCHEMA
    assert result['project']['effect_graph']['nodes'][2]['opacity'] == .6
    assert result['project']['cues'] == draft['cues']
    assert result['request_sha256'] == catalog['request_sha256']
    updated = canonical_digest({'project': result['project'], 'sidecars': sidecars})
    assert result['validation']['valid'] and result['validation']['request_sha256'] == updated
    assert result['catalog']['request_sha256'] == updated
    assert draft == original and not list(loopback.owned.iterdir())
    assert loopback.api.active is None and not loopback.cache.exists()


@pytest.mark.parametrize('endpoint', [CATALOG, EDIT])
@pytest.mark.parametrize('origin', [None, 'https://foreign.example', 'http://127.0.0.1:1'])
def test_native_editing_requires_exact_same_origin_before_preparing_project(loopback, endpoint, origin):
    code, reply, _ = loopback.request(endpoint, {'project': loopback.draft(), 'node': 'fork-a'}, origin=origin)
    assert code == 403 and reply['code'] == 'request_invalid'
    assert not loopback.owned.exists()


@pytest.mark.parametrize('endpoint', [CATALOG, EDIT])
def test_early_origin_rejection_is_complete_before_body_upload(loopback, endpoint):
    # Real split TCP upload: the rejected body has not arrived when 403 is sent.
    payload = json.dumps({'project': loopback.draft(), 'node': 'fork-a'}).encode()
    headers = (f'POST {endpoint} HTTP/1.1\r\nHost: 127.0.0.1:{loopback.port}\r\n'
               'Origin: https://foreign.example\r\nContent-Type: application/json\r\n'
               f'Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n').encode()
    connection = socket.create_connection(('127.0.0.1', loopback.port), timeout=5)
    response = None
    try:
        connection.sendall(headers)
        response = HTTPResponse(connection)
        response.begin()
        assert response.status == 403 and response.getheader('Connection') == 'close'
        assert json.loads(response.read())['code'] == 'request_invalid'
        # An in-flight body is discarded only after the complete rejection.
        connection.sendall(payload)
        connection.shutdown(socket.SHUT_WR)
        assert connection.recv(1) == b''
    finally:
        if response is not None:
            response.close()
        connection.close()
    assert not loopback.owned.exists()


@pytest.mark.parametrize('endpoint,headers,raw', [
    (CATALOG + '?node=frame', {}, b'{}'),
    (EDIT + '?operation=delete', {}, b'{}'),
    (CATALOG, {'Content-Type': 'text/plain'}, b'{}'),
    (EDIT, {'Content-Length': str(author.MAX_BODY + 1)}, b'{}'),
    (CATALOG, {'Transfer-Encoding': 'chunked'}, b'0\r\n\r\n'),
    (EDIT, {}, b'{broken'),
    (CATALOG, {}, b'{"project":{},"node":"x","bad":true}'),
    (CATALOG, {}, b'{"project":{},"node":"x","operation":{}}'),
    (EDIT, {}, b'{"project":{},"node":"x"}'),
    (EDIT, {}, b'{"project":{},"node":"x","operation":{"value":NaN}}'),
    (CATALOG, {}, b'{"project":{},"node":true}'),
    (CATALOG, {}, b'{"project":{},"node":""}'),
    (CATALOG, {}, b'{"project":{},"node":"x","sidecars":{"external":[]}}'),
])
def test_strict_bounded_native_editing_framing_rejects_before_prepare(loopback, endpoint, headers, raw):
    code, reply, _ = loopback.request(endpoint, headers=headers, raw=raw)
    assert code == 400 and reply['code'] == 'request_invalid'
    assert not loopback.owned.exists()


@pytest.mark.parametrize('endpoint', [CATALOG, EDIT])
def test_duplicate_content_length_is_rejected_before_reading_body(loopback, endpoint):
    connection = HTTPConnection('127.0.0.1', loopback.port, timeout=5)
    try:
        connection.putrequest('POST', endpoint)
        connection.putheader('Origin', f'http://127.0.0.1:{loopback.port}')
        connection.putheader('Content-Type', 'application/json')
        connection.putheader('Content-Length', '2')
        connection.putheader('Content-Length', '2')
        connection.endheaders(b'{}')
        response = connection.getresponse()
        assert response.status == 400 and json.loads(response.read())['code'] == 'request_invalid'
    finally:
        connection.close()
    assert not loopback.owned.exists()


@pytest.mark.parametrize('endpoint', [CATALOG, EDIT])
def test_shader_or_unadmitted_motion_source_rejected_before_project_load(loopback, monkeypatch, endpoint):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('policy must reject before opening a native project or source file')
    monkeypatch.setattr(Project, 'load', forbidden)
    draft = loopback.draft()
    draft['effect_graph_controls'] = [{'node': 'fork-a', 'property': 'opacity', 'range': [0, 1],
        'motion': {'kind': 'recording', 'file': draft['layers'][0]['asset']}}]
    body = {'project': draft, 'node': 'fork-a'}
    if endpoint == EDIT:
        body['operation'] = {'kind': 'remove_binding', 'index': 0}
    code, reply, _ = loopback.request(endpoint, body)
    assert code == 400 and reply['code'] == 'draft_rejected'
    assert 'source.png' not in json.dumps(reply) and not loopback.owned.exists()


def test_native_rejection_and_busy_slot_release_keep_current_draft_intact(loopback):
    draft = loopback.draft()
    before = deepcopy(draft)
    body = {'project': draft, 'node': 'fork-a', 'operation': {
        'kind': 'set_parameter', 'property': 'opacity', 'value': 2}}
    code, reply, _ = loopback.request(EDIT, body)
    assert code == 400 and reply['code'] == 'edit_rejected'
    assert PRIVATE not in json.dumps(reply) and draft == before
    assert author.GRAPH_VALIDATION_SLOTS.acquire(blocking=False)
    try:
        code, reply, _ = loopback.request(CATALOG, {'project': draft, 'node': 'fork-a'})
        assert code == 429 and reply['code'] == 'busy'
    finally:
        author.GRAPH_VALIDATION_SLOTS.release()
    code, reply, _ = loopback.request(CATALOG, {'project': draft, 'node': 'fork-a'})
    assert code == 200 and reply['schema'] == CATALOG_SCHEMA
    assert not list(loopback.owned.iterdir())


def test_control_edit_is_admitted_by_validation_and_invalid_control_cannot_bypass_preview(loopback, monkeypatch):
    draft = loopback.draft()
    code, catalog, _ = loopback.request(CATALOG, {'project': draft, 'node': 'fork-a'})
    assert code == 200
    motion = next(row for row in catalog['motions'] if row['motion'] == {'kind': 'named', 'pattern': 'circle'})
    code, result, _ = loopback.request(EDIT, {'project': draft, 'node': 'fork-a', 'operation': {
        'kind': 'put_binding', 'index': None, 'property': 'opacity', 'motion_id': motion['id'],
        'settings': {'axis': 'x', 'range': [.2, .8], 'smoothing_frames': 2}}})
    assert code == 200 and result['validation']['valid']
    candidate = result['project']
    code, report, _ = loopback.request('/api/graph/validate', {'project': candidate})
    assert code == 200 and report['valid']
    candidate['effect_graph_controls'][0]['property'] = 'PRIVATE_INVALID_PROPERTY'
    def forbidden(*_args, **_kwargs):
        raise AssertionError('invalid control reached compositor')
    monkeypatch.setattr(Compositor, '__init__', forbidden)
    for endpoint in ('/api/preview', '/api/stream/start'):
        code, reply, _ = loopback.request(endpoint, {'project': candidate, 'frame': 3})
        assert code == 400 and 'invalid target' in reply['error']
    assert loopback.api.active is None and not loopback.cache.exists()


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('field,value', [('time_seconds', 4), ('time_delta_seconds', .2)])
def test_only_declared_numeric_gl_uniforms_are_parameter_edits(nested, field, value):
    transition = {'transition_style': 'gl_transition', 'shader_path': 'admitted.frag',
                  'shader_sha256': 'a' * 64, 'trusted_shader': True, 'frame_index': 30}
    node = ({'id': 'chain', 'type': 'transition_chain', 'inputs': ['frame', 'background'],
             'stages': [{'id': 'stage', **transition}]} if nested else
            {'id': 'transition', 'type': 'transition', 'inputs': ['frame', 'background'], **transition})
    original = {'schema': 'framerudiments.project.v1', 'effect_graph': {'nodes': [node], 'output': node['id']}}
    draft = deepcopy(original)
    target = draft['effect_graph']['nodes'][0]
    target = target['stages'][0] if nested else target
    target[field] = value
    author.check_draft(original, draft)
    target['frame_index'] = 99
    with pytest.raises(ValueError, match='read-only'):
        author.check_draft(original, draft)
