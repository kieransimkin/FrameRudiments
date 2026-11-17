from io import BytesIO
from threading import Event, Thread
from time import monotonic

from PIL import Image
import pytest

from framerudiments.preview_frame_cache import PreviewFrameCache
from framerudiments.preview_quality import profile_capabilities
from framerudiments.preview_stream_contract import quality_profiles, Segment
from framerudiments.preview_stream_s2 import PreviewStreamAPI, SeekStream
from test_preview_adaptive import Clock, project


def png(size, n=0):
    output = BytesIO()
    Image.new('RGBA', size, (n % 255, 20, 90, 255)).save(output, 'PNG')
    return output.getvalue()


def store(path):
    p = project()
    return PreviewFrameCache(path, project_fingerprint='a'*64, pipeline_fingerprint='b'*64,
                             renderer_abi='test-s3', segments=(Segment('whole', 0, 1001, 'a'*64),),
                             profiles=quality_profiles(p), max_bytes=8*1024**2, ram_bytes=512), p


def ready(stream, sequence):
    deadline = monotonic() + 5
    last_event = 0
    while monotonic() < deadline:
        events = stream.since(last_event, timeout=.05)
        if events:
            last_event = max(row["id"] for row in events)
        matching = [e for e in events if e['cursor_seq'] == sequence]
        errors = [e for e in matching if e['type'] == 'render-error']
        assert not errors, errors
        rows = [e for e in matching if e['type'] == 'frame-ready']
        if rows:
            return rows[-1]
    raise AssertionError('frame not ready')


def test_stream_adapts_actual_native_dimensions_and_keeps_highest_cached_frame(tmp_path):
    cache, p = store(tmp_path)
    clock = Clock()
    calls = []
    def render(n, tier):
        calls.append((n, tier))
        clock.advance(70)
        profile = cache.profiles[int(tier[1:])]
        return png((profile.output_width, profile.output_height), n)
    stream = SeekStream(cache, render, 1001, 60, capabilities=profile_capabilities(p), clock=clock)
    try:
        for i in range(180):
            stream.command('advance', sequence=i, frame=i)
            row = ready(stream, i)
            selected = cache.profiles[row['quality_rank']]
            with Image.open(BytesIO(stream.image(i, row['generation'], row['image_sha256']))) as decoded:
                assert decoded.size == (selected.output_width, selected.output_height)
        assert stream.controller.rank == 0
        assert {'Q0', 'Q1', 'Q2', 'Q3', 'Q4', 'Q5'} <= {tier for _, tier in calls}
        assert cache.get(0).profile_id == 'Q5'
        count = len(calls)
        stream.command('seek', sequence=180, frame=0)
        hit = ready(stream, 180)
        assert hit['cache_hit'] and hit['profile_id'] == 'Q5' and len(calls) == count
        assert stream.acknowledge(180, 0, hit['image_sha256'])
        assert not stream.acknowledge(180, 0, hit['image_sha256'])
        assert not stream.acknowledge(179, 0, hit['image_sha256'])
        assert stream.controller.snapshot()['samples'] == 0  # seek + hit do not train
    finally:
        stream.close()


def test_wrong_native_dimensions_cannot_be_admitted_as_high_quality(tmp_path):
    cache, p = store(tmp_path)
    stream = SeekStream(cache, lambda n, tier: png((1, 1)), 1001, capabilities=profile_capabilities(p))
    try:
        stream.command('seek', sequence=0, frame=4)
        deadline = monotonic() + 3
        while monotonic() < deadline and not any(e['type'] == 'render-error' for e in stream.since(0, timeout=.02)):
            pass
        assert cache.get(4) is None
        assert stream.controller.fresh_total == 0
    finally:
        stream.close()


def test_invalidation_during_render_fences_publish_and_cached_delivery(tmp_path):
    cache, p = store(tmp_path)
    started, release = Event(), Event()
    changed = [False]
    def verify():
        if changed[0]:
            raise ValueError('source changed')
    def render(n, tier):
        started.set()
        assert release.wait(3)
        return png((640, 360), n)
    stream = SeekStream(cache, render, 1001, capabilities=profile_capabilities(p), verifier=verify)
    try:
        stream.command('seek', sequence=0, frame=4)
        assert started.wait(2)
        changed[0] = True
        release.set()
        deadline = monotonic() + 3
        while monotonic() < deadline and not any(e['type'] == 'render-error' for e in stream.since(0, timeout=.02)):
            pass
        assert cache.get(4) is None
        with pytest.raises(ValueError, match='Pinned source changed'):
            stream.command('seek', sequence=1, frame=4)
    finally:
        release.set()
        stream.close()


def test_context_cleanup_runs_on_worker_and_closed_frame_is_never_published(tmp_path):
    from threading import get_ident
    cache, p = store(tmp_path)
    entered, release = Event(), Event()
    ids = []
    class Renderer:
        def __call__(self, n, tier):
            ids.append(get_ident())
            entered.set()
            assert release.wait(3)
            return png((640, 360), n)
        def close(self):
            ids.append(get_ident())
    stream = SeekStream(cache, Renderer(), 1001, capabilities=profile_capabilities(p))
    stream.command('seek', sequence=0, frame=4)
    assert entered.wait(2)
    stream.close()
    release.set()
    stream.worker.join(3)
    assert ids[0] == ids[1] != get_ident()
    assert cache.get(4) is None


def test_rapid_adaptive_seek_series_discards_outdated_samples(tmp_path):
    cache, p = store(tmp_path)
    started, release = Event(), Event()
    calls = []
    def render(n, tier):
        calls.append(n)
        if n == 1:
            started.set()
            assert release.wait(3)
        return png((640, 360), n)
    stream = SeekStream(cache, render, 1001, capabilities=profile_capabilities(p))
    try:
        stream.command('seek', sequence=1, frame=1)
        assert started.wait(2)
        for i in range(2, 1002):
            stream.command('seek', sequence=i, frame=(i*137) % 1001)
        release.set()
        assert ready(stream, 1001)['frame'] == 0
        assert calls == [1, 0]
        assert stream.controller.snapshot()['samples'] == 1
        assert cache.get(1) is None
    finally:
        release.set()
        stream.close()


def test_continuous_sse_acks_and_retiring_sessions_remain_bounded(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    from urllib.request import Request, urlopen
    from framerudiments.preview_stream_s2 import get_route, post_route
    from test_preview_stream_s2 import TestProject

    def factory(_path, _draft, _sidecars, **_kwargs):
        return lambda n, tier: png((8, 8), n)
    api = PreviewStreamAPI(tmp_path/'project.json', lambda *_: None, lambda *_: None,
                           project_loader=lambda _: TestProject(), cache_root=tmp_path/'cache',
                           adaptive_renderer_factory=factory)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def _origin_ok(self):
            return self.headers.get('Origin') == f'http://127.0.0.1:{self.server.server_port}'
        def _send(self, code, data, mime):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def _json(self, code, body):
            self._send(code, json.dumps(body).encode(), 'application/json')
        def do_GET(self):
            get_route(self)
        def do_POST(self):
            post_route(self)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.stream_api = api
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def post(path, body):
        request = Request(base+path, data=json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json', 'Origin': base})
        with urlopen(request, timeout=4) as result:
            return json.load(result)
    connection = None
    try:
        reply = post('/api/stream/start', {'project': TestProject.data, 'adaptive': True, 'frame': 3})
        token = reply['session']
        assert reply['adaptive_fps'] and reply['profile'] == 'Q5'
        connection = urlopen(base+f'/api/stream/events?session={token}&continuous=1', timeout=4)
        assert connection.headers['Content-Type'].startswith('text/event-stream')
        row = None
        for _ in range(30):
            line = connection.readline().decode().strip()
            if line.startswith('data: '):
                event = json.loads(line[6:])
                if event['type'] == 'frame-ready':
                    row = event
                    break
        assert row is not None
        ack = {'session': token, 'sequence': 0, 'frame': 3, 'sha256': row['image_sha256']}
        assert post('/api/stream/presented', ack)['acknowledged']
        assert not post('/api/stream/presented', ack)['acknowledged']
        assert post('/api/stream/close', {'session': token})['closed']
    finally:
        if connection is not None:
            connection.close()
        api.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_sse_disconnect_releases_subscription_slot_even_when_socket_restore_fails():
    from threading import BoundedSemaphore
    from types import SimpleNamespace
    from framerudiments.preview_stream_s2 import _continuous_events
    class Connection:
        def gettimeout(self):
            return None
        def settimeout(self, value):
            if value is None:
                raise OSError('closed socket')
    stream = SimpleNamespace(closed=True, event_slots=BoundedSemaphore(2), since=lambda *args, **kwargs: [])
    handler = SimpleNamespace(connection=Connection(), wfile=BytesIO(), close_connection=False,
                              send_response=lambda code: None, send_header=lambda *args: None,
                              end_headers=lambda: None)
    _continuous_events(handler, stream, 0)
    assert stream.event_slots.acquire(blocking=False)
    assert stream.event_slots.acquire(blocking=False)
    assert not stream.event_slots.acquire(blocking=False)


def test_new_revision_waits_for_native_worker_to_retire(tmp_path):
    from test_preview_stream_s2 import TestProject
    started, release = Event(), Event()
    created = []
    class Renderer:
        def __call__(self, n, tier):
            started.set()
            assert release.wait(4)
            return png((8, 8), n)
        def close(self):
            return None
    def factory(*args, **kwargs):
        result = Renderer()
        created.append(result)
        return result
    api = PreviewStreamAPI(tmp_path/'project.json', lambda *_: None, lambda *_: None,
                           project_loader=lambda _: TestProject(), cache_root=tmp_path/'cache',
                           adaptive_renderer_factory=factory)
    body = {'project': TestProject.data, 'adaptive': True, 'frame': 3}
    try:
        api.open(body)
        assert started.wait(2)
        worker = api.active.worker
        with pytest.raises(RuntimeError, match='retiring'):
            api.open(body)
        assert len(created) == 1
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        api.open(body)
        assert len(created) == 2
    finally:
        release.set()
        api.shutdown()
