"""S6 bounded, verified sequential reconstruction; no opaque state checkpoints.

The receipt cache contains only hashes of fully verified prefixes. It is not a
saved engine state: recreating a native pipeline always starts at frame zero.
One live pipeline and one final result are retained, independently of the PNG
cache. A newly extended prefix is committed only after an independent reset and
an exact comparison of every frame in that prefix.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


class StatefulSeekUnavailable(RuntimeError):
    """A content-free diagnostic suitable for the local preview protocol."""

    code = 'stateful_seek_unavailable'

    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__(self.code + ': ' + reason_code)


class ReplayCancelled(RuntimeError):
    """A newer playback intent superseded this reconstruction."""


@dataclass(frozen=True)
class ReplayReceipt:
    frame: int
    pixels_sha256: str
    prefix_sha256: str


def _digest(value):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError('a replay digest function is required for non-byte results')
    return sha256(value).hexdigest()


def _hex(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


class VerifiedPrefixReplay:
    """Own a frame-zero pipeline and a bounded, identity-bound receipt prefix.

    ``factory()`` returns an object with ``identity``, ``render(frame)`` and
    ``close()``. Each factory call must allocate a fresh authoritative reset.
    ``digest`` hashes the actual RGBA8 result, not a claimed backend checksum.
    All methods are called on the renderer's owning thread.
    """

    def __init__(self, factory, identity, *, digest=_digest,
                 max_prefix_frames=1800, max_request_frames=3600):
        if not callable(factory) or not callable(digest) or not _hex(identity):
            raise ValueError('a factory, digest and immutable SHA-256 identity are required')
        for value, maximum in ((max_prefix_frames, 36000), (max_request_frames, 72000)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('invalid bounded replay limit')
        self.factory, self.identity, self.digest = factory, identity, digest
        self.max_prefix_frames, self.max_request_frames = max_prefix_frames, max_request_frames
        self.receipts = ()
        self.pipeline = None
        self.live_next_frame = 0
        self._last_result = None
        self._sticky_reason = None
        self._closed = False
        self.phase = 'idle'
        self.requested_frame = None
        self.last_reason = None
        self.current_request_frames = 0
        self.replayed_frames = 0
        self.resets = 0
        self.verification_rounds = 0

    def snapshot(self):
        return {
            'enabled': True, 'mode': 'verified_prefix_replay',
            'phase': self.phase, 'requested_frame': self.requested_frame,
            'verified_prefix_frames': len(self.receipts),
            'live_next_frame': self.live_next_frame if self.pipeline is not None else 0,
            'current_request_frames': self.current_request_frames,
            'replayed_frames': self.replayed_frames,
            'max_prefix_frames': self.max_prefix_frames,
            'max_request_frames': self.max_request_frames,
            'resets': self.resets, 'verification_rounds': self.verification_rounds,
            'receipt_bytes': len(self.receipts) * 64,
            'state_storage': 'one_owned_live_pipeline_and_hash_receipts',
            'opaque_checkpoints': False, 'tolerance_rgba8': 0,
            'last_reason': self.last_reason,
        }

    def _unavailable(self, reason, *, sticky=False):
        self.phase, self.last_reason = 'unavailable', reason
        if sticky:
            self._sticky_reason = reason
            self.receipts = ()
        raise StatefulSeekUnavailable(reason)

    def _release_native(self):
        self._last_result = None
        if self.pipeline is not None:
            try:
                self.pipeline.close()
            except Exception:
                # Keep the object reachable. Replacement is forbidden while its
                # owned resources have not been proved closed.
                self._unavailable('native_cleanup_failed', sticky=True)
            self.pipeline = None
        self.live_next_frame = 0

    def release(self):
        """Yield native resources while retaining only verified hash receipts."""
        self._release_native()

    def invalidate(self, reason):
        """Revoke receipts after the adapter detects a changed external input."""
        self.receipts = ()
        self._sticky_reason = self.last_reason = reason
        self.phase = 'unavailable'
        self._release_native()

    def close(self):
        self._release_native()
        self._closed = True
        self.receipts = ()

    def _start(self, cancelled):
        self._check_cancelled(cancelled)
        if self.pipeline is None:
            try:
                self.pipeline = self.factory()
            except StatefulSeekUnavailable:
                raise
            except Exception:
                self._unavailable('native_runtime_unavailable')
            self.live_next_frame = 0
            self.resets += 1
            if getattr(self.pipeline, 'identity', None) != self.identity:
                self._unavailable('runtime_identity_changed', sticky=True)
        self._check_cancelled(cancelled)

    @staticmethod
    def _check_cancelled(cancelled):
        if cancelled():
            raise ReplayCancelled('superseded replay')

    def _step(self, frame, cancelled, progress):
        self._check_cancelled(cancelled)
        if self.current_request_frames >= self.max_request_frames:
            self._unavailable('replay_budget_exceeded')
        if frame != self.live_next_frame:
            self._unavailable('nonsequential_replay', sticky=True)
        self.current_request_frames += 1
        self.replayed_frames += 1
        result = self.pipeline.render(frame)
        self.live_next_frame = frame + 1
        self._check_cancelled(cancelled)
        digest = self.digest(result)
        if not _hex(digest):
            self._unavailable('invalid_replay_result', sticky=True)
        if progress is not None:
            progress(self.snapshot())
        return result, digest

    def _receipt(self, frame, digest, previous):
        seed = self.identity if previous is None else previous.prefix_sha256
        prefix = sha256((seed + ':' + str(frame) + ':' + digest).encode('ascii')).hexdigest()
        return ReplayReceipt(frame, digest, prefix)

    def render(self, frame, *, cancelled=lambda: False, progress=None):
        if type(frame) is not int or frame < 0:
            raise ValueError('absolute replay frame must be a nonnegative integer')
        if not callable(cancelled) or (progress is not None and not callable(progress)):
            raise ValueError('replay callbacks must be callable')
        self.requested_frame, self.current_request_frames, self.last_reason = frame, 0, None
        try:
            if self._closed:
                self._unavailable('renderer_closed')
            if self._sticky_reason:
                self._unavailable(self._sticky_reason)
            if frame >= self.max_prefix_frames:
                self._unavailable('prefix_limit_exceeded')
            self._check_cancelled(cancelled)
            known = len(self.receipts)
            if frame < known:
                # Repeated exact-frame demand can reuse the one already verified
                # live result. A backwards seek must reset, never rewind state.
                if self.pipeline is not None and self.live_next_frame == frame + 1 and self._last_result is not None:
                    result = self._last_result
                else:
                    if self.pipeline is not None and self.live_next_frame > frame:
                        self._release_native()
                    self.phase = 'verifying'
                    self._start(cancelled)
                    required = frame + 1 - self.live_next_frame
                    if required > self.max_request_frames:
                        self._unavailable('replay_budget_exceeded')
                    result = None
                    for n in range(self.live_next_frame, frame + 1):
                        result, digest = self._step(n, cancelled, progress)
                        if digest != self.receipts[n].pixels_sha256:
                            self._unavailable('replay_mismatch', sticky=True)
            else:
                # Candidate receipts remain private until the entire independent
                # second pass agrees. No partial prefix survives cancellation.
                self.phase = 'replaying'
                self._start(cancelled)
                required = (frame + 1 - self.live_next_frame) + frame + 1
                if required > self.max_request_frames:
                    self._unavailable('replay_budget_exceeded')
                candidate = list(self.receipts)
                for n in range(self.live_next_frame, frame + 1):
                    _, digest = self._step(n, cancelled, progress)
                    if n < known:
                        if digest != candidate[n].pixels_sha256:
                            self._unavailable('replay_mismatch', sticky=True)
                    else:
                        candidate.append(self._receipt(n, digest, candidate[-1] if candidate else None))
                self._release_native()
                self.phase = 'verifying'
                self._start(cancelled)
                result = None
                for n in range(frame + 1):
                    result, digest = self._step(n, cancelled, progress)
                    if digest != candidate[n].pixels_sha256:
                        self._unavailable('replay_mismatch', sticky=True)
                self._check_cancelled(cancelled)
                self.receipts = tuple(candidate)
                self.verification_rounds += 1
            self._last_result = result
            self.phase = 'ready'
            if progress is not None:
                progress(self.snapshot())
            return result
        except ReplayCancelled:
            self._release_native()
            self.phase, self.last_reason = 'cancelled', None
            if progress is not None:
                progress(self.snapshot())
            raise
        except StatefulSeekUnavailable as exc:
            self._release_native()
            self.phase, self.last_reason = 'unavailable', exc.reason_code
            if progress is not None:
                progress(self.snapshot())
            raise
        except Exception:
            self._release_native()
            self.phase, self.last_reason = 'unavailable', 'native_render_failed'
            if progress is not None:
                progress(self.snapshot())
            raise StatefulSeekUnavailable('native_render_failed') from None
