"""S7 qualification through the real loopback handler, native stream and PNG cache.

Synthetic source files include private-looking names/text to catch accidental
receipt disclosure. They are not user media. No renderer or HTTP doubles.
"""
from copy import deepcopy
from hashlib import sha256
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from math import isfinite
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image
import pytest

from framerudiments.preview_stream_s2 import PreviewStreamAPI
from test_authoring_preview import author
from test_preview_native_project import fixture as native_project


PRIVATE = 'PRIVATE_S7_SOURCE_é_71357'
SCHEMA = 'framerudiments.preview-stream-qualification.v1'


def wait_ready(stream, sequence):
    """Wait on new events so the native owner can perform file I/O between polls."""
    deadline = monotonic() + 4
    event_cursor = 0
    observed = []
    while monotonic() < deadline:
        rows = stream.since(event_cursor, timeout=.05)
        for row in rows:
            event_cursor = max(event_cursor, row['id'])
            observed.append(row)
            if row['type'] == 'render-error':
                raise AssertionError(row)
            if row['type'] == 'frame-ready' and row['cursor_seq'] == sequence:
                return row
    raise AssertionError(('stream did not publish latest frame', observed))


@pytest.fixture
def loopback(tmp_path):
    directory = tmp_path / PRIVATE
    directory.mkdir()
    original, data = native_project(directory)
    cue = directory / 'private-cues-71357.lrc'
    cue.write_text(f'[00:00.250][SECTION: {PRIVATE}]\n', encoding='utf-8')
    data['title'] = PRIVATE
    data['clock']['authority'] = PRIVATE
    data['layers'][0]['id'] = PRIVATE
    data['cues'] = {'path': cue.name, 'sha256': sha256(cue.read_bytes()).hexdigest()}
    original.unlink()
    path = directory / 'private-project-71357.json'
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    pinned_bytes = {file: file.read_bytes() for file in directory.iterdir()}
    api = PreviewStreamAPI(path, author.check_draft, author.render_preview,
                           cache_root=tmp_path / 'cache')

    class Handler(author.AuthoringHandler):
        project_file = path
        session_data = {'project': data, 'audioPath': None}

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.stream_api = api
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'

    def request(endpoint, body=None, *, origin='same', raw=False):
        headers = {}
        if origin is not None:
            headers['Origin'] = base if origin == 'same' else origin
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = Request(base + endpoint, data=None if body is None else json.dumps(body).encode(),
                      headers=headers, method='GET' if body is None else 'POST')
        try:
            response = urlopen(req, timeout=6)
        except HTTPError as exc:
            response = exc
        with response:
            payload = response.read()
            return response.status, payload if raw else json.loads(payload), response.headers

    def open_stream(**overrides):
        body = {'project': deepcopy(data), 'adaptive': True, 'refine': False,
                'frame': 3, 'target_fps': 24,
                'sidecars': {'cues': [{'start': .5, 'label': f'[SECTION: {PRIVATE}_EDITED]'}]},
                **overrides}
        deadline = monotonic() + 3
        while True:
            code, reply, _ = request('/api/stream/start', body)
            if code == 200:
                stream = api.get(reply['session'])
                wait_ready(stream, 0)
                return reply
            if 'retiring' not in reply.get('error', '') or monotonic() >= deadline:
                raise AssertionError((code, reply))
            sleep(.005)

    def export(token, **kwargs):
        return request('/api/stream/qualification?' + urlencode({'session': token}), **kwargs)

    try:
        yield SimpleNamespace(api=api, request=request, open=open_stream, export=export,
                              path=path, data=data, private_directory=directory)
    finally:
        server.shutdown()
        server.server_close()
        api.shutdown()
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert {file: file.read_bytes() for file in directory.iterdir()} == pinned_bytes


def assert_finite_json(value):
    if isinstance(value, dict):
        for child in value.values():
            assert_finite_json(child)
    elif isinstance(value, list):
        for child in value:
            assert_finite_json(child)
    elif isinstance(value, float):
        assert isfinite(value)


def test_export_matches_live_native_session_without_disclosing_content(loopback):
    opened = loopback.open()
    token = opened['session']
    code, envelope, headers = loopback.export(token)
    assert code == 200
    assert headers['Cache-Control'] == 'no-store'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert 'Access-Control-Allow-Origin' not in headers
    assert set(envelope) == {'session', 'cursor_seq', 'frame', 'qualification'}
    assert envelope['session'] == token and envelope['cursor_seq'] == 0 and envelope['frame'] == 3
    receipt = envelope['qualification']
    assert receipt['schema'] == SCHEMA
    assert receipt['frame_count'] == 120 and receipt['target_fps'] == 24
    assert receipt['rates']['acknowledgement_source'] == 'client_decode_ack'
    assert receipt['cache']['frames'] == receipt['cache']['q5_frames'] == 1
    assert receipt['identity'] and receipt['versions'] and receipt['segments']
    assert receipt['resources']['scope'] == 'preview_server_process'
    assert receipt['resources']['gpu_memory_bytes'] is None
    assert_finite_json(receipt)
    text = json.dumps(receipt, ensure_ascii=False, allow_nan=False)
    for private in (PRIVATE, token, str(loopback.private_directory), loopback.path.name,
                    'private-cues-71357.lrc', 'source.wav', 'source.png'):
        assert private not in text
    assert len(text.encode('utf-8')) < 1024 * 1024


@pytest.mark.parametrize('second_rank', [0, 5])
def test_status_has_summary_and_export_observes_current_cursor_and_target(loopback, second_rank):
    opened = loopback.open()
    token = opened['session']
    _, first, _ = loopback.export(token)
    code, status, _ = loopback.request('/api/stream/status?' + urlencode({'session': token}))
    assert code == 200 and status['qualification']['schema'] == SCHEMA
    assert not {'identity', 'segments', 'transitions', 'invalidations'} & set(status['qualification'])
    # Native service time may have selected a lower adaptive tier. Exercise
    # both real plans deterministically; Q5 coverage must follow served pixels.
    stream = loopback.api.get(token)
    with stream.condition:
        stream.controller.rank = second_rank
    code, _, _ = loopback.request('/api/stream/control', {
        'session': token, 'command': 'target_fps', 'sequence': 1, 'frame': 17, 'target_fps': 57})
    assert code == 200
    ready = wait_ready(stream, 1)
    assert ready['quality_rank'] == second_rank
    code, latest, _ = loopback.export(token)
    assert code == 200 and latest['frame'] == 17 and latest['cursor_seq'] == 1
    assert latest['qualification']['target_fps'] == 57
    assert latest['qualification']['sample_sequence'] >= first['qualification']['sample_sequence']
    assert first['qualification']['cache']['frames'] == 1
    assert latest['qualification']['cache']['frames'] == 2
    expected_q5 = first['qualification']['cache']['q5_frames'] + (ready['quality_rank'] == 5)
    assert latest['qualification']['cache']['q5_frames'] == expected_q5


def test_export_keeps_browser_ack_evidence_distinct_from_client_injected_labels(loopback):
    token = loopback.open()['session']
    ready = wait_ready(loopback.api.get(token), 0)
    code, png, _ = loopback.request(ready['url'], raw=True)
    assert code == 200
    with Image.open(BytesIO(png)) as image:
        image.load()
        assert image.mode == 'RGBA' and image.size == (64, 48)
    acknowledgment = {'session': token, 'sequence': ready['cursor_seq'],
                      'frame': ready['frame'], 'sha256': ready['image_sha256']}
    code, reply, _ = loopback.request('/api/stream/presented', {
        **acknowledgment, 'acknowledgement_source': 'harness_png_decode'})
    assert code == 400 and not reply.get('acknowledged')
    code, reply, _ = loopback.request('/api/stream/presented', acknowledgment)
    assert code == 200 and reply['acknowledged']
    code, envelope, _ = loopback.export(token)
    assert code == 200
    assert envelope['qualification']['rates']['acknowledgement_source'] == 'client_decode_ack'


def test_export_uses_existing_same_origin_get_policy(loopback):
    token = loopback.open()['session']
    assert loopback.export(token, origin=None)[0] == 200
    assert loopback.export(token, origin='https://untrusted.example')[0] == 403
    assert loopback.export(token, origin='null')[0] == 403


@pytest.mark.parametrize('suffix', [
    '', '&session=', '&session=another-token', '&unexpected=1', '&unexpected=',
    '&after=0', '&continuous=1', '&frame=0', '&' + 'x' * 600 + '=1',
])
def test_export_rejects_missing_repeated_and_extra_query_fields(loopback, suffix):
    token = loopback.open()['session']
    query = '' if suffix == '' else urlencode({'session': token}) + suffix
    code, _, _ = loopback.request('/api/stream/qualification?' + query)
    assert code == 400


def test_replaced_closed_and_foreign_sessions_cannot_export(loopback):
    first = loopback.open()['session']
    assert loopback.export('foreign-token')[0] == 400
    second = loopback.open(frame=7)['session']
    assert second != first
    assert loopback.export(first)[0] == 400
    assert loopback.export(second)[0] == 200
    code, reply, _ = loopback.request('/api/stream/close', {'session': second})
    assert code == 200 and reply['closed']
    assert loopback.export(second)[0] == 400


@pytest.mark.parametrize('injected', [
    {'acknowledgement_source': 'harness_png_decode'},
    {'cache_limits': {'max_bytes': 1}},
    {'qualification': {'approved': True}},
    {'metrics': {'fresh_fps': 120}},
])
def test_http_client_cannot_inject_profiler_authority_or_measurements(loopback, injected):
    token = loopback.open()['session']
    code, _, _ = loopback.request('/api/stream/start', {'project': loopback.data, **injected})
    assert code == 400
    code, _, _ = loopback.request('/api/stream/control', {
        'session': token, 'command': 'seek', 'sequence': 1, 'frame': 3, **injected})
    assert code == 400
    code, current, _ = loopback.export(token)
    assert code == 200 and current['cursor_seq'] == 0


def test_export_filters_untrusted_version_and_diagnostic_text(loopback, monkeypatch):
    import framerudiments.preview_qualification as qualification

    monkeypatch.setattr(qualification, 'version', lambda _name: PRIVATE + '/private-version')
    token = loopback.open()['session']
    stream = loopback.api.get(token)
    # An internal producer's diagnostic must be projected through the same
    # allowlist as ordinary cache events before entering the exported receipt.
    with stream.condition:
        stream._event('cache-invalidated', invalidation={
            'cause': PRIVATE, 'affected_sections': [{'id': PRIVATE}],
            'invalidated_frames': 1, 'invalidated_bytes': 0, 'revision': 2,
            'private_path': str(loopback.path), 'message': PRIVATE})
        stream._event('quality-changed', from_profile='Q4', to_profile='Q5', reason=PRIVATE)
    code, envelope, _ = loopback.export(token)
    assert code == 200
    receipt = envelope['qualification']
    assert PRIVATE not in json.dumps(receipt, ensure_ascii=False)
    assert receipt['invalidations'][-1]['cause'] == 'unknown'
    assert receipt['invalidations'][-1]['affected_sections'] == 1
    assert all(value is None for key, value in receipt['versions'].items() if key != 'python')
