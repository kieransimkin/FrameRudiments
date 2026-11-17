"""S2 non-GPU regression: latest cursor wins, URI pins and loopback SSE."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Event, Thread
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json

from PIL import Image
import pytest

from framerudiments.preview_frame_cache import PreviewFrameCache
from framerudiments.preview_stream_contract import QualityProfile, Segment
from framerudiments.preview_stream_s2 import (
    PreviewStreamAPI, SeekStream, get_route, post_route,
)


def image(seed):
    file = BytesIO()
    Image.new('RGBA', (8, 8), (seed % 255, 50, 70, 255)).save(file, 'PNG')
    return file.getvalue()


def make_cache(path, n=1001):
    profiles = tuple(QualityProfile(f'Q{i}', i, '1/4' if i == 0 else '1',
                                    2 if i == 0 else 8, 2 if i == 0 else 8,
                                    1, 1, 'synthetic') for i in range(6))
    return PreviewFrameCache(path, project_fingerprint='a'*64,
                             pipeline_fingerprint='b'*64, renderer_abi='test-s2',
                             segments=(Segment('section-1', 0, n, 'a'*64),),
                             profiles=profiles, max_bytes=2**20, ram_bytes=512)


def wait_ready(stream, sequence):
    deadline = monotonic() + 4
    while monotonic() < deadline:
        found = [row for row in stream.since(0, timeout=.03)
                 if row['type'] == 'frame-ready' and row['cursor_seq'] == sequence]
        if found:
            return found[-1]
    raise AssertionError('stream did not publish latest frame')


def test_cache_hits_and_explicit_invalidation(tmp_path):
    calls = []
    stream = SeekStream(make_cache(tmp_path), lambda n: calls.append(n) or image(n), 1001)
    try:
        stream.command('seek', sequence=0, frame=4)
        row = wait_ready(stream, 0)
        assert row['frame'] == 4 and row['profile_id'] == 'Q0'
        assert row['production_equivalent'] is False
        assert stream.image(4, row['generation'], row['image_sha256'])
        stream.command('seek', sequence=1, frame=4)
        assert wait_ready(stream, 1)['cache_hit'] is True
        assert calls == [4]
        assert stream.command('seek', sequence=0, frame=3)['state'] == 'out_of_order'
        stream.cache.invalidate_all()
        assert stream.image(4, row['generation'], row['image_sha256']) is None
    finally:
        stream.close()


def test_thousand_seeks_coalesce_running_and_pending(tmp_path):
    started, release = Event(), Event()
    called = []
    def slow(n):
        called.append(n)
        if n == 1:
            started.set()
            assert release.wait(3)
        return image(n)
    stream = SeekStream(make_cache(tmp_path), slow, 1001)
    try:
        stream.command('seek', sequence=1, frame=1)
        assert started.wait(2)
        for seq in range(2, 1002):
            stream.command('seek', sequence=seq, frame=(seq*137) % 1001)
        release.set()
        assert wait_ready(stream, 1001)['frame'] == 0
        assert called == [1, 0]
        assert not any(e['type'] == 'frame-ready' and e['cursor_seq'] != 1001
                       for e in stream.since(0))
    finally:
        release.set()
        stream.close()


class TestProject:
    data = {'schema':'framerudiments.project.v1','title':'Synthetic'}
    inputs = []
    canonical_inputs = None
    frame_count, fps = 20, 30
    start_sample, end_sample, sample_rate, repeats = 0, 480000, 48000, 1
    width, height = 8, 8
    cues = []
    class output_quality:
        enabled, spatial_scale, temporal_samples = True, 4, 8
    def verify_inputs(self):
        return None


def test_loopback_sse_content_pin_origin_and_close(tmp_path):
    calls = []
    def render(_project, _draft, n, **_kwargs):
        calls.append(n)
        return image(n)
    api = PreviewStreamAPI(tmp_path/'project.json', lambda _orig, _draft:None,
                           render, cache_root=tmp_path/'cache',
                           project_loader=lambda _path:TestProject())
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def _origin_ok(self):
            return self.headers.get('Origin') == f'http://127.0.0.1:{self.server.server_port}'
        def _send(self, status, data, mime):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def _json(self, status, body):
            self._send(status, json.dumps(body).encode(), 'application/json')
        def do_GET(self):
            if not get_route(self):
                self._json(404, {})
        def do_POST(self):
            if not post_route(self):
                self._json(404, {})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.stream_api = api
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def post(path, body, *, origin=True):
        headers = {'Content-Type':'application/json'}
        if origin:
            headers['Origin'] = base
        request = Request(base+path, data=json.dumps(body).encode(), headers=headers, method='POST')
        with urlopen(request, timeout=6) as result:
            return json.load(result)
    try:
        with pytest.raises(HTTPError) as denied:
            post('/api/stream/start', {'project':TestProject.data}, origin=False)
        assert denied.value.code == 403
        opened = post('/api/stream/start', {'project':TestProject.data, 'frame':3})
        token = opened['session']
        frame_event = None
        last = 0
        for _ in range(5):
            with urlopen(base+'/api/stream/events?'+urlencode({'session':token,'after':last}), timeout=8) as r:
                payload = r.read().decode()
                assert r.headers['Content-Type'].startswith('text/event-stream')
            import re
            found = re.findall(r'(?m)^id: (\d+)$', payload)
            if found:
                last = int(found[-1])
            entries = re.findall(r'(?m)^data: ({.+})$', payload)
            frame_event = next((json.loads(x) for x in entries
                                if json.loads(x)['type']=='frame-ready'), None)
            if frame_event:
                break
        assert frame_event is not None
        with urlopen(base+frame_event['url'], timeout=6) as r:
            with Image.open(BytesIO(r.read())) as decoded:
                assert decoded.size == (2, 2)
        with pytest.raises(HTTPError) as expired:
            urlopen(base+frame_event['url'].replace(frame_event['image_sha256'],'0'*64), timeout=6)
        assert expired.value.code == 410
        assert post('/api/stream/close', {'session':token})['closed']
        assert calls
    finally:
        server.shutdown()
        server.server_close()
        api.shutdown()
        worker.join(timeout=2)
