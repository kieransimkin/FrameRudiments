"""M10.2 real HTTP/native boundary, source-policy and preview-bypass coverage."""
from copy import deepcopy
from functools import partial
from hashlib import sha256
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from threading import Thread
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.compositor import Compositor
from framerudiments.graph_authoring import SCHEMA
from framerudiments.preview_stream_contract import canonical_digest
from framerudiments.preview_stream_s2 import PreviewStreamAPI
from framerudiments.project import Project
from test_authoring_preview import author
from test_preview_native_project import fixture as native_project
from test_preview_qualification_http import wait_ready


PRIVATE = 'PRIVATE_M10_2_SOURCE_AND_TEXT'
VALIDATE = '/api/graph/validate'


@pytest.fixture
def loopback(tmp_path):
    directory = tmp_path / PRIVATE
    directory.mkdir()
    path, data = native_project(directory)
    cue = directory / 'private-cues.lrc'
    cue.write_text(f'[00:00.250][SECTION: {PRIVATE}]\n', encoding='utf-8')
    data['title'] = PRIVATE
    data['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    path.write_text(json.dumps(data), encoding='utf-8')
    pinned = {item: item.read_bytes() for item in directory.iterdir()}
    owned = tmp_path / 'graph-validation'
    cache = tmp_path / 'stream-cache'
    api = PreviewStreamAPI(path, author.check_draft, author.render_preview, cache_root=cache,
        graph_validator=partial(author.validate_preview_graph, temp_parent=owned))

    class Handler(author.AuthoringHandler):
        project_file = path
        session_data = {'project': data, 'audioPath': None}

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.stream_api = api
    server.graph_temp_parent = owned
    worker = Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'

    def request(endpoint, body=None, *, origin='same', headers=None, raw=None, method='POST', binary=False):
        payload = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        fields = {'Content-Type': 'application/json'}
        if origin is not None:
            fields['Origin'] = base if origin == 'same' else origin
        fields.update(headers or {})
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request(method, endpoint, body=payload if method == 'POST' else None, headers=fields)
            response = connection.getresponse()
            result = response.read()
            return response.status, result if binary else json.loads(result), dict(response.getheaders())
        finally:
            connection.close()

    def draft():
        result = deepcopy(data)
        result['effect_graph'] = {'nodes': [
            {'id': 'frame', 'type': 'source'}, {'id': 'background', 'type': 'source'},
            {'id': 'fork-a', 'type': 'opacity', 'inputs': ['frame'], 'opacity': .4},
            {'id': 'fork-b', 'type': 'copy', 'inputs': ['frame']},
            {'id': 'out', 'type': 'composite', 'inputs': ['fork-a', 'fork-b']}], 'output': 'out'}
        return result

    try:
        yield SimpleNamespace(request=request, draft=draft, data=data, path=path, api=api,
                              owned=owned, cache=cache, directory=directory)
    finally:
        server.shutdown()
        server.server_close()
        api.shutdown()
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert {item: item.read_bytes() for item in directory.iterdir()} == pinned
        assert not owned.exists() or not list(owned.iterdir())


def test_native_http_report_binds_exact_snapshot_and_keeps_private_inputs_local(loopback):
    draft = loopback.draft()
    sidecars = {'cues': [{'start': .75, 'label': f'[SECTION: {PRIVATE} EDITED]'}]}
    code, reply, headers = loopback.request(VALIDATE, {'project': draft, 'sidecars': sidecars})
    assert code == 200 and reply['valid'] and reply['schema'] == SCHEMA
    assert reply['graph_present'] and reply['errors'] == []
    assert reply['request_sha256'] == canonical_digest({'project': draft, 'sidecars': sidecars})
    assert reply['graph_sha256'] == canonical_digest({'effect_graph': draft['effect_graph'],
                                                     'effect_graph_controls': []})
    assert len(reply['nodes']) == 5
    assert headers['Cache-Control'] == 'no-store' and headers['X-Content-Type-Options'] == 'nosniff'
    assert 'Access-Control-Allow-Origin' not in headers
    encoded = json.dumps(reply)
    for private in (PRIVATE, str(loopback.directory), 'source.wav', 'private-cues.lrc'):
        assert private not in encoded
    draft['effect_graph']['nodes'][-1]['inputs'].reverse()
    _, changed, _ = loopback.request(VALIDATE, {'project': draft, 'sidecars': sidecars})
    assert changed['valid'] and changed['graph_sha256'] != reply['graph_sha256']
    assert changed['request_sha256'] != reply['request_sha256']


@pytest.mark.parametrize('origin', [None, 'https://foreign.example', 'http://127.0.0.1:1'])
def test_graph_validation_requires_exact_same_origin(loopback, origin):
    code, reply, _ = loopback.request(VALIDATE, {'project': loopback.draft()}, origin=origin)
    assert code == 403 and reply['code'] == 'request_invalid'
    assert not loopback.owned.exists()


@pytest.mark.parametrize('endpoint,headers,raw', [
    (VALIDATE + '?frame=1', {}, b'{}'),
    (VALIDATE, {'Content-Type': 'text/plain'}, b'{}'),
    (VALIDATE, {'Content-Length': str(author.MAX_BODY + 1)}, b'{}'),
    (VALIDATE, {'Content-Length': '0'}, b''),
    (VALIDATE, {'Transfer-Encoding': 'chunked'}, b'0\r\n\r\n'),
    (VALIDATE, {}, b'{broken'),
    (VALIDATE, {}, b'{"project":{"value":NaN}}'),
    (VALIDATE, {}, b'[]'),
    (VALIDATE, {}, b'{"project":{},"extra":true}'),
    (VALIDATE, {}, b'{"project":{},"sidecars":null}'),
    (VALIDATE, {}, b'{"project":{},"sidecars":{"external":[]}}'),
])
def test_bounded_request_shape_and_finite_json_rejected_before_native_prepare(loopback, endpoint, headers, raw):
    code, reply, _ = loopback.request(endpoint, headers=headers, raw=raw)
    assert code == 400 and reply == {'error': 'Invalid graph validation request', 'code': 'request_invalid'}
    assert not loopback.owned.exists()


@pytest.mark.parametrize('field,value', [
    ('audio', {'source': {'path': '/private/unpinned.wav', 'sha256': 'a' * 64}}),
    ('clock', {'bpm': 1, 'authority': 'changed', 'status': 'confirmed'}),
    ('effect_graph_controls', []), ('effect_graph_backend', 'gpu'),
])
def test_source_clocks_controls_and_backend_remain_readonly(loopback, field, value):
    draft = loopback.draft()
    draft[field] = value
    code, reply, _ = loopback.request(VALIDATE, {'project': draft})
    assert code == 400 and reply['code'] == 'draft_rejected'
    assert 'private' not in reply['error'].lower()
    assert not loopback.owned.exists()


def test_shader_admission_rejected_before_any_native_project_read(loopback, monkeypatch):
    draft = loopback.draft()
    draft['effect_graph']['nodes'].append({
        'id': 'untrusted', 'type': 'transition', 'inputs': ['frame', 'background'],
        'transition_style': 'gl_transition', 'shader_path': '/private/secret.frag',
        'shader_sha256': '1' * 64, 'trusted_shader': True})
    def forbidden(*_args, **_kwargs):
        raise AssertionError('request policy must reject before Project.load')
    monkeypatch.setattr(Project, 'load', forbidden)
    code, reply, _ = loopback.request(VALIDATE, {'project': draft})
    assert code == 400 and reply['code'] == 'draft_rejected'
    assert 'secret' not in json.dumps(reply)
    assert not loopback.owned.exists()


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('field,value', [
    ('shader_path', '/private/other.frag'), ('shader_sha256', 'b' * 64), ('trusted_shader', True),
    ('shader_passes', [{'shader_path': '/private/pass.frag', 'shader_sha256': 'c' * 64}]),
    ('max_pass_bytes', 4096), ('frame_index', 3), ('time_seconds', 4), ('time_delta_seconds', .2),
])
def test_existing_shader_authority_is_readonly_in_nodes_and_chain_stages(nested, field, value):
    transition = {'transition_style': 'gl_transition', 'shader_path': 'admitted.frag',
                  'shader_sha256': 'a' * 64, 'trusted_shader': False}
    node = ({'id': 'chain', 'type': 'transition_chain', 'inputs': ['frame', 'background'],
             'stages': [{'id': 'stage', **transition}]} if nested else
            {'id': 'transition', 'type': 'transition', 'inputs': ['frame', 'background'], **transition})
    original = {'schema': 'framerudiments.project.v1', 'effect_graph': {'nodes': [node], 'output': node['id']}}
    draft = deepcopy(original)
    changed = draft['effect_graph']['nodes'][0]
    changed['inputs'].reverse()
    author.check_draft(original, draft)  # Connection edits preserve admitted shader authority.
    target = changed['stages'][0] if nested else changed
    target[field] = value
    with pytest.raises(ValueError, match='read-only'):
        author.check_draft(original, draft)


def test_semantic_failure_and_busy_are_explicit_safe_responses(loopback):
    draft = loopback.draft()
    draft['effect_graph']['nodes'].append({'id': 'private-wrong-kind', 'type': 'unsupported'})
    code, reply, _ = loopback.request(VALIDATE, {'project': draft})
    assert code == 200 and reply['valid'] is False
    assert reply['errors'][0]['code'] == 'graph_schema'
    assert 'private-wrong-kind' not in json.dumps(reply)
    assert author.GRAPH_VALIDATION_SLOTS.acquire(blocking=False)
    try:
        code, reply, _ = loopback.request(VALIDATE, {'project': loopback.draft()})
        assert code == 429 and reply['code'] == 'busy'
    finally:
        author.GRAPH_VALIDATION_SLOTS.release()


@pytest.mark.parametrize('captions', [True, [], 'PRIVATE INVALID CAPTIONS'])
def test_malformed_native_project_fields_have_controlled_semantic_errors(loopback, captions):
    draft = loopback.draft()
    draft['captions'] = captions
    code, reply, _ = loopback.request(VALIDATE, {'project': draft})
    assert code == 200 and reply['valid'] is False
    assert reply['errors'][0]['code'] == 'project_invalid'
    assert 'PRIVATE' not in json.dumps(reply)


@pytest.mark.parametrize('endpoint', ['/api/preview', '/api/stream/start'])
def test_preview_cannot_bypass_disconnected_graph_semantics(loopback, monkeypatch, endpoint):
    draft = loopback.draft()
    draft['effect_graph']['nodes'] += [
        {'id': 'hidden-a', 'type': 'copy', 'inputs': ['hidden-b']},
        {'id': 'hidden-b', 'type': 'copy', 'inputs': ['hidden-a']}]
    def forbidden(*_args, **_kwargs):
        raise AssertionError('invalid graph reached rendering')
    monkeypatch.setattr(Compositor, '__init__', forbidden)
    body = {'project': draft, 'frame': 3}
    if endpoint.endswith('/start'):
        body.update(adaptive=True, refine=False)
    code, reply, _ = loopback.request(endpoint, body)
    assert code == 400 and 'free of cycles' in reply['error']
    assert loopback.api.active is None and not loopback.cache.exists()


def test_same_graph_renders_native_pixels_through_one_shot_and_stream(loopback, monkeypatch):
    draft = loopback.draft()
    expected = Compositor(Project(deepcopy(draft), loopback.path.parent))
    try:
        reference, _ = expected.frame(3, apply_quality=False)
    finally:
        expected.close()
    closes = []
    original_close = Compositor.close
    def close(instance):
        closes.append(instance)
        return original_close(instance)
    monkeypatch.setattr(Compositor, 'close', close)
    code, png, _ = loopback.request('/api/preview', {'project': draft, 'frame': 3}, binary=True)
    assert code == 200 and closes
    with Image.open(BytesIO(png)) as image:
        assert image.convert('RGB').tobytes() == reference.convert('RGB').tobytes()
    code, reply, _ = loopback.request('/api/stream/start', {
        'project': draft, 'frame': 3, 'adaptive': True, 'refine': False, 'target_fps': 24})
    assert code == 200, reply
    ready = wait_ready(loopback.api.get(reply['session']), 0)
    # Fetch the exact server-issued immutable image URL, then decode the wire PNG.
    code, png, _ = loopback.request(ready['url'], method='GET', binary=True)
    assert code == 200
    with Image.open(BytesIO(png)) as image:
        image.load()
        assert image.mode == 'RGBA' and image.width > 0 and image.height > 0


def test_graph_free_one_shot_preserves_native_preview_without_graph_catalogue_limits(loopback, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('graph-free preview must not apply graph-specific source catalogue limits')
    monkeypatch.setattr('framerudiments.graph_authoring.validate_authoring_graph', forbidden)
    reference = Compositor(Project(deepcopy(loopback.data), loopback.path.parent))
    try:
        expected, _ = reference.frame(3, apply_quality=False)
    finally:
        reference.close()
    code, png, _ = loopback.request('/api/preview', {'project': loopback.data, 'frame': 3}, binary=True)
    assert code == 200
    with Image.open(BytesIO(png)) as image:
        assert image.convert('RGB').tobytes() == expected.convert('RGB').tobytes()
