"""Bounded delayed feedback over immutable bytes, with pinned state checkpoints.

The owner supplies the authoritative frame renderer and identity. This module
retains complete delayed outputs, not opaque GPU/native-engine state. Checkpoint
hashes establish byte integrity against a separately trusted expected checksum;
they do not prove the provenance or correctness of an arbitrary supplied image.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
import json
import struct

SCHEMA = 'framerudiments.delayed-feedback.v1'
MAX_FRAME_COUNT = 10_000_000
MAX_DELAY = 256
MAX_TAPS = 32
MAX_CHECKPOINTS = 64
MAX_CHECKPOINT_INTERVAL = 3600
MAX_REQUEST_FRAMES = 36000
MAX_STATE_BYTES = 512 * 1024**2
MAX_RESULT_BYTES = 64 * 1024**2


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in {low}..{high}')
    return value


def _hex(value):
    return (isinstance(value, str) and len(value) == 64
            and all(char in '0123456789abcdef' for char in value))


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _ceil(value):
    return -(-value.numerator // value.denominator)


class FeedbackCancelled(RuntimeError):
    """The caller superseded a reconstruction before state was committed."""


class FeedbackUnavailable(RuntimeError):
    """A bounded content-free diagnostic for native/preview adapters."""

    def __init__(self, reason_code):
        if not isinstance(reason_code, str) or reason_code not in _REASONS:
            raise ValueError('unknown feedback diagnostic')
        self.reason_code = reason_code
        super().__init__('feedback_unavailable: ' + reason_code)


_REASONS = {'renderer_closed', 'replay_budget_exceeded', 'state_budget_exceeded',
            'invalid_feedback_result', 'invalid_feedback_state', 'native_render_failed',
            'input_identity_changed'}


@dataclass(frozen=True)
class FeedbackCheckpoint:
    """Complete immutable delay-ring state; metadata alone cannot restore it."""

    identity: str
    configuration_sha256: str
    next_frame: int
    reset_frame: int
    result_bytes: int
    history: tuple[tuple[int, bytes], ...]

    def __post_init__(self):
        _validate_checkpoint(self)

    @property
    def payload_sha256(self):
        value = sha256()
        for frame, payload in self.history:
            value.update(struct.pack('>QI', frame, len(payload)))
            value.update(payload)
        return value.hexdigest()

    def _metadata(self):
        return {'schema': SCHEMA + '.checkpoint', 'identity': self.identity,
                'configuration_sha256': self.configuration_sha256,
                'next_frame': self.next_frame, 'reset_frame': self.reset_frame,
                'result_bytes': self.result_bytes,
                'history_frames': [frame for frame, _payload in self.history],
                'payload_sha256': self.payload_sha256}

    @property
    def digest(self):
        return sha256(_json(self._metadata())).hexdigest()

    def document(self):
        return {**self._metadata(), 'sha256': self.digest,
                'payload_bytes': len(self.history) * self.result_bytes,
                'history': [{'frame': frame, 'sha256': sha256(payload).hexdigest()}
                            for frame, payload in self.history],
                'scope': 'trusted_immutable_state_with_separately_pinned_checksum'}


def _validate_checkpoint(checkpoint):
    if type(checkpoint) is not FeedbackCheckpoint:
        raise ValueError('feedback checkpoint must be an immutable FeedbackCheckpoint')
    if not _hex(checkpoint.identity) or not _hex(checkpoint.configuration_sha256):
        raise ValueError('feedback checkpoint requires SHA-256 identities')
    _integer(checkpoint.next_frame, 'checkpoint next frame', 1, MAX_FRAME_COUNT)
    _integer(checkpoint.reset_frame, 'checkpoint reset frame', 0, checkpoint.next_frame - 1)
    _integer(checkpoint.result_bytes, 'checkpoint result bytes', 1, MAX_RESULT_BYTES)
    history = checkpoint.history
    if not isinstance(history, tuple) or not 1 <= len(history) <= MAX_DELAY:
        raise ValueError('feedback checkpoint requires bounded immutable history')
    if len(history) * checkpoint.result_bytes > MAX_STATE_BYTES:
        raise ValueError('feedback checkpoint payload exceeds its byte limit')
    start = checkpoint.next_frame - len(history)
    if start < checkpoint.reset_frame:
        raise ValueError('feedback checkpoint history crosses its reset')
    for index, row in enumerate(history):
        if (not isinstance(row, tuple) or len(row) != 2 or type(row[0]) is not int
                or row[0] != start + index or type(row[1]) is not bytes
                or len(row[1]) != checkpoint.result_bytes):
            raise ValueError('feedback checkpoint history must be contiguous fixed-length bytes')


@dataclass(frozen=True)
class FeedbackPlan:
    """One immutable reconstruction description; no callback executes to plan."""

    start_frame: int
    target_frame: int
    reset_frame: int
    origin: str
    checkpoint_sha256: str | None
    steps: int
    history_refs: tuple[int | None, ...]
    retained_state_bytes: int
    working_bytes: int
    max_state_bytes: int
    max_request_frames: int

    def document(self):
        return {'schema': SCHEMA + '.plan', 'start_frame': self.start_frame,
                'target_frame': self.target_frame, 'reset_frame': self.reset_frame,
                'origin': self.origin, 'checkpoint_sha256': self.checkpoint_sha256,
                'steps': self.steps, 'history_refs': list(self.history_refs),
                'history_rule': 'frame minus each declared delay; missing before exact reset',
                'retained_state_bytes': self.retained_state_bytes,
                'working_bytes': self.working_bytes, 'max_state_bytes': self.max_state_bytes,
                'max_request_frames': self.max_request_frames,
                'scope': 'native_immutable_delayed_results_not_external_engine_state'}


def _payload_bytes(states, extra=()):
    """Count owned immutable payload objects once, including shared checkpoints."""
    values = {id(payload): payload for state in states for _frame, payload in state.history}
    values.update((id(payload), payload) for payload in extra)
    return sum(len(payload) for payload in values.values())


class DelayedFeedback:
    """Reconstruct causal output from exact reset boundaries or owned checkpoints.

    ``evaluate(frame, ordered_history)`` returns immutable ``bytes`` of exactly
    ``result_bytes``. A missing pre-reset tap is ``None``. The owner must treat
    this as a pure deterministic callback and keep source-clock identity pinned.
    Target callbacks always run, so owners can restore target metadata/routes.
    All methods belong to one owning thread; this is not a concurrent worker.
    """

    def __init__(self, evaluate, identity, *, frame_count, result_bytes, delays,
                 repeat_span=None, reset='programme', checkpoint_interval=30,
                 max_checkpoints=8, max_request_frames=3600, max_state_bytes=256 * 1024**2):
        if not callable(evaluate) or not _hex(identity):
            raise ValueError('feedback requires a callback and SHA-256 identity')
        self.frame_count = _integer(frame_count, 'feedback frame count', 1, MAX_FRAME_COUNT)
        self.result_bytes = _integer(result_bytes, 'feedback result bytes', 1, MAX_RESULT_BYTES)
        if not isinstance(delays, tuple) or not 1 <= len(delays) <= MAX_TAPS:
            raise ValueError('feedback delays must be a tuple of 1..32 positive integers')
        self.delays = tuple(_integer(value, 'feedback delay', 1, MAX_DELAY) for value in delays)
        if reset not in ('programme', 'repeat'):
            raise ValueError('feedback reset must be programme or repeat')
        if repeat_span is not None:
            if type(repeat_span) not in (int, Fraction):
                raise ValueError('feedback repeat span must be an exact positive Fraction')
            repeat_span = Fraction(repeat_span)
            if (repeat_span <= 0 or repeat_span > 1_000_000_000
                    or repeat_span.denominator > 1_000_000_000
                    or abs(repeat_span.numerator) > 2**53 - 1):
                raise ValueError('feedback repeat span exceeds exact coordinate bounds')
        if reset == 'repeat' and repeat_span is None:
            raise ValueError('repeat feedback requires an exact repeat span')
        self.checkpoint_interval = _integer(checkpoint_interval, 'checkpoint interval', 1, MAX_CHECKPOINT_INTERVAL)
        self.max_checkpoints = _integer(max_checkpoints, 'checkpoint count', 1, MAX_CHECKPOINTS)
        self.max_request_frames = _integer(max_request_frames, 'reconstruction limit', 1, MAX_REQUEST_FRAMES)
        self.max_state_bytes = _integer(max_state_bytes, 'feedback state byte limit', 1, MAX_STATE_BYTES)
        self.max_delay = max(self.delays)
        self.working_reserve_bytes = (self.max_delay + 1) * self.result_bytes
        self._storage_limit = self.max_state_bytes - self.working_reserve_bytes
        if self._storage_limit < self.max_delay * self.result_bytes:
            raise ValueError('feedback byte limit cannot admit committed and working history')
        self.evaluate, self.identity = evaluate, identity
        self.repeat_span, self.reset = repeat_span, reset
        self.configuration_sha256 = sha256(_json({
            'schema': SCHEMA, 'frame_count': frame_count, 'result_bytes': result_bytes,
            'delays': self.delays, 'reset': reset,
            'repeat_span': None if repeat_span is None else [repeat_span.numerator, repeat_span.denominator],
            'checkpoint_interval': checkpoint_interval, 'max_checkpoints': max_checkpoints,
            'max_request_frames': max_request_frames, 'max_state_bytes': max_state_bytes,
        })).hexdigest()
        self._state = None
        self._state_origin = 'live'
        self._checkpoints = ()
        self._closed = False
        self._rendering = False
        self.phase, self.requested_frame, self.last_reason = 'idle', None, None
        self.last_execution = None
        self.executed_steps = self.completed_requests = self.restores = 0
        self.current_request_frames = self.checkpoint_evictions = 0
        self._last_restore_frame = None

    def _reset_frame(self, frame):
        if self.reset == 'programme':
            return 0
        return _ceil((frame // self.repeat_span) * self.repeat_span)

    def _states(self):
        return self._checkpoints + (() if self._state is None else (self._state,))

    def _select(self, frame):
        reset = self._reset_frame(frame)
        available = [(value, 'checkpoint') for value in self._checkpoints
                     if value.reset_frame == reset and value.next_frame <= frame]
        if self._state is not None and self._state.reset_frame == reset and self._state.next_frame <= frame:
            available.append((self._state, self._state_origin))
        if available:
            state, origin = max(available, key=lambda item: (item[0].next_frame, item[1] == 'live'))
            return reset, state, origin
        return reset, None, 'reset'

    def plan(self, frame):
        if self._closed:
            raise FeedbackUnavailable('renderer_closed')
        _integer(frame, 'feedback frame', 0, self.frame_count - 1)
        reset, state, origin = self._select(frame)
        start = reset if state is None else state.next_frame
        steps = frame + 1 - start
        if steps > self.max_request_frames:
            raise FeedbackUnavailable('replay_budget_exceeded')
        retained = _payload_bytes(self._states())
        if retained + self.working_reserve_bytes > self.max_state_bytes:
            raise FeedbackUnavailable('state_budget_exceeded')
        return FeedbackPlan(start, frame, reset, origin, None if state is None else state.digest,
            steps, tuple(frame - delay if frame - delay >= reset else None for delay in self.delays),
            retained, self.working_reserve_bytes, self.max_state_bytes, self.max_request_frames)

    def _checkpoint(self, next_frame, reset, ring):
        return FeedbackCheckpoint(self.identity, self.configuration_sha256, next_frame, reset,
                                  self.result_bytes, tuple(sorted(ring.items())))

    def checkpoint(self):
        """Return the actual immutable current state, or None before a success."""
        if self._closed:
            raise FeedbackUnavailable('renderer_closed')
        return self._state

    def restore(self, checkpoint, *, expected_sha256):
        """Restore separately trusted pinned state; never trust a self-hash alone."""
        if self._closed:
            raise FeedbackUnavailable('renderer_closed')
        if self._rendering:
            raise FeedbackUnavailable('invalid_feedback_state')
        _validate_checkpoint(checkpoint)
        if not _hex(expected_sha256) or checkpoint.digest != expected_sha256:
            raise ValueError('feedback checkpoint checksum does not match its expected pin')
        if (checkpoint.identity != self.identity or checkpoint.configuration_sha256 != self.configuration_sha256
                or checkpoint.result_bytes != self.result_bytes):
            raise ValueError('feedback checkpoint identity or configuration differs')
        if checkpoint.next_frame > self.frame_count:
            raise ValueError('feedback checkpoint is outside the programme')
        reset = self._reset_frame(checkpoint.next_frame - 1)
        if (checkpoint.reset_frame != reset
                or len(checkpoint.history) != min(self.max_delay, checkpoint.next_frame - reset)):
            raise ValueError('feedback checkpoint does not contain the exact required history')
        if _payload_bytes((checkpoint,)) > self._storage_limit:
            raise ValueError('feedback checkpoint exceeds the retained state byte limit')
        self._state, self._checkpoints = checkpoint, (checkpoint,)
        self._state_origin = 'checkpoint'
        self.phase, self.requested_frame, self.last_reason = 'ready', None, None
        self.last_execution = None
        self.restores += 1
        self._last_restore_frame = checkpoint.next_frame

    @staticmethod
    def _cancel(cancelled):
        if cancelled():
            raise FeedbackCancelled('feedback reconstruction superseded')

    def clear_request(self, frame=None, *, reason_code=None):
        """Retire diagnostics for owner admission, without changing valid state.

        An adapter may admit its native schedule before calling ``render``. A
        failed admission must not expose an earlier request's success receipt.
        The optional reason is a bounded content-free status, never error text.
        """
        if self._rendering:
            raise FeedbackUnavailable('invalid_feedback_state')
        if self._closed:
            raise FeedbackUnavailable('renderer_closed')
        if reason_code is not None and (not isinstance(reason_code, str)
                or reason_code not in _REASONS | {'invalid_request', 'superseded'}):
            raise ValueError('unknown feedback admission diagnostic')
        self.last_execution = None
        self.current_request_frames = 0
        self.requested_frame = frame if type(frame) is int and 0 <= frame < self.frame_count else None
        self.last_reason = reason_code
        self.phase = ('planning' if reason_code is None else
                      'cancelled' if reason_code == 'superseded' else 'unavailable')

    def render(self, frame, *, cancelled=lambda: False, progress=None):
        if self._rendering:
            raise FeedbackUnavailable('invalid_feedback_state')
        self.clear_request(frame)
        self._rendering = True
        ring, pending, history, value = {}, {}, None, None
        state = completed = candidates = committed = None
        try:
            if not callable(cancelled) or (progress is not None and not callable(progress)):
                raise ValueError('feedback cancellation and progress callbacks must be callable')
            plan = self.plan(frame)
            _reset, state, _origin = self._select(frame)
            if state is not None:
                ring.update(state.history)
            self._cancel(cancelled)
            self.phase = 'reconstructing'
            frames = []
            evicted = 0
            committed = self._states()
            peak = _payload_bytes(committed)
            for index in range(plan.start_frame, frame + 1):
                self._cancel(cancelled)
                # Checkpoints staged by this request may be discarded, but the
                # previously committed cache survives any cancellation/failure.
                while pending and (_payload_bytes((*committed, *pending.values()), ring.values())
                                   + self.result_bytes > self.max_state_bytes):
                    del pending[min(pending)]
                    evicted += 1
                if (_payload_bytes((*committed, *pending.values()), ring.values())
                        + self.result_bytes > self.max_state_bytes):
                    raise FeedbackUnavailable('state_budget_exceeded')
                history = tuple(ring.get(index - delay) if index - delay >= plan.reset_frame else None
                                for delay in self.delays)
                if any(index - delay >= plan.reset_frame and item is None
                       for delay, item in zip(self.delays, history)):
                    raise FeedbackUnavailable('invalid_feedback_state')
                self.current_request_frames += 1
                self.executed_steps += 1
                value = self.evaluate(index, history)
                if type(value) is not bytes or len(value) != self.result_bytes:
                    raise FeedbackUnavailable('invalid_feedback_result')
                peak = max(peak, _payload_bytes((*committed, *pending.values()), (*ring.values(), value)))
                ring[index] = value
                ring = {n: payload for n, payload in ring.items() if n > index - self.max_delay}
                history = None
                frames.append(index)
                self._cancel(cancelled)
                if (index + 1 - plan.reset_frame) % self.checkpoint_interval == 0:
                    pending[index + 1] = self._checkpoint(index + 1, plan.reset_frame, ring)
                    while len(pending) > self.max_checkpoints:
                        del pending[min(pending)]
                        evicted += 1
                if progress is not None:
                    progress({**plan.document(), 'phase': 'reconstructing',
                              'executed_steps': len(frames), 'completed': False})
            self._cancel(cancelled)
            completed = self._checkpoint(frame + 1, plan.reset_frame, ring)
            candidates = {item.next_frame: item for item in (*self._checkpoints, *pending.values(), completed)}
            while (len(candidates) > self.max_checkpoints
                   or _payload_bytes((completed, *candidates.values())) > self._storage_limit):
                del candidates[min(candidates)]
                evicted += 1
            receipt = {**plan.document(), 'evaluated_frames': frames, 'executed_steps': len(frames),
                       'peak_owned_payload_bytes': peak, 'completed': True}
            # The final progress observer can still fail or cancel: publish no
            # state until all callbacks have completed successfully.
            if progress is not None:
                progress({**receipt, 'phase': 'ready', 'evaluated_frames': list(frames),
                          'history_refs': list(receipt['history_refs'])})
            self._cancel(cancelled)
            self._state, self._checkpoints = completed, tuple(candidates[n] for n in sorted(candidates))
            self._state_origin = 'live'
            self.last_execution = receipt
            self.completed_requests += 1
            self.checkpoint_evictions += evicted
            self.phase = 'ready'
            return value
        except FeedbackCancelled:
            self.phase, self.last_reason = 'cancelled', 'superseded'
            raise
        except FeedbackUnavailable as exc:
            self.phase, self.last_reason = 'unavailable', exc.reason_code
            raise
        except ValueError:
            self.phase, self.last_reason = 'failed', 'invalid_request'
            raise
        except Exception as exc:
            self.phase, self.last_reason = 'unavailable', 'native_render_failed'
            raise FeedbackUnavailable('native_render_failed') from exc
        except BaseException:
            self.phase, self.last_reason = 'failed', 'reconstruction_failed'
            raise
        finally:
            self._rendering = False
            ring.clear()
            pending.clear()
            if candidates is not None:
                candidates.clear()
            state = completed = candidates = committed = None
            history = value = None
            del history, value, state, completed, candidates, committed

    def snapshot(self):
        return {'schema': SCHEMA, 'identity': self.identity,
                'configuration_sha256': self.configuration_sha256,
                'phase': self.phase, 'requested_frame': self.requested_frame,
                'next_frame': 0 if self._state is None else self._state.next_frame,
                'live_next_frame': 0 if self._state is None else self._state.next_frame,
                'checkpoint_frames': [value.next_frame for value in self._checkpoints],
                'checkpoint_count': len(self._checkpoints),
                'checkpoint_evictions': self.checkpoint_evictions,
                'retained_state_bytes': _payload_bytes(self._states()),
                'working_reserve_bytes': self.working_reserve_bytes,
                'executed_steps': self.executed_steps, 'completed_requests': self.completed_requests,
                'current_request_frames': self.current_request_frames,
                'replayed_frames': self.executed_steps, 'max_request_frames': self.max_request_frames,
                'restored_from_frame': (self.last_execution['start_frame'] if self.last_execution is not None
                                        else self._last_restore_frame),
                'restores': self.restores, 'last_reason': self.last_reason,
                'limits': {'checkpoint_interval': self.checkpoint_interval,
                           'max_checkpoints': self.max_checkpoints,
                           'max_request_frames': self.max_request_frames,
                           'max_state_bytes': self.max_state_bytes},
                'last_execution': None if self.last_execution is None else {
                    **self.last_execution, 'history_refs': list(self.last_execution['history_refs']),
                    'evaluated_frames': list(self.last_execution['evaluated_frames'])},
                'scope': {'opaque_engine_state': False, 'persistent_files': False,
                          'byte_budget': 'owned_immutable_payloads_including_working_ring',
                          'excludes': 'callback_private_scratch_and_external_aliases_and_python_metadata'}}

    def close(self):
        if self._rendering:
            raise FeedbackUnavailable('invalid_feedback_state')
        self._state, self._checkpoints = None, ()
        self.last_execution = None
        self.evaluate = None
        self._closed, self.phase = True, 'closed'
