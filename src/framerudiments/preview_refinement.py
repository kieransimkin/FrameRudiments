"""S4 stopped-cursor Q5 refinement; no background work trains the FPS controller."""
from threading import Thread
from time import monotonic

from .preview_frame_cache import _verified_png
from .preview_refinement_process import RefinementCancelled
from .preview_stream_contract import _integer, segment_for_frame


def refinement_order(segments, anchor):
    """Lazy bounded traversal: anchor/forward/backward, later sections, earlier sections."""
    current = segment_for_frame(segments, anchor)
    index = segments.index(current)
    yield from range(anchor, current.end_frame_exclusive)
    yield from range(anchor - 1, current.start_frame - 1, -1)
    for segment in segments[index + 1:]:
        yield from range(segment.start_frame, segment.end_frame_exclusive)
    for segment in reversed(segments[:index]):
        yield from range(segment.end_frame_exclusive - 1, segment.start_frame - 1, -1)


class RefinementSupervisor:
    """One thread/child per stream. All shared state uses stream.condition.

    Foreground sets foreground_active before waiting for quiescence. The child
    notices preemption at most one short polling interval later and is reaped.
    No render owns a cache lock. Commit checks happen on the parent after work.
    """
    def __init__(self, stream, factory, segments):
        self.stream, self.factory, self.segments = stream, factory, tuple(segments)
        if self.segments[-1].end_frame_exclusive != stream.frame_count:
            raise ValueError('Refinement sections must cover the stream')
        segment_for_frame(self.segments, 0)  # Validate complete contiguous coverage.
        self.epoch = 0
        self.anchor = self.candidate = None
        self.order = None
        self.suspended = True
        self.active = self.renderer_live = False
        self.state = 'waiting_for_stop'
        self.completed = self.skipped = self.cancelled = 0
        self.error_type = None
        self.stateful = None
        self.last_report = 0.0
        self.thread = Thread(target=self._run, name='framerudiments-q5-supervisor', daemon=True)
        self.thread.start()

    @property
    def quiescent(self):
        return not self.active and not self.renderer_live

    def snapshot(self):
        return dict(enabled=True, state=self.state, epoch=self.epoch, anchor_frame=self.anchor,
                    frame=self.candidate, profile='Q5', completed=self.completed, skipped=self.skipped,
                    cancellations=self.cancelled, error_type=self.error_type, process_isolated=True,
                    active=self.active, worker_alive=self.renderer_live,
                    stateful=self.stateful,
                    policy='paused_only_foreground_priority', production_equivalent=False)

    def stop_at(self, frame):
        _integer(frame, 'stop frame', 0, self.stream.frame_count - 1)
        if not self.thread.is_alive() or self.state == 'cleanup_failed':
            raise RuntimeError('Refinement supervisor requires a clean session restart')
        self.epoch += 1
        self.anchor, self.candidate = frame, None
        self.order = refinement_order(self.segments, frame)
        self.suspended = False
        self.state, self.error_type = 'queued', None
        self.completed = self.skipped = 0
        self._report(force=True)

    def suspend(self):
        self.suspended = True
        if self.anchor is not None and self.state not in ('complete', 'failed', 'quota_blocked', 'cleanup_failed', 'stale'):
            self.state = 'yielding'
        self.stream.condition.notify_all()

    def _report(self, *, force=False):
        now = monotonic()
        if force or now - self.last_report >= .25:
            self.last_report = now
            self.stream._event('refinement-progress', refinement=self.snapshot())

    def _eligible(self):
        s = self.stream
        return (not s.closed and not s.playing and not s.foreground_active and s.pending is None
                and not s.foreground_release_pending and s.foreground_cleanup_error is None
                and not self.suspended and self.order is not None and s.replay_revoked is None
                and self.state not in ('complete', 'failed', 'quota_blocked', 'stale', 'cleanup_failed'))

    def _cancelled(self, epoch):
        with self.stream.condition:
            return epoch != self.epoch or not self._eligible()

    def _run(self):
        renderer = None
        s = self.stream
        try:
            while True:
                # Retire idle native contexts before letting foreground resume.
                with s.condition:
                    if s.closed:
                        break
                    cleanup = renderer is not None and not self._eligible()
                    if not cleanup and not self._eligible():
                        s.condition.wait(.1)
                        continue
                    if not cleanup:
                        if self.candidate is None:
                            self.candidate = next(self.order, None)
                        if self.candidate is None:
                            self.state = 'complete'
                            self._report(force=True)
                            continue
                        frame, epoch = self.candidate, self.epoch
                        hit = s.cache.get(frame)
                        if hit is not None and hit.quality_rank == 5:
                            self.skipped += 1
                            self.candidate = None
                            self._report()
                            # Even an all-cached multi-hour range cannot monopolize controls.
                            s.condition.wait(.001)
                            continue
                        ticket = s.cache.ticket(frame, 'Q5')
                        self.active = True
                        self.state = 'rendering'
                        self._report()
                if cleanup:
                    renderer.close()
                    renderer = None
                    with s.condition:
                        self.renderer_live = self.active = False
                        s.condition.notify_all()
                    continue
                try:
                    if renderer is None:
                        renderer = self.factory()  # Construct on the owning supervisor thread.
                        with s.condition:
                            self.renderer_live = True
                    cancellable = getattr(renderer, 'render_cancellable', None)
                    if callable(cancellable):
                        def progress(status):
                            with s.condition:
                                if not self._cancelled(epoch):
                                    self.stateful = dict(status)
                                    self._report()
                                    if frame == s.frame:
                                        s.stateful = dict(status)
                                        s._event('stateful-progress', frame=frame, stateful=s.stateful)
                        png = cancellable(frame, 'Q5', lambda: self._cancelled(epoch), progress)
                    else:
                        png = renderer(frame, lambda: self._cancelled(epoch))
                    _verified_png(png, ticket.width, ticket.height)
                    with s.condition:
                        if self._cancelled(epoch):
                            raise RefinementCancelled('new stop or foreground priority')
                        s._verify_inputs()
                        s._bind_replay_identity(renderer)
                        outcome = s.cache.publish(ticket, png, settings_sha256=s._settings(ticket))
                        if outcome in ('stale', 'quota_rejected'):
                            s.observations.counters['quota_rejections'] += int(outcome == 'quota_rejected')
                            s.observations.counters['stale_results'] += int(outcome == 'stale')
                            self.state = 'stale' if outcome == 'stale' else 'quota_blocked'
                            self._report(force=True)
                            continue
                        self.completed += 1
                        s.observations.rendered(render_ms=0, queue_ms=0, origin='refinement')
                        self.candidate = None
                        # Off-screen upgrades never move the visible cursor.
                        if frame == s.frame and not s.playing:
                            item = s.cache.get(frame)
                            if item is not None:
                                s._event('frame-ready', **s._frame_data(item, hit=False, origin='refinement'))
                        self._report(force=frame == self.anchor)
                except RefinementCancelled:
                    with s.condition:
                        self.cancelled += 1
                        if epoch == self.epoch:
                            self.state = 'yielding'
                except Exception as exc:
                    from .preview_replay import StatefulSeekUnavailable
                    with s.condition:
                        sticky = (isinstance(exc, StatefulSeekUnavailable) and exc.reason_code in {
                            'replay_mismatch', 'runtime_identity_changed', 'input_identity_changed'})
                        if not s.closed and sticky:
                            s._replay_unavailable(exc.reason_code, s.frame)
                        # A superseded task must not poison a newer stop.
                        if epoch == self.epoch:
                            self.state, self.error_type = 'failed', type(exc).__name__
                            if isinstance(exc, StatefulSeekUnavailable):
                                self.stateful = {**(self.stateful or s.stateful), 'phase': 'unavailable',
                                                 'requested_frame': frame, 'last_reason': exc.reason_code}
                                if frame == s.frame and not sticky:
                                    s._replay_unavailable(exc.reason_code, s.frame)
                            elif getattr(renderer, 'stateful_replay', False) and frame == s.frame:
                                s._replay_unavailable('worker_failed', frame)
                            self._report(force=True)
                finally:
                    with s.condition:
                        self.active = False
                        s.condition.notify_all()
        except Exception as exc:
            with s.condition:
                self.state, self.error_type = 'failed', type(exc).__name__
                self._report(force=True)
        finally:
            try:
                if renderer is not None:
                    renderer.close()
            except Exception as exc:
                with s.condition:
                    self.state, self.error_type = 'cleanup_failed', type(exc).__name__
                    self._report(force=True)
                # Keep renderer_live set: a failed cleanup must block replacement.
            else:
                with s.condition:
                    self.renderer_live = self.active = False
                    if s.closed:
                        self.state = 'closed'
                    s.condition.notify_all()
