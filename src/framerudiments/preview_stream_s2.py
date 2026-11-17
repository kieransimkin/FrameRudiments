"""S2: local, opt-in, cache-backed seekable preview transport.

One foreground render thread, one replaceable pending job. Q0 is a truthful
quarter-size downsample of the existing fast preview, NOT an adaptive or
production-equivalent render. The existing audio clock remains authoritative.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from threading import Condition, RLock, Thread
from time import monotonic, perf_counter
from urllib.parse import parse_qs, urlencode, urlsplit
import json
import re

from PIL import Image, UnidentifiedImageError

from .preview_frame_cache import PreviewFrameCache
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
    """Fenced cursor intents, cached readback and bounded SSE event history."""

    def __init__(self, cache, renderer, frame_count, target_fps=30, telemetry=None):
        _integer(frame_count, 'frame_count', 1, 10_000_000)
        _integer(target_fps, 'target_fps', 15, 120)
        self.cache, self.renderer = cache, renderer
        self.frame_count, self.target_fps = frame_count, target_fps
        self.telemetry = telemetry
        self.token = token_urlsafe(32)
        self.condition = Condition()
        self.events = deque(maxlen=128)
        self.event_seq = 0
        self.cursor_seq = -1
        self.frame = 0
        self.last_stop = 0
        self.playing = False
        self.pending = None
        self.closed = False
        self.worker = Thread(target=self._run, name='framerudiments-preview-s2', daemon=True)
        self.worker.start()

    def _event(self, kind, **properties):
        self.event_seq += 1
        self.events.append(dict(schema=SCHEMA, id=self.event_seq, type=kind,
                                cursor_seq=self.cursor_seq, **properties))
        self.condition.notify_all()

    def _frame_data(self, item, *, hit):
        params = urlencode(dict(session=self.token, frame=item.frame,
                                generation=item.generation, sha256=item.image_sha256))
        return dict(frame=item.frame, generation=item.generation,
                    profile_id=item.profile_id, quality_rank=item.quality_rank,
                    width=item.width, height=item.height, cache_hit=hit,
                    production_equivalent=False, image_sha256=item.image_sha256,
                    url='/api/stream/frame?' + params)

    def status(self, state='active'):
        return dict(schema=SCHEMA, session=self.token, state=state,
                    cursor_seq=self.cursor_seq, frame=self.frame,
                    target_fps=self.target_fps, playing=self.playing,
                    stopped_frame=self.last_stop, profile='Q0',
                    adaptive_fps=False, production_equivalent=False)

    def command(self, name, *, sequence, frame=None, target_fps=None):
        if name not in ('seek', 'play', 'pause', 'stop', 'target_fps'):
            raise ValueError('unknown stream command')
        _integer(sequence, 'client sequence', 0, _MAX_SEQ)
        if frame is not None:
            _integer(frame, 'frame', 0, self.frame_count - 1)
        if name in ('seek', 'play') and frame is None:
            raise ValueError('seek and play need absolute frame')
        if name == 'target_fps' and target_fps is None:
            raise ValueError('FPS target is missing')
        if target_fps is not None:
            _integer(target_fps, 'target_fps', 15, 120)
        with self.condition:
            if self.closed:
                raise ValueError('stream closed')
            if sequence <= self.cursor_seq:
                return self.status('out_of_order')
            self.cursor_seq = sequence
            if frame is not None:
                self.frame = frame
            if name == 'play':
                self.playing = True
            elif name in ('pause', 'stop'):
                self.playing = False
                self.last_stop = self.frame
            if target_fps is not None:
                self.target_fps = target_fps
            self.pending = None
            self._event('cursor', frame=self.frame, playing=self.playing)
            if name != 'target_fps':
                cached = self.cache.get(self.frame)
                if cached is not None:
                    self._event('frame-ready', **self._frame_data(cached, hit=True))
                    if self.telemetry:
                        self.telemetry.record_hit()
                else:
                    self.pending = (self.frame, sequence, monotonic())
                    self.condition.notify_all()
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

    def image(self, frame, generation, digest):
        _integer(frame, 'frame', 0, self.frame_count-1)
        _integer(generation, 'generation', 0, 2**63-1)
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid image digest')
        with self.condition:
            if self.closed:
                raise ValueError('stream closed')
            hit = self.cache.get(frame)
            return (hit.png if hit is not None and hit.generation == generation
                    and hit.image_sha256 == digest else None)

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.condition.notify_all()
        # The daemon can finish a native call, but may never publish afterward.
        self.worker.join(timeout=0.1)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                frame, sequence, queued = self.pending
                self.pending = None
            started = perf_counter()
            try:
                ticket = self.cache.ticket(frame, 'Q0')
                image = q0_png(self.renderer(frame), (ticket.width, ticket.height))
                with self.condition:
                    if self.closed or self.cursor_seq != sequence or self.frame != frame:
                        continue
                    outcome = self.cache.publish(ticket, image, settings_sha256=canonical_digest({
                        'renderer_abi': RENDERER_ABI, 'pipeline': ticket.pipeline_fingerprint,
                        'profile': 'Q0'}))
                    if outcome in ('stale', 'quota_rejected'):
                        self._event('overload', frame=frame, reason=outcome)
                    else:
                        cached = self.cache.get(frame)
                        if cached is not None:
                            self._event('frame-ready', **self._frame_data(cached, hit=False))
                    if self.telemetry:
                        self.telemetry.record_render(render_ms=(perf_counter()-started)*1000,
                                                     queue_ms=(started-queued)*1000)
            except Exception as exc:
                with self.condition:
                    if not self.closed and self.cursor_seq == sequence:
                        self._event('render-error', frame=frame, error_type=type(exc).__name__)
                        if self.telemetry:
                            self.telemetry.record_rejection(failure=True)


class PreviewStreamAPI:
    """One active revision, bound to the preview server's validated project."""

    def __init__(self, project_file, validate_draft, render_one, *, telemetry=None,
                 cache_root=None, project_loader=None):
        self.path = Path(project_file).resolve()
        self.validate = validate_draft
        self.render = render_one
        self.telemetry = telemetry
        self.loader = project_loader
        self.root = Path(cache_root) if cache_root else (Path.home()/'.cache'/'framerudiments'/'stream-s2')
        self.lock = RLock()
        self.active = None

    def open(self, body):
        if not isinstance(body, dict) or set(body)-{'project','sidecars','frame','target_fps'}:
            raise ValueError('invalid stream start')
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
        frame = body.get('frame', 0)
        _integer(frame, 'frame', 0, original.frame_count-1)
        target = requested_target_fps(original, body.get('target_fps'))
        project_hash = project_fingerprint(original, renderer_abi=RENDERER_ABI)
        pipeline = canonical_digest(dict(renderer_abi=RENDERER_ABI, draft=draft, sidecars=sidecars))
        cache = PreviewFrameCache(self.root/project_hash/pipeline,
                                  project_fingerprint=project_hash, pipeline_fingerprint=pipeline,
                                  renderer_abi=RENDERER_ABI,
                                  segments=section_segments(original, project_hash=project_hash),
                                  profiles=quality_profiles(original), max_bytes=512*1024**2,
                                  min_free_bytes=16*1024**2, ram_bytes=16*1024**2)
        draft, sidecars = deepcopy(draft), deepcopy(sidecars)
        def renderer(n):
            return self.render(self.path, draft, n, quality='fast', sidecars=sidecars)
        stream = SeekStream(cache, renderer, original.frame_count, target_fps=max(15,target),
                            telemetry=self.telemetry)
        with self.lock:
            prior, self.active = self.active, stream
            if prior:
                prior.close()
            return stream.command('seek', sequence=0, frame=frame)

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
            self.active = None
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
    if target not in ('/api/stream/events','/api/stream/frame','/api/stream/status'):
        return False
    try:
        if handler.headers.get('Origin') and not handler._origin_ok():
            handler._json(403, {'error':'same-origin only'})
            return True
        if len(urlsplit(handler.path).query)>512:
            raise ValueError('query too long')
        q = parse_qs(urlsplit(handler.path).query, max_num_fields=5)
        stream = handler.server.stream_api.get(_get_one(q,'session'))
        if target == '/api/stream/status':
            if set(q) != {'session'}:
                raise ValueError('invalid status query')
            with stream.condition:
                handler._json(200,stream.status())
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
        if set(q)-{'session','after'}:
            raise ValueError('invalid SSE query')
        last = _uint(_get_one(q,'after')) if 'after' in q else 0
        header = handler.headers.get('Last-Event-ID')
        if header:
            last = max(last,_uint(header))
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
    if target not in ('/api/stream/start','/api/stream/control','/api/stream/close'):
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
        else:
            if not isinstance(data, dict) or set(data)!={'session'}:
                raise ValueError('stream close requires session')
            reply = handler.server.stream_api.close(data['session'])
        handler._json(200,reply)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as exc:
        handler._json(400, {'error':str(exc)[:160]})
    return True
