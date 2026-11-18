"""M10.4 actual loopback transport, shared bounds and source admission."""
from copy import deepcopy
from http.client import HTTPConnection
import json

import pytest

from framerudiments.compositor import Compositor
from framerudiments.graph_authoring_preview import SCHEMA
from framerudiments.preview_stream_contract import canonical_digest
from framerudiments.project import Project
from test_authoring_graph_http import PRIVATE, loopback as native_loopback
from test_authoring_preview import author
from test_graph_authoring_preview import pixels, reference


PREVIEW = '/api/graph/preview'
loopback = native_loopback


def payload(loopback, *, frame=17, quality='fast'):
    draft = loopback.draft()
    draft['effect_graph']['nodes'].append({'id': 'mix', 'type': 'transition',
        'inputs': ['fork-a', 'background'], 'transition_style': 'dissolve', 'progress': .4})
    return {'project': draft, 'sidecars': {}, 'node': 'mix', 'frame': frame, 'quality': quality}


@pytest.mark.parametrize('quality', ['fast', 'full'])
def test_transition_http_returns_exact_native_comparison_and_draft_identity(loopback, quality):
    body = payload(loopback, quality=quality)
    before = deepcopy(body)
    code, result, headers = loopback.request(PREVIEW, body)
    assert code == 200 and result['schema'] == SCHEMA
    assert result['request_sha256'] == canonical_digest({'project': body['project'], 'sidecars': {}})
    assert result['node']['id'] == 'mix' and result['frame'] == 17 and result['quality'] == quality
    for row, target in zip(result['images'], ('fork-a', 'mix', 'background')):
        assert pixels(row) == reference(loopback.path, body['project'], target, 17, quality)[0]
    assert headers['Cache-Control'] == 'no-store' and headers['X-Content-Type-Options'] == 'nosniff'
    assert 'Access-Control-Allow-Origin' not in headers
    assert PRIVATE not in json.dumps(result) and str(loopback.directory) not in json.dumps(result)
    assert body == before and not list(loopback.owned.iterdir())
    assert loopback.api.active is None and not loopback.cache.exists()


@pytest.mark.parametrize('origin', [None, 'https://foreign.example', 'http://127.0.0.1:1'])
def test_transition_preview_requires_same_origin_before_native_prepare(loopback, origin):
    code, result, _ = loopback.request(PREVIEW, payload(loopback), origin=origin)
    assert code == 403 and result['code'] == 'request_invalid'
    assert not loopback.owned.exists()


@pytest.mark.parametrize('endpoint,headers,raw', [
    (PREVIEW+'?frame=0', {}, b'{}'),
    (PREVIEW, {'Content-Type': 'text/plain'}, b'{}'),
    (PREVIEW, {'Content-Length': str(author.MAX_BODY+1)}, b'{}'),
    (PREVIEW, {'Transfer-Encoding': 'chunked'}, b'0\r\n\r\n'),
    (PREVIEW, {}, b'{broken'),
    (PREVIEW, {}, b'{"project":{},"node":"mix","frame":NaN,"quality":"fast"}'),
    (PREVIEW, {}, b'{"project":{},"node":"mix","frame":true,"quality":"fast"}'),
    (PREVIEW, {}, b'{"project":{},"node":"mix","frame":1.2,"quality":"fast"}'),
    (PREVIEW, {}, b'{"project":{},"node":"mix","frame":-1,"quality":"fast"}'),
    (PREVIEW, {}, b'{"project":{},"node":"mix","frame":0}'),
    (PREVIEW, {}, b'{"project":{},"node":"mix","frame":0,"quality":"full","progress":0}'),
    (PREVIEW, {}, b'{"project":{},"node":null,"frame":0,"quality":"fast"}'),
    (PREVIEW, {}, b'{"project":{},"node":"mix","frame":0,"quality":"fast","sidecars":{"external":[]}}'),
])
def test_transition_request_framing_is_strict_before_preparation(loopback, endpoint, headers, raw):
    code, result, _ = loopback.request(endpoint, headers=headers, raw=raw)
    assert code == 400 and result['code'] == 'request_invalid'
    assert not loopback.owned.exists()


def test_duplicate_content_length_rejected_before_reading_body(loopback):
    connection = HTTPConnection('127.0.0.1', loopback.port, timeout=5)
    try:
        connection.putrequest('POST', PREVIEW)
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


def test_changed_pin_and_shader_authority_are_rejected_before_native_project_open(loopback, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('source policy must precede native project loading')
    monkeypatch.setattr(Project, 'load', forbidden)
    body = payload(loopback)
    body['project']['audio']['source']['sha256'] = 'b'*64
    code, result, _ = loopback.request(PREVIEW, body)
    assert code == 400 and result['code'] == 'draft_rejected'
    body = payload(loopback)
    body['project']['effect_graph']['nodes'][-1].update(
        transition_style='gl_transition', shader_path='/PRIVATE_SHADER', shader_sha256='a'*64, trusted_shader=True)
    code, result, _ = loopback.request(PREVIEW, body)
    assert code == 400 and result['code'] == 'draft_rejected' and 'PRIVATE' not in json.dumps(result)
    assert not loopback.owned.exists()


def test_disconnected_invalid_branch_cannot_bypass_native_preview(loopback, monkeypatch):
    body = payload(loopback)
    body['project']['effect_graph']['nodes'].append({'id': 'hidden', 'type': 'copy', 'inputs': ['missing']})
    def forbidden(*_args, **_kwargs):
        raise AssertionError('invalid disconnected graph reached rendering')
    monkeypatch.setattr(Compositor, '__init__', forbidden)
    code, result, _ = loopback.request(PREVIEW, body)
    assert code == 400 and result['code'] == 'graph_invalid'
    assert not list(loopback.owned.iterdir())


def test_shared_preview_slot_busy_and_failure_release_are_explicit(loopback):
    assert author.PREVIEW_SLOTS.acquire(blocking=False)
    assert author.PREVIEW_SLOTS.acquire(blocking=False)
    try:
        code, result, _ = loopback.request(PREVIEW, payload(loopback))
        assert code == 429 and result['code'] == 'busy'
    finally:
        author.PREVIEW_SLOTS.release()
        author.PREVIEW_SLOTS.release()
    body = payload(loopback)
    body['node'] = 'frame'
    code, result, _ = loopback.request(PREVIEW, body)
    assert code == 400 and result['code'] == 'node_invalid'
    code, result, _ = loopback.request(PREVIEW, payload(loopback))
    assert code == 200 and result['schema'] == SCHEMA
    assert not list(loopback.owned.iterdir())


@pytest.mark.parametrize('endpoint', [PREVIEW, '/api/preview'])
def test_shared_native_size_bound_rejects_before_rendering(loopback, monkeypatch, endpoint):
    import framerudiments.graph_authoring_preview as preview
    monkeypatch.setattr(preview, 'MAX_PREVIEW_PIXELS', 1)
    def forbidden(*_args, **_kwargs):
        raise AssertionError('oversized native viewport reached compositor')
    monkeypatch.setattr(Compositor, '__init__', forbidden)
    body = payload(loopback)
    if endpoint == '/api/preview':
        body.pop('node')
    code, result, _ = loopback.request(endpoint, body)
    assert code == 400 and result['error'] == str(preview.AuthoringPreviewError('preview_limit'))
    assert result['code'] == 'preview_limit'


@pytest.mark.parametrize('endpoint', [PREVIEW, '/api/preview'])
def test_shared_encoded_png_bound_closes_resources_and_returns_fixed_error(loopback, monkeypatch, endpoint):
    import framerudiments.graph_authoring_preview as preview
    monkeypatch.setattr(preview, 'MAX_IMAGE_BYTES', 1)
    body = payload(loopback)
    if endpoint == '/api/preview':
        body.pop('node')
    code, result, _ = loopback.request(endpoint, body)
    assert code == 400 and result['error'] == str(preview.AuthoringPreviewError('preview_limit'))
    assert result['code'] == 'preview_limit'
    assert not loopback.owned.exists() or not list(loopback.owned.iterdir())
