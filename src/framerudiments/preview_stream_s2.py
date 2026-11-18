"""S2: local, opt-in, cache-backed seekable preview transport.

One foreground render thread, one replaceable pending job. Legacy Q0 remains a downsample.
Explicit adaptive sessions use native-size S3 plans and a measured controller.
Neither mode claims production approval. The existing audio clock remains authoritative.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from copy import copy, deepcopy
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from threading import BoundedSemaphore, Condition, RLock, Thread
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlsplit
import json
import re

from PIL import Image, UnidentifiedImageError

from .preview_frame_cache import PreviewFrameCache, _verified_png
from .preview_dependencies import build_dependency_index, cache_namespace, implementation_fingerprint
from .preview_segment_cache import SectionFrameCache
from .preview_seek_support import seek_status, temporal_reach
from .preview_adaptive import AdaptiveController
from .preview_quality import RENDERER_ABI as ADAPTIVE_ABI, PinnedInputGuard, profile_capabilities
from .preview_replay import StatefulSeekUnavailable
from .preview_qualification import StreamQualification
from .preview_stream_contract import (
    _integer, canonical_digest, project_fingerprint, quality_profiles,
    requested_target_fps, section_segments,
)

RENDERER_ABI = 'framerudiments.preview-stream-s2-q0.v1'
SCHEMA = 'framerudiments.preview-stream-s2.v1'
_MAX_SEQ = 2**53 - 1


def q0_png(png, size):
    """Verified RGBA image with exact S0 Q0 dimensions; never assert Q5."""
    if not isinstance(png, bytes) or not 0 < len(png) <= 256 * 1024**2:
        raise ValueError('bounded PNG bytes required')
    try:
        with Image.open(BytesIO(png)) as im:
            if im.format != 'PNG':
                raise ValueError('PNG required')
            im.load()
            result = im.convert('RGBA').resize(size, Image.Resampling.LANCZOS)
            buffer = BytesIO()
            result.save(buffer, format='PNG')
            return buffer.getvalue()
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError('invalid preview PNG') from exc


class SeekStream:
    """Latest-intent queue with optional adaptive native rendering (S3)."""

    def __init__(self, cache, renderer, frame_count, target_fps=30, telemetry=None,
                 *, capabilities=None, verifier=None, clock=monotonic, refinement_factory=None,
                 stateful=None, qualification_identity=None, acknowledgement_source='client_decode_ack'):
        _integer(frame_count, 'frame_count', 1, 10_000_000)
        _integer(target_fps, 'target_fps', 15, 120)
        self.cache, self.renderer = cache, renderer
        self.frame_count, self.target_fps = frame_count, target_fps
        self.telemetry, self.verifier, self.clock = telemetry, verifier, clock
        self.capabilities = capabilities
        self.stateful = dict(stateful) if stateful is not None else seek_status({})
        self.replay_revoked = None
        self.replay_engine_identity = None
        self.invalidation = getattr(cache, 'last_revision', None)
        self.source_revoked = False
        self.controller = (AdaptiveController(target_fps, clock=clock, supported_ranks=[
            row['quality_rank'] for row in capabilities if row['supported']])
            if capabilities is not None else None)
        self.token = token_urlsafe(32)
        self.condition = Condition()
        self.events = deque(maxlen=128)
        self.event_seq = 0
        self.cursor_seq = -1
        self.frame = self.last_stop = 0
        self.playing = False
        self.pending = None
        self.closed = False
        self.acks = deque(maxlen=128)
        self.last_presented_frame = None
        self.request_at = self.clock()
        self.event_slots = BoundedSemaphore(2)
        self.foreground_active = False
        self.foreground_release_pending = False
        self.foreground_cleanup_error = None
        self.refinement = None
        self.observations = StreamQualification(cache, frame_count, target_fps, clock=clock,
            identity=qualification_identity, acknowledgement_source=acknowledgement_source)
        if refinement_factory is not None:
            if self.controller is None or not capabilities[5]['supported']:
                raise ValueError('Refinement requires an eligible adaptive Q5 renderer')
            from .preview_refinement import RefinementSupervisor
            self.refinement = RefinementSupervisor(self, refinement_factory, cache.segment_snapshot())
        self.worker = Thread(target=self._worker, name='framerudiments-preview-foreground', daemon=True)
        self.worker.start()

    def _event(self, kind, **properties):
        self.observations.event(kind, self.cursor_seq, properties)
        self.event_seq += 1
        self.events.append(dict(schema=SCHEMA, id=self.event_seq, type=kind,
                                cursor_seq=self.cursor_seq, **properties))
        self.condition.notify_all()

    def _frame_data(self, item, *, hit, origin='foreground'):
        params = urlencode(dict(session=self.token, frame=item.frame,
                                generation=item.generation, sha256=item.image_sha256))
        stateful = (getattr(self.refinement, 'stateful', None) if origin == 'refinement' else None) or self.stateful
        if stateful.get('enabled'):
            stateful = {**stateful, 'phase': 'ready', 'requested_frame': item.frame, 'last_reason': None}
        return dict(frame=item.frame, generation=item.generation,
                    profile_id=item.profile_id, quality_rank=item.quality_rank,
                    width=item.width, height=item.height, cache_hit=hit,
                    production_equivalent=False, image_sha256=item.image_sha256, origin=origin,
                    stateful=stateful,
                    refinement=self.refinement.snapshot() if self.refinement else None,
                    adaptive=self.controller.snapshot() if self.controller else None,
                    request_age_ms=max(0, (self.clock()-self.request_at)*1000),
                    url='/api/stream/frame?' + params)

    def status(self, state='active', *, refresh_qualification=False):
        return dict(schema=SCHEMA, session=self.token, state=state,
                    cursor_seq=self.cursor_seq, frame=self.frame,
                    target_fps=self.target_fps, playing=self.playing, stopped_frame=self.last_stop,
                    profile=self.controller.profile_id if self.controller else 'Q0',
                    adaptive_fps=self.controller is not None, production_equivalent=False,
                    adaptive=self.controller.snapshot() if self.controller else None,
                    capabilities=self.capabilities, invalidation=self.invalidation,
                    stateful=self.stateful,
                    qualification=self.observations.snapshot(refresh=refresh_qualification),
                    refinement=self.refinement.snapshot() if self.refinement else dict(enabled=False))

    def qualification(self):
        """A detached bounded receipt; no session token, project text or paths."""
        with self.condition:
            return self.observations.snapshot(full=True, refresh=True)

    def _settings(self, ticket):
        """Identical settings identity for foreground and background Q5 CAS."""
        if getattr(self.cache, 'scoped_dependencies', False):
            return self.cache.settings_identity(ticket)
        settings = {'renderer_abi': ADAPTIVE_ABI if self.controller else RENDERER_ABI,
                    'pipeline': ticket.pipeline_fingerprint, 'profile': ticket.profile_id}
        if self.controller:
            settings['settings'] = self.capabilities[ticket.quality_rank]['settings_sha256']
        return canonical_digest(settings)

    @property
    def retired(self):
        return (not self.worker.is_alive() and self.foreground_cleanup_error is None
                and (self.refinement is None or
                (not self.refinement.thread.is_alive() and self.refinement.quiescent)))

    def _verify_inputs(self):
        if self.source_revoked:
            raise ValueError('Pinned source changed; reopen a verified project')
        if self.verifier:
            try:
                self.verifier()
            except StatefulSeekUnavailable as exc:
                self._replay_unavailable(exc.reason_code, self.frame)
                raise ValueError('Stateful preview identity changed; reopen a verified project') from None
            except (ValueError, OSError):
                self.source_revoked = True
                self.pending = None
                if self.refinement:
                    self.refinement.suspend()
                self.cache.invalidate_all()
                self.invalidation = getattr(self.cache, 'last_revision', None)
                if self.invalidation is not None:
                    self._event('cache-invalidated', invalidation=self.invalidation)
                raise ValueError('Pinned source changed; reopen a verified project') from None

    def command(self, name, *, sequence, frame=None, target_fps=None):
        received_at = self.clock()
        if name not in ('seek', 'advance', 'play', 'pause', 'stop', 'target_fps'):
            raise ValueError('unknown stream command')
        _integer(sequence, 'client sequence', 0, _MAX_SEQ)
        if frame is not None:
            _integer(frame, 'frame', 0, self.frame_count - 1)
        if name in ('seek', 'advance', 'play') and frame is None:
            raise ValueError('seek, advance and play need absolute frame')
        if name == 'target_fps' and target_fps is None:
            raise ValueError('FPS target is missing')
        if target_fps is not None:
            _integer(target_fps, 'target_fps', 15, 120)
        with self.condition:
            if self.closed:
                raise ValueError('stream closed')
            if sequence <= self.cursor_seq:
                return self.status('out_of_order')
            self._verify_inputs()
            self.observations.command(sequence, self.frame if frame is None else frame, name,
                received_at=received_at, coalesced=self.pending is not None, target_fps=target_fps,
                profile=self.controller.profile_id if self.controller else 'Q0')
            self.cursor_seq = sequence
            self.request_at = self.clock()
            if frame is not None:
                self.frame = frame
            if name in ('play', 'advance'):
                self.playing = True
            elif name in ('pause', 'stop'):
                self.playing = False
                self.last_stop = self.frame
            if target_fps is not None:
                self.target_fps = target_fps
            if self.controller and (name in ('seek', 'play', 'pause', 'stop') or target_fps is not None):
                self.controller.reset(name, target_fps=target_fps)
            self.pending = None
            if self.refinement and self.replay_revoked is None:
                if name in ('pause', 'stop'):
                    self.foreground_release_pending = True
                    self.refinement.stop_at(self.frame)
                elif name in ('seek', 'play', 'advance'):
                    self.refinement.suspend()
            self._event('cursor', frame=self.frame, playing=self.playing)
            if self.replay_revoked is not None:
                self.stateful = {**self.stateful, 'phase': 'unavailable',
                                 'requested_frame': self.frame, 'last_reason': self.replay_revoked}
                self._event('stateful-seek-unavailable', frame=self.frame,
                            reason=self.replay_revoked, stateful=self.stateful)
                self.observations.command_ack(name, received_at)
                return self.status('stateful_seek_unavailable')
            cached = self.cache.get(self.frame)
            self.observations.lookup(cached is not None)
            if cached is not None:
                if self.controller:
                    self.controller.hit()
                self._event('frame-ready', **self._frame_data(cached, hit=True, origin='cache'))
                if self.telemetry:
                    self.telemetry.record_hit()
            # A stopped-frame upgrade is foreground work, not the S4 worker.
            if cached is None or (self.controller and not self.playing
                                  and cached.quality_rank < self.controller.rank):
                self.pending = (self.frame, sequence, self.clock(),
                                self.controller.epoch if self.controller else 0)
                self.condition.notify_all()
            self.observations.command_ack(name, received_at)
            return self.status('accepted')

    def since(self, last=0, timeout=3):
        _integer(last, 'event cursor', 0, _MAX_SEQ)
        if type(timeout) not in (float, int) or not 0 <= timeout <= 3:
            raise ValueError('invalid event wait')
        with self.condition:
            if last >= self.event_seq and not self.closed:
                self.condition.wait_for(lambda: self.closed or self.event_seq > last, timeout)
            if self.events and last and last < self.events[0]['id'] - 1:
                return [dict(schema=SCHEMA, id=self.event_seq, type='resync',
                             cursor_seq=self.cursor_seq, frame=self.frame)]
            return [dict(e) for e in self.events if e['id'] > last]

    def acknowledge(self, sequence, frame, digest):
        _integer(sequence, 'client sequence', 0, _MAX_SEQ)
        _integer(frame, 'frame', 0, self.frame_count - 1)
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid image digest')
        with self.condition:
            identity = (sequence, frame, digest)
            if self.closed or sequence != self.cursor_seq or identity in self.acks:
                return False
            delivered = any(e['type'] == 'frame-ready' and e['cursor_seq'] == sequence
                            and e['frame'] == frame and e['image_sha256'] == digest for e in self.events)
            if not delivered:
                return False
            self.acks.append(identity)
            repeated = frame == self.last_presented_frame
            self.last_presented_frame = frame
            self.observations.acknowledged(repeated=repeated)
            refined = any(e['type'] == 'frame-ready' and e['cursor_seq'] == sequence
                          and e.get('image_sha256') == digest and e.get('origin') == 'refinement'
                          for e in self.events)
            if self.controller and not refined:
                self.controller.acknowledge(max(0, (self.clock()-self.request_at)*1000), repeated=repeated)
            if self.telemetry:
                self.telemetry.record_presentation(repeated=repeated)
            return True

    def image(self, frame, generation, digest):
        _integer(frame, 'frame', 0, self.frame_count-1)
        _integer(generation, 'generation', 0, 2**63-1)
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid image digest')
        with self.condition:
            if self.closed:
                raise ValueError('stream closed')
            self._verify_inputs()
            hit = self.cache.get(frame)
            return (hit.png if hit is not None and hit.generation == generation
                    and hit.image_sha256 == digest else None)

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.condition.notify_all()
        if self.refinement:
            self.refinement.thread.join(timeout=2)
        self.worker.join(timeout=0.1)

    def _worker(self):
        try:
            self._run()
        finally:
            close = getattr(self.renderer, 'close', None)
            if callable(close):
                try:
                    close()  # Release contexts on their creating thread.
                except Exception as exc:
                    with self.condition:
                        self.foreground_cleanup_error = type(exc).__name__
                        self.condition.notify_all()

    def _replay_unavailable(self, reason, frame):
        """Called under condition; identity/parity failures revoke old pixels."""
        self.stateful = {**self.stateful, 'phase': 'unavailable',
                         'requested_frame': frame, 'last_reason': reason}
        if reason in {'replay_mismatch', 'runtime_identity_changed', 'input_identity_changed'}:
            if self.replay_revoked is None:
                self.replay_revoked = reason
                self.pending = None
                if self.refinement:
                    self.refinement.suspend()
                self.cache.invalidate_all()
                self.invalidation = getattr(self.cache, 'last_revision', None)
                if self.invalidation is not None:
                    self._event('cache-invalidated', invalidation=self.invalidation)
        self._event('stateful-seek-unavailable', frame=frame, reason=reason, stateful=self.stateful)

    def _bind_replay_identity(self, renderer):
        """One engine/device identity for both producers in a qualification."""
        if not getattr(renderer, 'stateful_replay', False):
            return
        status = getattr(renderer, 'stateful_status', {})
        identity = status.get('engine_identity_sha256')
        if (not isinstance(identity, str) or len(identity) != 64
                or any(c not in '0123456789abcdef' for c in identity)):
            raise StatefulSeekUnavailable('invalid_replay_result')
        if self.replay_engine_identity is not None and identity != self.replay_engine_identity:
            raise StatefulSeekUnavailable('runtime_identity_changed')
        self.replay_engine_identity = identity

    def _release_foreground(self):
        """Called only by the foreground owner, including a cached pause."""
        release = getattr(self.renderer, 'release_compositor', None)
        try:
            if callable(release):
                release()
        except Exception as exc:
            from .preview_replay import StatefulSeekUnavailable

            with self.condition:
                if isinstance(exc, StatefulSeekUnavailable) and exc.reason_code in {
                        'input_identity_changed', 'runtime_identity_changed', 'replay_mismatch'}:
                    self._replay_unavailable(exc.reason_code, self.frame)
                else:
                    self.foreground_cleanup_error = type(exc).__name__
                    self._event('render-error', frame=self.frame, error_type='ForegroundCleanupError')
        finally:
            with self.condition:
                self.foreground_release_pending = False
                self.condition.notify_all()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None
                                        or self.foreground_release_pending)
                if self.closed:
                    return
                release_only = self.pending is None
                if release_only:
                    self.foreground_active = True
            if release_only:
                self._release_foreground()
                with self.condition:
                    self.foreground_active = False
                    self.condition.notify_all()
                continue
            with self.condition:
                # Coalesce again after releasing the lock; a newer command may
                # have replaced or cancelled the request before native work.
                if self.pending is None:
                    continue
                frame, sequence, queued, epoch = self.pending
                profile_id = self.controller.profile_id if self.controller else 'Q0'
                self.pending = None
                self.foreground_active = True
                self.condition.notify_all()
                self.condition.wait_for(lambda: self.closed or self.refinement is None
                                        or self.refinement.quiescent
                                        or self.refinement.state == 'cleanup_failed')
                if self.foreground_cleanup_error or (self.refinement and self.refinement.state == 'cleanup_failed'):
                    self.foreground_active = False
                    self._event('render-error', frame=frame, error_type='RefinementCleanupError')
                    self.condition.notify_all()
                    continue
                if self.closed:
                    self.foreground_active = False
                    self.condition.notify_all()
                    return
                if self.replay_revoked is not None:
                    self.foreground_active = False
                    self._replay_unavailable(self.replay_revoked, self.frame)
                    self.condition.notify_all()
                    continue
            started = self.clock()
            try:
                ticket = self.cache.ticket(frame, profile_id)
                if self.controller:
                    cancellable = getattr(self.renderer, 'render_cancellable', None)
                    if callable(cancellable):
                        def cancelled():
                            with self.condition:
                                return (self.closed or self.cursor_seq != sequence or self.frame != frame
                                        or self.replay_revoked is not None)
                        def progress(status):
                            with self.condition:
                                if not cancelled():
                                    self.stateful = dict(status)
                                    self._event('stateful-progress', frame=frame, stateful=self.stateful)
                        image = cancellable(frame, profile_id, cancelled, progress)
                    else:
                        image = self.renderer(frame, profile_id)
                    _verified_png(image, ticket.width, ticket.height)  # Never resize to fake a rank.
                else:
                    image = q0_png(self.renderer(frame), (ticket.width, ticket.height))
                with self.condition:
                    if (self.closed or self.cursor_seq != sequence or self.frame != frame
                            or self.replay_revoked is not None):
                        self.observations.counters['stale_results'] += 1
                        continue
                    self._verify_inputs()
                    self._bind_replay_identity(self.renderer)
                    outcome = self.cache.publish(ticket, image, settings_sha256=self._settings(ticket))
                    duration_ms, queue_ms = max(0, (self.clock()-started)*1000), max(0, (started-queued)*1000)
                    if outcome in ('stale', 'quota_rejected'):
                        self._event('overload', frame=frame, reason=outcome)
                        self.observations.counters['stale_results'] += int(outcome == 'stale')
                        continue
                    self.observations.rendered(render_ms=duration_ms, queue_ms=queue_ms)
                    decision = (self.controller.observe(duration_ms, queue_ms=queue_ms,
                                setup_ms=min(duration_ms, max(0, getattr(self.renderer, "last_setup_ms", 0))),
                                epoch=epoch, profile_id=profile_id)
                                if self.controller and not getattr(self.renderer, 'stateful_replay', False) else None)
                    if decision:
                        self._event('quality-changed', frame=frame, **decision)
                    cached = self.cache.get(frame)
                    if cached is not None:
                        self._event('frame-ready', **self._frame_data(cached, hit=False))
                    else:
                        self._event('overload', frame=frame, reason='cache_evicted')
                    if self.telemetry:
                        self.telemetry.record_render(render_ms=duration_ms, queue_ms=queue_ms)
            except Exception as exc:
                from .preview_refinement_process import RefinementCancelled
                from .preview_replay import StatefulSeekUnavailable
                with self.condition:
                    if getattr(self.renderer, 'cleanup_error', False):
                        self.foreground_cleanup_error = 'ReplayCleanupError'
                    if (not self.closed and isinstance(exc, StatefulSeekUnavailable)
                            and exc.reason_code in {'replay_mismatch', 'runtime_identity_changed', 'input_identity_changed'}):
                        self._replay_unavailable(exc.reason_code, self.frame)
                        continue
                    if not self.closed and self.cursor_seq == sequence:
                        if isinstance(exc, RefinementCancelled):
                            continue
                        if isinstance(exc, StatefulSeekUnavailable):
                            self._replay_unavailable(exc.reason_code, frame)
                            continue
                        if getattr(self.renderer, 'stateful_replay', False):
                            self._replay_unavailable('native_cleanup_failed' if self.foreground_cleanup_error
                                                     else 'worker_failed', frame)
                            continue
                        decision = (self.controller.allocation_failure(epoch=epoch, profile_id=profile_id)
                                    if self.controller and isinstance(exc, MemoryError) else None)
                        if decision:
                            self._event('quality-changed', frame=frame, **decision)
                            self.pending = (frame, sequence, self.clock(), self.controller.epoch)
                            self.condition.notify_all()
                        else:
                            self._event('render-error', frame=frame, error_type=type(exc).__name__)
                        if self.telemetry:
                            self.telemetry.record_rejection(failure=True)
            finally:
                try:
                    with self.condition:
                        release = self.refinement is not None and not self.playing
                    if release:
                        self._release_foreground()
                finally:
                    with self.condition:
                        self.foreground_active = False
                        self.condition.notify_all()


class PreviewStreamAPI:
    """One active revision, bound to the preview server's validated project."""

    def __init__(self, project_file, validate_draft, render_one, *, telemetry=None,
                 cache_root=None, project_loader=None, adaptive_renderer_factory=None, refinement_factory=None,
                 stateful_renderer_factory=None, stateful_guard_factory=None,
                 acknowledgement_source='client_decode_ack', cache_limits=None, graph_validator=None):
        self.path = Path(project_file).resolve()
        self.validate = validate_draft
        self.render = render_one
        self.telemetry = telemetry
        self.loader = project_loader
        self.adaptive_renderer_factory = adaptive_renderer_factory
        self.refinement_factory = refinement_factory
        self.stateful_renderer_factory = stateful_renderer_factory
        self.stateful_guard_factory = stateful_guard_factory
        self.graph_validator = graph_validator
        if acknowledgement_source not in ('client_decode_ack', 'harness_png_decode'):
            raise ValueError('unsupported acknowledgement source')
        self.acknowledgement_source = acknowledgement_source
        self.cache_limits = dict(max_bytes=512*1024**2, min_free_bytes=16*1024**2, ram_bytes=16*1024**2)
        if cache_limits is not None:
            if not isinstance(cache_limits, dict) or set(cache_limits) - set(self.cache_limits):
                raise ValueError('unknown preview cache limits')
            for key, value in cache_limits.items():
                self.cache_limits[key] = _integer(value, key, 1 if key == 'max_bytes' else 0, 64*1024**3)
        self.root = Path(cache_root) if cache_root else (Path.home()/'.cache'/'framerudiments'/'stream-s2')
        self.lock = RLock()
        self.active = None
        self._open_epoch = 0
        self._clients = OrderedDict()
        self._cancelled_jobs = 0

    def _start_intent(self, body):
        """Fence reordered draft-start requests before any slow source validation.

        Client IDs are opaque per-tab tokens, never cache paths or authorities.
        Retrying the same revision is allowed only for the same render snapshot.
        All clients additionally share the server's single latest-intent epoch.
        """
        client, revision = body.get('client_id'), body.get('edit_sequence')
        if client is not None or revision is not None:
            if not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', client):
                raise ValueError('invalid stream edit client')
            _integer(revision, 'edit sequence', 1, _MAX_SEQ)
        snapshot = canonical_digest({k: body.get(k) for k in ('project', 'sidecars', 'adaptive', 'refine')})
        with self.lock:
            if client is not None:
                previous = self._clients.get(client)
                if previous and (revision < previous[0] or (revision == previous[0] and snapshot != previous[1])):
                    raise ValueError('superseded or conflicting draft revision')
                if client not in self._clients and len(self._clients) >= 64:
                    # Do not evict anti-replay state and later accept an old tab's
                    # revision as new. Reopening the local server resets the bound.
                    raise ValueError('too many edit clients; restart the preview server')
                self._clients[client] = (revision, snapshot)
            self._open_epoch += 1
            return self._open_epoch

    def open(self, body):
        allowed = {'project', 'sidecars', 'frame', 'target_fps', 'adaptive', 'refine',
                   'client_id', 'edit_sequence'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError('invalid stream start')
        intent = self._start_intent(body)
        if self.loader:
            original = self.loader(self.path)
        else:
            from .project import Project
            original = Project.load(self.path)
        original.verify_inputs()
        draft = body.get('project')
        self.validate(original.data, draft)
        sidecars = body.get('sidecars', {})
        if (not isinstance(sidecars, dict) or set(sidecars)-{'lines','words','cues'}
                or any(not isinstance(v, list) or len(v)>100000 for v in sidecars.values())):
            raise ValueError('invalid sidecars')
        if self.graph_validator is not None:
            # Production authoring validates the exact project/sidecar snapshot
            # before any cache reuse or worker can bypass semantic admission.
            self.graph_validator(self.path, draft, sidecars)
        frame = body.get('frame', 0)
        _integer(frame, 'frame', 0, original.frame_count-1)
        target = requested_target_fps(original, body.get('target_fps'))
        adaptive = body.get('adaptive', False)
        if type(adaptive) is not bool:
            raise ValueError('adaptive must be boolean')
        refine = body.get('refine', False)
        if type(refine) is not bool or (refine and not adaptive):
            raise ValueError('refine must be boolean and requires adaptive mode')
        candidate = copy(original)
        candidate.data = deepcopy(draft)
        if adaptive and draft.get('render_quality') != original.data.get('render_quality'):
            from .output_quality import OutputQuality
            candidate.output_quality = OutputQuality(draft.get('render_quality'))
        capabilities = profile_capabilities(candidate, allow_stateful=True) if adaptive else None
        if capabilities is not None and not capabilities[5]['supported']:
            raise ValueError(capabilities[5]['reason'])
        abi = ADAPTIVE_ABI if adaptive else RENDERER_ABI
        stateful = seek_status(draft) if adaptive else seek_status({})
        feedback = adaptive and stateful['mode'] == 'verified_prefix_replay'
        project_hash = project_fingerprint(original, renderer_abi=abi)
        pipeline = canonical_digest(dict(renderer_abi=abi, draft=draft, sidecars=sidecars))
        guard = (PinnedInputGuard(getattr(original, 'base', self.path.parent), draft,
                                  getattr(original, 'inputs', ())) if adaptive else None)
        if feedback:
            from .preview_stateful_renderer import ExternalRuntimeGuard
            runtime_guard = (self.stateful_guard_factory or ExternalRuntimeGuard)(draft)
            input_guard = guard
            def guard():
                input_guard()
                runtime_guard()
        namespace = cache_namespace(self.path)
        implementation = implementation_fingerprint() if adaptive else None
        if feedback:
            # An engine without serializable verified state must qualify again
            # on a new stream/device. Retaining old pixels across that boundary
            # would silently bypass S6 reset verification (notably projectM).
            implementation = canonical_digest({'implementation': implementation,
                'runtime': runtime_guard.digest, 'qualification': token_urlsafe(32)})
        manifest = (build_dependency_index(candidate, draft, sidecars,
                    namespace=namespace, renderer_abi=abi, reach=temporal_reach(draft),
                    implementation=implementation, capabilities=capabilities)
                    if adaptive else None)
        draft, sidecars = deepcopy(draft), deepcopy(sidecars)
        def renderer(n):
            return self.render(self.path, draft, n, quality='fast', sidecars=sidecars)
        with self.lock:
            if intent != self._open_epoch:
                raise ValueError('superseded draft admission')
            if self.active:
                with self.active.condition:
                    if not self.active.closed:
                        self._cancelled_jobs += int(self.active.pending is not None)
                        self._cancelled_jobs += int(self.active.foreground_active)
                        self._cancelled_jobs += int(bool(self.active.refinement and self.active.refinement.active))
                self.active.close()
                if not self.active.retired:
                    raise RuntimeError('Previous render is retiring; retry stream start')
            limits = self.cache_limits
            profiles = quality_profiles(candidate if adaptive else original)
            if adaptive:
                # Stable per-workspace storage; per-section closures, not a new
                # directory for every edited draft, own cache validity.
                previous_cache = self.active.cache if self.active else None
                if isinstance(previous_cache, SectionFrameCache) and previous_cache.project_hash == manifest.namespace:
                    cache = previous_cache
                    cache.revise(manifest, renderer_abi=abi, profiles=profiles)
                else:
                    cache = SectionFrameCache(self.root/'section-cache-v1'/manifest.namespace,
                                manifest=manifest, renderer_abi=abi, profiles=profiles, **limits)
            else:
                cache = PreviewFrameCache(self.root/project_hash/pipeline,
                            project_fingerprint=project_hash, pipeline_fingerprint=pipeline,
                            renderer_abi=abi, segments=section_segments(original, project_hash=project_hash),
                            profiles=profiles, **limits)
            if adaptive:
                if feedback:
                    from .preview_refinement_process import RefinementSpec
                    from .preview_stateful_process import SpawnStatefulRenderer
                    spec = RefinementSpec.from_snapshot(self.path, original.data, draft, sidecars,
                                                        cache.root/'sessions')
                    renderer = (self.stateful_renderer_factory or SpawnStatefulRenderer)(spec)
                else:
                    from .preview_native_renderer import NativePreviewRenderer
                    factory = self.adaptive_renderer_factory or NativePreviewRenderer
                    renderer = factory(self.path, draft, sidecars, validate_draft=self.validate,
                                       temp_parent=cache.root/'sessions', verifier=guard)
            refinement_factory = None
            if refine:
                from .preview_refinement_process import RefinementSpec, SpawnQ5Renderer
                spec = RefinementSpec.from_snapshot(self.path, original.data, draft, sidecars,
                                                    cache.root/'sessions')
                if feedback:
                    from .preview_stateful_process import SpawnStatefulRenderer
                    factory = self.stateful_renderer_factory or SpawnStatefulRenderer
                else:
                    factory = self.refinement_factory or SpawnQ5Renderer
                def refinement_factory():
                    return factory(spec)
            stream = SeekStream(cache, renderer, original.frame_count, target_fps=max(15,target),
                                telemetry=self.telemetry, capabilities=capabilities, verifier=guard,
                                refinement_factory=refinement_factory, stateful=stateful,
                                acknowledgement_source=self.acknowledgement_source,
                                qualification_identity=dict(project_sha256=project_hash,
                                    pipeline_sha256=pipeline, implementation_sha256=implementation))
            self.active = stream
            if stream.invalidation is not None:
                stream.invalidation = {**stream.invalidation, 'cancelled_jobs': self._cancelled_jobs}
                if feedback:
                    stream.invalidation['qualification_reset'] = True
                self._cancelled_jobs = 0
                with stream.condition:
                    stream._event('cache-invalidated', invalidation=stream.invalidation)
            try:
                return stream.command('seek', sequence=0, frame=frame)
            except BaseException:
                stream.close()
                raise

    def get(self, token):
        with self.lock:
            if not isinstance(token, str) or not self.active or self.active.token != token:
                raise ValueError('invalid stream session')
            return self.active

    def command(self, body):
        if not isinstance(body, dict) or set(body)-{'session','command','sequence','frame','target_fps'}:
            raise ValueError('invalid stream control')
        return self.get(body.get('session')).command(body.get('command'),
                  sequence=body.get('sequence'), frame=body.get('frame'),
                  target_fps=body.get('target_fps'))

    def close(self, token):
        with self.lock:
            if not self.active or self.active.token != token:
                return dict(closed=False)
            self.active.close()
            # Retain a retiring native worker so open() cannot stack another.
            return dict(closed=True)

    def shutdown(self):
        with self.lock:
            if self.active:
                self.active.close()
                self.active = None


def _get_one(query, key):
    rows = query.get(key, [])
    if len(rows)!=1:
        raise ValueError('missing or repeated stream query field')
    return rows[0]


def _uint(value, maximum=_MAX_SEQ):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value)>20:
        raise ValueError('invalid stream integer')
    result = int(value)
    if result>maximum:
        raise ValueError('stream integer exceeds range')
    return result


def get_route(handler):
    """Handle only S2 paths, leaving all existing GET routes untouched."""
    target = urlsplit(handler.path).path
    if target not in ('/api/stream/events','/api/stream/frame','/api/stream/status','/api/stream/qualification'):
        return False
    try:
        if handler.headers.get('Origin') and not handler._origin_ok():
            handler._json(403, {'error':'same-origin only'})
            return True
        if len(urlsplit(handler.path).query)>512:
            raise ValueError('query too long')
        q = parse_qs(urlsplit(handler.path).query, max_num_fields=5, keep_blank_values=True)
        stream = handler.server.stream_api.get(_get_one(q,'session'))
        if target in ('/api/stream/status', '/api/stream/qualification'):
            if set(q) != {'session'}:
                raise ValueError('invalid status query')
            with stream.condition:
                if target == '/api/stream/qualification':
                    if stream.closed:
                        raise ValueError('stream closed')
                    handler._json(200, dict(session=stream.token, cursor_seq=stream.cursor_seq,
                                           frame=stream.frame, qualification=stream.qualification()))
                else:
                    handler._json(200,stream.status(refresh_qualification=True))
            return True
        if target == '/api/stream/frame':
            if set(q) != {'session','frame','generation','sha256'}:
                raise ValueError('invalid frame query')
            image = stream.image(_uint(_get_one(q,'frame'),stream.frame_count-1),
                                 _uint(_get_one(q,'generation'),2**63-1),_get_one(q,'sha256'))
            if image is None:
                handler._json(410, {'error':'expired or invalidated frame'})
            else:
                handler._send(200,image,'image/png')
            return True
        if set(q)-{'session','after','continuous'}:
            raise ValueError('invalid SSE query')
        last = _uint(_get_one(q,'after')) if 'after' in q else 0
        header = handler.headers.get('Last-Event-ID')
        if header:
            last = max(last,_uint(header))
        if 'continuous' in q:
            if _get_one(q, 'continuous') != '1':
                raise ValueError('invalid continuous SSE mode')
            return _continuous_events(handler, stream, last)
        rows = stream.since(last, timeout=3)
        lines = ['retry: 250\n']
        for row in rows:
            lines.append('id: %d\nevent: %s\ndata: %s\n\n' % (
                row['id'],row['type'],json.dumps(row,separators=(',',':'))))
        handler._send(200,(''.join(lines)+': idle\n\n').encode(),'text/event-stream; charset=utf-8')
        return True
    except (ValueError, OSError) as exc:
        handler._json(400, {'error':str(exc)[:160]})
        return True


def post_route(handler):
    target = urlsplit(handler.path).path
    if target not in ('/api/stream/start','/api/stream/control','/api/stream/close','/api/stream/presented'):
        return False
    if not handler._origin_ok():
        handler._json(403, {'error':'same-origin only'})
        return True
    try:
        size = _uint(handler.headers.get('Content-Length',''), 2*1024*1024)
        if size < 1:
            raise ValueError('empty stream command')
        data = json.loads(handler.rfile.read(size))
        if target == '/api/stream/start':
            reply = handler.server.stream_api.open(data)
        elif target == '/api/stream/control':
            reply = handler.server.stream_api.command(data)
        elif target == '/api/stream/presented':
            if not isinstance(data, dict) or set(data) != {'session','sequence','frame','sha256'}:
                raise ValueError('invalid presentation receipt')
            active = handler.server.stream_api.get(data['session'])
            reply = {'acknowledged': active.acknowledge(data['sequence'], data['frame'], data['sha256'])}
        else:
            if not isinstance(data, dict) or set(data)!={'session'}:
                raise ValueError('stream close requires session')
            reply = handler.server.stream_api.close(data['session'])
        handler._json(200,reply)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        handler._json(400, {'error':str(exc)[:160]})
    return True


def _continuous_events(handler, stream, last):
    """Bounded persistent SSE: no 250ms reconnect delay on every delivered frame.

    Old S2 clients retain finite polling responses. S3 requests continuous=1;
    at most two subscribers/session, a 20s connection lease and 5s write timeout.
    """
    if not stream.event_slots.acquire(blocking=False):
        handler._json(429, {'error': 'stream subscriber limit'})
        return True
    previous_timeout = handler.connection.gettimeout()
    try:
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('X-Accel-Buffering', 'no')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        handler.close_connection = True
        handler.connection.settimeout(5)
        handler.wfile.write(b'retry: 50\n\n')
        handler.wfile.flush()
        deadline = monotonic() + 20
        while monotonic() < deadline:
            rows = stream.since(last, timeout=1)
            payload = []
            for row in rows:
                payload.append('id: %d\nevent: %s\ndata: %s\n\n' % (
                    row['id'], row['type'], json.dumps(row, separators=(',', ':'))))
                last = row['id']
            handler.wfile.write((''.join(payload) or ': heartbeat\n\n').encode('utf-8'))
            handler.wfile.flush()
            if stream.closed:
                break
    except (OSError, TimeoutError):
        pass  # A disconnected reader does not cancel rendering or write a second HTTP status.
    finally:
        try:
            handler.connection.settimeout(previous_timeout)
        except OSError:
            pass
        finally:
            stream.event_slots.release()
    return True
