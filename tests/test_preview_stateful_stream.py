"""S6 coordinator races with real SQLite/PNG and explicit cancellable doubles.

The doubles prove scheduling, generations and diagnostics; native replay pixels
are exercised separately. No browser/engine fidelity is inferred here.
"""
from threading import Event
from time import monotonic, sleep

import pytest

from framerudiments.preview_quality import profile_capabilities
from framerudiments.preview_refinement_process import RefinementCancelled
from framerudiments.preview_replay import StatefulSeekUnavailable
from framerudiments.preview_seek_support import seek_status
from framerudiments.preview_stream_s2 import PreviewStreamAPI
from test_preview_dependencies import project
from test_preview_revision_stream import open_retry
from test_preview_segment_cache import png, populate


def wait_for(predicate, seconds=5):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        result = predicate()
        if result:
            return result
        sleep(.005)
    raise AssertionError('stateful coordinator did not reach expected state')


def event(stream, kind, *, frame=None, sequence=None):
    return wait_for(lambda: next((row for row in stream.since(0, 0)
        if row['type'] == kind and (frame is None or row.get('frame') == frame)
        and (sequence is None or row.get('cursor_seq') == sequence)), None))


class RuntimeGuard:
    digest = 'e' * 64
    reason = None
    def __call__(self):
        if self.reason:
            raise StatefulSeekUnavailable(self.reason)


class CancellableRenderer:
    stateful_replay = True
    cleanup_error = False
    last_setup_ms = 0

    def __init__(self, fixtures, index):
        self.fixtures, self.index = fixtures, index
        self.calls, self.cancelled = [], []
        self.releases = self.closes = 0
        self.native_live = False
        self.behaviour = None
        self.engine_identity = 'd' * 64
        self.stateful_status = seek_status({'effects': [{}]})

    def render_cancellable(self, frame, tier, cancelled, progress=None):
        assert tier == 'Q5'
        assert not any(other.native_live for other in self.fixtures.renderers if other is not self)
        self.native_live = True
        self.calls.append(frame)
        self.stateful_status = {**self.stateful_status, 'phase': 'replaying', 'requested_frame': frame}
        self.stateful_status.pop('engine_identity_sha256', None)
        if progress:
            progress(self.stateful_status)
        if self.behaviour:
            self.behaviour(frame, cancelled)
        if cancelled():
            self.cancelled.append(frame)
            raise RefinementCancelled('synthetic latest-intent preemption')
        self.stateful_status = {**self.stateful_status, 'phase': 'ready',
                               'verified_prefix_frames': frame + 1, 'last_reason': None,
                               'engine_identity_sha256': self.engine_identity}
        if progress:
            progress(self.stateful_status)
        return png(seed=frame)

    def release_compositor(self):
        self.releases += 1
        self.native_live = False

    def close(self):
        self.closes += 1
        self.native_live = False


class Fixture:
    def __init__(self, tmp_path, *, refine=False):
        self.project, self.renderers, self.guard = project(), [], RuntimeGuard()
        self.project.data['effects'] = [dict(id='feedback', engine='butterchurn', preset='feedback', seed=7)]
        self.on_create = None
        self.api = PreviewStreamAPI(tmp_path/'project.json', lambda *_: None, lambda *_: None,
            cache_root=tmp_path/'cache', project_loader=lambda _: self.project,
            stateful_guard_factory=lambda _: self.guard,
            stateful_renderer_factory=self.factory)
        self.body = dict(project=self.project.data, adaptive=True, refine=refine, frame=0,
                         client_id='s6_synthetic_test_client', edit_sequence=1)

    def factory(self, _spec):
        renderer = CancellableRenderer(self, len(self.renderers))
        self.renderers.append(renderer)
        if self.on_create:
            self.on_create(renderer)
        return renderer

    def open(self):
        self.api.open(self.body)
        self.stream = self.api.active
        event(self.stream, 'frame-ready', frame=0)
        wait_for(lambda: not self.stream.foreground_active)
        return self.stream

    def close(self):
        self.api.shutdown()
        stream = self.api.active
        if stream:
            stream.worker.join(3)
            if stream.refinement:
                stream.refinement.thread.join(3)
            assert stream.retired
        assert not any(renderer.native_live for renderer in self.renderers)


def test_latest_seek_cancels_obsolete_replay_without_publishing_it(tmp_path):
    fixture = Fixture(tmp_path)
    stream = fixture.open()
    entered = Event()
    def hold(frame, cancelled):
        if frame == 17:
            entered.set()
            wait_for(cancelled)
    fixture.renderers[0].behaviour = hold
    try:
        stream.command('seek', sequence=1, frame=17)
        assert entered.wait(2)
        stream.command('seek', sequence=2, frame=3)
        ready = event(stream, 'frame-ready', frame=3, sequence=2)
        assert ready['quality_rank'] == 5 and ready['stateful']['phase'] == 'ready'
        assert fixture.renderers[0].cancelled == [17]
        assert stream.cache.get(17) is None
        assert not [row for row in stream.since(0, 0) if row['type'] == 'frame-ready' and row['frame'] == 17]
        assert stream.controller.snapshot()['fresh_rendered'] == 0
    finally:
        fixture.close()


def test_pause_releases_foreground_then_background_yields_to_new_play(tmp_path):
    fixture = Fixture(tmp_path, refine=True)
    stream = fixture.open()
    entered = Event()
    def on_create(renderer):
        if renderer.index == 1:
            def hold(frame, cancelled):
                entered.set()
                wait_for(cancelled)
            renderer.behaviour = hold
    fixture.on_create = on_create
    try:
        stream.command('pause', sequence=1, frame=5)
        event(stream, 'frame-ready', frame=5, sequence=1)
        assert entered.wait(2)
        retained = stream.cache.get(5)
        assert fixture.renderers[0].releases >= 1
        assert fixture.renderers[1].calls == [6]  # Anchor already verified by foreground.
        stream.command('play', sequence=2, frame=9)
        event(stream, 'frame-ready', frame=9, sequence=2)
        assert fixture.renderers[1].cancelled == [6]
        assert fixture.renderers[1].closes >= 1
        assert stream.cache.get(5) == retained and retained.quality_rank == 5
        assert stream.cache.get(6) is None
        assert not stream.refinement.renderer_live
        assert stream.controller.rank == 5
    finally:
        fixture.close()


@pytest.mark.parametrize('reason', ['runtime_identity_changed', 'input_identity_changed'])
def test_guard_identity_failure_revokes_existing_frames_only_once(tmp_path, reason):
    fixture = Fixture(tmp_path)
    stream = fixture.open()
    populate(stream.cache, range(1, 30))
    old = stream.cache.get(0)
    baseline = stream.cache.stats()['section_revision']
    fixture.guard.reason = reason
    try:
        for sequence in range(1, 5):
            with pytest.raises(ValueError, match='identity changed'):
                stream.command('seek', sequence=sequence, frame=0)
        with pytest.raises(ValueError, match='identity changed'):
            stream.image(0, old.generation, old.image_sha256)
        assert stream.replay_revoked == reason
        assert stream.cache.stats()['frames'] == 0
        assert stream.cache.stats()['section_revision'] == baseline + 1
        invalidations = [row for row in stream.since(0, 0) if row['type'] == 'cache-invalidated'
                         and row['invalidation']['invalidated_frames'] == 30]
        assert len(invalidations) == 1
    finally:
        fixture.close()


def test_transient_replay_budget_failure_does_not_revoke_verified_frames_or_next_seek(tmp_path):
    fixture = Fixture(tmp_path)
    stream = fixture.open()
    retained = stream.cache.get(0)
    def exhausted(frame, _cancelled):
        if frame == 29:
            raise StatefulSeekUnavailable('replay_budget_exceeded')
    fixture.renderers[0].behaviour = exhausted
    try:
        stream.command('seek', sequence=1, frame=29)
        failure = event(stream, 'stateful-seek-unavailable', frame=29)
        assert failure['reason'] == 'replay_budget_exceeded'
        assert stream.replay_revoked is None and stream.cache.get(0) == retained
        stream.command('seek', sequence=2, frame=2)
        event(stream, 'frame-ready', frame=2, sequence=2)
        assert stream.stateful['phase'] == 'ready'
        assert stream.cache.get(2).quality_rank == 5
    finally:
        fixture.close()


def test_new_feedback_qualification_revises_one_workspace_cache(tmp_path):
    fixture = Fixture(tmp_path)
    stream = fixture.open()
    root = stream.cache.root
    original = stream.cache.get(0)
    try:
        for sequence in range(2, 6):
            reply = open_retry(fixture.api, {**fixture.body, 'edit_sequence': sequence})
            stream = fixture.api.active
            ready = event(stream, 'frame-ready', frame=0)
            assert stream.cache.root == root
            assert ready['cache_hit'] is False  # New native reset must qualify again.
            assert ready['generation'] > original.generation
            assert reply['invalidation']['qualification_reset'] is True
            assert len(list((tmp_path/'cache'/'section-cache-v1').iterdir())) == 1
            original = stream.cache.get(0)
    finally:
        fixture.close()


def test_feedback_admits_only_q5_and_native_temporal_stays_full_canvas():
    p = project()
    p.data['effects'] = [dict(engine='butterchurn', preset='feedback')]
    assert not any(row['supported'] for row in profile_capabilities(p))
    assert [row['quality_rank'] for row in profile_capabilities(p, allow_stateful=True) if row['supported']] == [5]
    p.data.pop('effects')
    p.data['post_effects'] = {'temporal': {'shutter_frames': 2}}
    rows = profile_capabilities(p, allow_stateful=True)
    assert [row['quality_rank'] for row in rows if row['supported']] == [3, 4, 5]
    assert all((row['output_width'], row['output_height']) == (16, 16) for row in rows if row['supported'])


def test_obsolete_foreground_parity_failure_still_revokes_session(tmp_path):
    fixture = Fixture(tmp_path)
    stream = fixture.open()
    entered, release = Event(), Event()
    def fail_after_seek(frame, _cancelled):
        if frame == 17:
            entered.set()
            assert release.wait(3)
            raise StatefulSeekUnavailable('replay_mismatch')
    fixture.renderers[0].behaviour = fail_after_seek
    try:
        stream.command('seek', sequence=1, frame=17)
        assert entered.wait(2)
        stream.command('seek', sequence=2, frame=0)  # Previously verified cache hit.
        release.set()
        wait_for(lambda: not stream.foreground_active)
        assert stream.replay_revoked == 'replay_mismatch'
        assert stream.cache.stats()['frames'] == 0
        assert stream.command('seek', sequence=3, frame=0)['state'] == 'stateful_seek_unavailable'
    finally:
        release.set()
        fixture.close()


@pytest.mark.parametrize('new_intent', ['pause', 'play'])
def test_background_parity_failure_revokes_new_stop_and_waiting_foreground(tmp_path, new_intent):
    fixture = Fixture(tmp_path, refine=True)
    stream = fixture.open()
    entered, release = Event(), Event()
    def on_create(renderer):
        if renderer.index == 1:
            def fail_once(_frame, _cancelled):
                if len(renderer.calls) == 1:
                    entered.set()
                    assert release.wait(3)
                    raise StatefulSeekUnavailable('replay_mismatch')
            renderer.behaviour = fail_once
    fixture.on_create = on_create
    try:
        stream.command('pause', sequence=1, frame=0)
        assert entered.wait(2)
        stream.command(new_intent, sequence=2, frame=0 if new_intent == 'pause' else 5)
        if new_intent == 'play':
            wait_for(lambda: stream.foreground_active)  # Waiting for native cleanup.
        release.set()
        wait_for(lambda: not stream.refinement.active and not stream.foreground_active)
        assert stream.replay_revoked == 'replay_mismatch'
        assert stream.cache.stats()['frames'] == 0
        assert 5 not in fixture.renderers[0].calls  # Revoked while queued: never start.
    finally:
        release.set()
        fixture.close()


@pytest.mark.parametrize('same_identity', [True, False])
def test_independent_workers_must_share_one_observed_engine_identity(tmp_path, same_identity):
    fixture = Fixture(tmp_path, refine=True)
    stream = fixture.open()
    def on_create(renderer):
        if renderer.index == 1:
            renderer.engine_identity = 'd' * 64 if same_identity else 'e' * 64
    fixture.on_create = on_create
    try:
        stream.command('pause', sequence=1, frame=0)
        if same_identity:
            wait_for(lambda: stream.refinement.state == 'complete')
            assert stream.replay_revoked is None
            assert stream.cache.stats()['frames'] == stream.frame_count
            assert all(stream.cache.get(frame).quality_rank == 5 for frame in range(stream.frame_count))
        else:
            failure = event(stream, 'stateful-seek-unavailable')
            assert failure['reason'] == 'runtime_identity_changed'
            assert stream.replay_revoked == 'runtime_identity_changed'
            assert stream.cache.stats()['frames'] == 0
            assert stream.cache.get(1) is None  # Different device/reset identity was never admitted.
    finally:
        fixture.close()
