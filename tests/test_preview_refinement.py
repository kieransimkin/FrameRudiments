"""S4 race, priority and cache acceptance with real PNG/SQLite and spawned workers."""
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
import multiprocessing
import os
from pathlib import Path
from threading import Event, Timer
from time import monotonic, sleep

from PIL import Image
import pytest

from framerudiments.preview_frame_cache import PreviewFrameCache
from framerudiments.preview_quality import profile_capabilities
from framerudiments.preview_refinement import refinement_order
from framerudiments.preview_refinement_process import (
    RefinementCancelled, RefinementSpec, RefinementWorkerError, SpawnQ5Renderer,
)
from framerudiments.preview_stream_contract import Segment, quality_profiles
from framerudiments.preview_stream_s2 import PreviewStreamAPI, SeekStream
from test_preview_adaptive import project


def png(size=(640, 360), frame=0):
    data = BytesIO()
    Image.new('RGBA', size, (frame % 256, 90, 150, 255)).save(data, 'PNG')
    return data.getvalue()


def wait_for(predicate, seconds=5):
    until = monotonic() + seconds
    while monotonic() < until:
        if predicate():
            return
        sleep(.005)
    raise AssertionError('condition did not complete')


def wait_complete(stream, seconds=30):
    # These are durable multi-frame correctness proofs, not latency benchmarks.
    # Windows CI can spend seconds in filesystem flushes. Keep other timing
    # assertions strict, fail immediately on a terminal error, and retain state.
    until = monotonic() + seconds
    with stream.condition:
        while stream.refinement.state != 'complete':
            snapshot = stream.refinement.snapshot()
            assert snapshot['state'] not in ('failed', 'quota_blocked', 'cleanup_failed', 'stale', 'closed'), snapshot
            remaining = until - monotonic()
            assert remaining > 0, snapshot
            stream.condition.wait(min(.05, remaining))


def store(tmp_path, total=12, quota=4*1024**2):
    p = project()
    segments = (Segment('a', 0, 4, 'a'*64), Segment('b', 4, 8, 'a'*64),
                Segment('c', 8, total, 'a'*64))
    return PreviewFrameCache(tmp_path, project_fingerprint='a'*64, pipeline_fingerprint='b'*64,
        renderer_abi='test', segments=segments, profiles=quality_profiles(p),
        max_bytes=quota, ram_bytes=1024), p, segments


class IsolatedDouble:
    """Cooperative deterministic test renderer, not a claim of native execution."""
    def __init__(self):
        self.calls = []
        self.entered = Event()
        self.hold = False
        self.closed = False
    def __call__(self, frame, cancelled):
        self.calls.append(frame)
        self.entered.set()
        while self.hold:
            if cancelled():
                raise RefinementCancelled('test preemption')
            sleep(.002)
        if cancelled():
            raise RefinementCancelled('test preemption')
        return png(frame=frame)
    def close(self):
        self.closed = True


def make_stream(tmp_path, background=None, total=12, quota=4*1024**2):
    cache, p, segments = store(tmp_path, total, quota)
    created = []
    def factory():
        renderer = background if background is not None else IsolatedDouble()
        created.append(renderer)
        return renderer
    def foreground(n, tier):
        profile = cache.profiles[int(tier[1:])]
        return png((profile.output_width, profile.output_height), n)
    stream = SeekStream(cache, foreground, total, capabilities=profile_capabilities(p),
                        refinement_factory=factory)
    stream.controller.rank = 0  # Foreground fast preview; refinement MUST ignore it.
    return stream, cache, created, segments


def close(stream):
    stream.close()
    stream.worker.join(2)
    stream.refinement.thread.join(2)
    assert stream.retired


def test_priority_order_is_half_open_complete_and_lazy():
    segments = (Segment('a', 0, 3, 'a'*64), Segment('b', 3, 6, 'a'*64),
                Segment('c', 6, 10, 'a'*64))
    assert list(refinement_order(segments, 4)) == [4, 5, 3, 6, 7, 8, 9, 2, 1, 0]
    for n in range(10):
        rows = list(refinement_order(segments, n))
        assert rows[0] == n and sorted(rows) == list(range(10))
    with pytest.raises(ValueError):
        next(refinement_order(segments, 10))


def test_pause_refines_anchor_first_and_q5_never_downgrades(tmp_path):
    stream, cache, created, _ = make_stream(tmp_path)
    try:
        stream.command('pause', sequence=1, frame=5)
        wait_complete(stream)
        assert created[0].calls == [5, 6, 7, 4, 8, 9, 10, 11, 3, 2, 1, 0]
        assert stream.last_stop == 5
        assert all(cache.get(n).profile_id == 'Q5' for n in range(12))
        assert stream.controller.fresh_total == 1  # Only foreground Q0 trained it.
        assert stream.refinement.completed == 12
        data = cache.ticket(5, 'Q1')
        assert cache.publish(data, png((data.width, data.height), 5),
                             settings_sha256=stream._settings(data)) == 'unchanged'
        assert cache.get(5).profile_id == 'Q5'
        background_rows = [e for e in stream.since(0, 0) if e.get('origin') == 'refinement']
        assert [e['frame'] for e in background_rows] == [5]  # No offscreen presentation.
        before = stream.controller.snapshot()
        row = background_rows[0]
        assert stream.acknowledge(1, 5, row['image_sha256'])
        assert stream.controller.snapshot()['acknowledged'] == before['acknowledged']
    finally:
        close(stream)


def test_best_cached_anchor_is_skipped_and_work_is_not_repeated(tmp_path):
    stream, cache, created, _ = make_stream(tmp_path)
    ticket = cache.ticket(5, 'Q5')
    cache.publish(ticket, png(frame=5), settings_sha256=stream._settings(ticket))
    try:
        stream.command('pause', sequence=1, frame=5)
        wait_complete(stream)
        assert created[0].calls[0] == 6 and 5 not in created[0].calls
        calls = len(created[0].calls)
        stream.command('stop', sequence=2, frame=5)
        wait_complete(stream)
        assert stream.refinement.skipped == 12
        assert len(created[0].calls) == calls
    finally:
        close(stream)


def test_resume_cancels_inflight_q5_before_foreground_can_render(tmp_path):
    bg = IsolatedDouble()
    bg.hold = True
    stream, cache, _, _ = make_stream(tmp_path, bg)
    try:
        stream.command('pause', sequence=1, frame=5)
        assert bg.entered.wait(2)
        stream.command('play', sequence=2, frame=9)
        wait_for(lambda: cache.get(9) is not None)
        assert bg.closed and cache.get(9).quality_rank == 0
        assert cache.get(5).quality_rank == 0  # Cancelled high-quality result never published.
        count = len(bg.calls)
        sleep(.05)
        assert len(bg.calls) == count
        assert stream.refinement.cancelled >= 1
    finally:
        close(stream)


def test_1000_stops_collapse_to_latest_priority_epoch(tmp_path):
    bg = IsolatedDouble()
    bg.hold = True
    stream, cache, _, _ = make_stream(tmp_path, bg)
    try:
        stream.command('pause', sequence=1, frame=5)
        assert bg.entered.wait(2)
        # Hold the coordinator lock to reproduce a burst already queued by HTTP.
        with stream.condition:
            for i in range(2, 1002):
                stream.command('pause', sequence=i, frame=i % 12)
        bg.hold = False
        wait_complete(stream)
        assert stream.last_stop == 1001 % 12
        assert bg.calls[1] == 1001 % 12
        assert stream.refinement.epoch == 1001
        assert len(bg.calls) <= 13
    finally:
        close(stream)


def test_generation_change_rejects_background_publication(tmp_path):
    bg = IsolatedDouble()
    bg.hold = True
    stream, cache, _, _ = make_stream(tmp_path, bg)
    try:
        stream.command('pause', sequence=1, frame=5)
        assert bg.entered.wait(2)
        cache.invalidate_all()
        bg.hold = False
        wait_for(lambda: stream.refinement.state == 'stale')
        assert all(cache.get(n) is None for n in range(12))
        assert stream.refinement.completed == 0
    finally:
        close(stream)


def test_shutdown_fences_both_cache_and_events_and_releases_renderer(tmp_path):
    bg = IsolatedDouble()
    bg.hold = True
    stream, cache, _, _ = make_stream(tmp_path, bg)
    stream.command('pause', sequence=1, frame=5)
    assert bg.entered.wait(2)
    close(stream)
    assert bg.closed and cache.get(5).quality_rank == 0
    assert stream.refinement.state == 'closed'


def test_quota_blocks_refinement_without_retry_loop(tmp_path):
    stream, cache, created, _ = make_stream(tmp_path, quota=1)
    try:
        stream.command('pause', sequence=1, frame=5)
        wait_for(lambda: stream.refinement.state == 'quota_blocked')
        calls = len(created[0].calls)
        sleep(.03)
        assert len(created[0].calls) == calls == 1 and cache.get(5) is None
    finally:
        close(stream)


def test_renderer_error_is_content_free_and_can_be_retried_at_a_new_stop(tmp_path):
    class Fails(IsolatedDouble):
        def __call__(self, frame, cancelled):
            raise RuntimeError('/private/source secret text')
    stream, cache, _, _ = make_stream(tmp_path, Fails())
    try:
        stream.command('pause', sequence=1, frame=5)
        wait_for(lambda: stream.refinement.state == 'failed')
        status = stream.status()['refinement']
        assert status['error_type'] == 'RuntimeError'
        assert 'private' not in json.dumps(status)
        assert cache.get(5).quality_rank == 0
    finally:
        close(stream)


def _synthetic_child(conn, spec, directory):
    """A real spawn/IPC/lifetime test target, deliberately NOT the native renderer."""
    mode = json.loads(spec.draft_json).get('mode')
    try:
        while True:
            n = conn.recv()
            if n is None:
                return
            if mode == 'hang':
                sleep(30)
            if mode == 'exit':
                return
            data = png((8, 8), n)
            (Path(directory)/'result.png').write_bytes(data)
            conn.send({'ok': True, 'frame': n, 'bytes': len(data),
                       'sha256': '0'*64 if mode == 'bad' else sha256(data).hexdigest()})
    finally:
        conn.close()


def spec(tmp_path, mode='normal'):
    return RefinementSpec.from_snapshot(tmp_path/'project.json', {}, {'mode': mode}, {}, tmp_path/'owned')


def test_real_spawn_is_separate_and_reuses_child_then_removes_owned_files(tmp_path):
    renderer = SpawnQ5Renderer(spec(tmp_path), worker_target=_synthetic_child)
    try:
        assert renderer(3, lambda: False) == png((8, 8), 3)
        pid = renderer.last_pid
        assert pid != os.getpid()
        assert renderer(7, lambda: False) == png((8, 8), 7)
        assert renderer.last_pid == pid
    finally:
        renderer.close()
    assert not list((tmp_path/'owned').iterdir())
    assert pid not in [p.pid for p in multiprocessing.active_children()]


@pytest.mark.parametrize('mode', ['bad', 'exit'])
def test_worker_failure_never_returns_pixels_and_is_reaped(tmp_path, mode):
    renderer = SpawnQ5Renderer(spec(tmp_path, mode), worker_target=_synthetic_child)
    with pytest.raises((RefinementWorkerError, EOFError)):
        renderer(4, lambda: False)
    assert renderer.process is None
    assert not list((tmp_path/'owned').iterdir())


def test_inflight_spawn_cancel_is_bounded_and_leaves_no_worker(tmp_path):
    renderer = SpawnQ5Renderer(spec(tmp_path, 'hang'), worker_target=_synthetic_child)
    cancel = Event()
    timer = Timer(.4, cancel.set)
    timer.start()
    try:
        with pytest.raises(RefinementCancelled):
            renderer(4, cancel.is_set)
        assert renderer.process is None
        assert not list((tmp_path/'owned').iterdir())
    finally:
        timer.cancel()
        renderer.close()


def test_child_deadline_is_not_an_unbounded_hang(tmp_path):
    renderer = SpawnQ5Renderer(spec(tmp_path, 'hang'), timeout_seconds=.2,
                               worker_target=_synthetic_child)
    with pytest.raises(RefinementWorkerError, match='timeout'):
        renderer(4, lambda: False)
    assert renderer.process is None


def test_snapshot_is_immutable_and_no_process_exists_before_work(tmp_path):
    draft = {'x': 3}
    snapshot = RefinementSpec.from_snapshot('project.json', {}, draft, {}, tmp_path)
    draft['x'] = 4
    assert json.loads(snapshot.draft_json) == {'x': 3}
    renderer = SpawnQ5Renderer(replace(snapshot, draft_json='{}'))
    with pytest.raises(RefinementCancelled):
        renderer(0, lambda: True)
    assert renderer.process is None
    renderer.close()


def test_api_refinement_is_explicit_and_requires_adaptive_mode(tmp_path):
    from test_preview_stream_s2 import TestProject
    api = PreviewStreamAPI(tmp_path/'project.json', lambda *_: None, lambda *_: None,
                           project_loader=lambda _: TestProject(), cache_root=tmp_path/'cache')
    with pytest.raises(ValueError, match='requires adaptive'):
        api.open({'project': TestProject.data, 'refine': True})
    with pytest.raises(ValueError, match='boolean'):
        api.open({'project': TestProject.data, 'adaptive': True, 'refine': 'yes'})


def test_cached_pause_releases_foreground_context_before_background_starts(tmp_path):
    from threading import get_ident
    cache, p, _ = store(tmp_path)
    lifecycle = []
    class Foreground:
        def __call__(self, n, tier):
            lifecycle.append(('render', get_ident()))
            profile = cache.profiles[int(tier[1:])]
            return png((profile.output_width, profile.output_height), n)
        def release_compositor(self):
            lifecycle.append(('release', get_ident()))
    class Background(IsolatedDouble):
        def __call__(self, n, cancelled):
            assert lifecycle[-1][0] == 'release'
            return super().__call__(n, cancelled)
    stream = SeekStream(cache, Foreground(), 12, capabilities=profile_capabilities(p),
                        refinement_factory=Background)
    stream.controller.rank = 0
    try:
        stream.command('play', sequence=1, frame=5)
        wait_for(lambda: cache.get(5) is not None)
        stream.command('pause', sequence=2, frame=5)  # Cache hit: no foreground render needed.
        try:
            wait_complete(stream)
        except AssertionError:
            pytest.fail(f"Cached-pause handoff did not complete: {stream.status()}; lifecycle={lifecycle}")
        assert [name for name, _ in lifecycle] == ['render', 'release']
        assert lifecycle[0][1] == lifecycle[1][1] != get_ident()
        assert cache.get(5).profile_id == 'Q5'
    finally:
        close(stream)


def test_api_constructs_background_only_after_stop_and_closes_revision(tmp_path):
    from test_preview_stream_s2 import TestProject
    created, snapshots = [], []
    class Background(IsolatedDouble):
        def __call__(self, n, cancelled):
            self.calls.append(n)
            if cancelled():
                raise RefinementCancelled('test preemption')
            return png((8, 8), n)
    def factory(specification):
        snapshots.append(specification)
        bg = Background()
        created.append(bg)
        return bg
    def foreground(_path, _draft, _sidecars, **_options):
        return lambda n, tier: png((8, 8), n)
    api = PreviewStreamAPI(tmp_path/'project.json', lambda *_: None, lambda *_: None,
                           project_loader=lambda _: TestProject(), cache_root=tmp_path/'cache',
                           adaptive_renderer_factory=foreground, refinement_factory=factory)
    reply = api.open({'project': TestProject.data, 'adaptive': True, 'refine': True, 'frame': 3})
    stream = api.get(reply['session'])
    try:
        wait_for(lambda: stream.cache.get(3) is not None)
        assert not created
        api.command({'session': reply['session'], 'sequence': 1, 'command': 'pause', 'frame': 3})
        wait_complete(stream)
        assert snapshots[0].draft_json == json.dumps(TestProject.data, ensure_ascii=False, allow_nan=False)
        assert stream.refinement.skipped == 1  # Q5 foreground already completed the anchor.
        assert stream.cache.get(4).profile_id == 'Q5'
        assert api.close(reply['session'])['closed']
        wait_for(lambda: stream.retired)
        assert all(bg.closed for bg in created)
        reopened = api.open({'project': TestProject.data, 'adaptive': True, 'refine': True, 'frame': 4})
        assert reopened['session'] != reply['session']
        assert api.get(reopened['session']).cache.get(4).profile_id == 'Q5'
    finally:
        api.shutdown()


def test_cancelled_process_can_restart_cleanly_with_same_immutable_snapshot(tmp_path):
    cancelled = Event()
    renderer = SpawnQ5Renderer(spec(tmp_path), worker_target=_synthetic_child)
    try:
        assert renderer(0, lambda: False) == png((8, 8), 0)
        old = renderer.last_pid
        cancelled.set()
        with pytest.raises(RefinementCancelled):
            renderer(1, cancelled.is_set)
        # Immediate preemption does not start work; supervisor cleanup retires it.
        renderer.close()
        cancelled.clear()
        assert renderer(2, cancelled.is_set) == png((8, 8), 2)
        assert renderer.last_pid != old
        assert old not in [p.pid for p in multiprocessing.active_children()]
    finally:
        renderer.close()
