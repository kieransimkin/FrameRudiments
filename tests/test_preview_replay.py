"""S6 exact-prefix proof, bounded work, cancellation and state ownership."""
from dataclasses import FrozenInstanceError
from hashlib import sha256
import random

import pytest

from framerudiments.preview_replay import ReplayCancelled, StatefulSeekUnavailable, VerifiedPrefixReplay

IDENTITY = sha256(b'synthetic sequential engine').hexdigest()


class Engine:
    def __init__(self, record, *, identity=IDENTITY, mismatch=None, fail=None, cleanup=False):
        self.record, self.identity = record, identity
        self.mismatch, self.fail, self.cleanup = mismatch, fail, cleanup
        self.next_frame, self.value, self.closed = 0, 3, False
        self.frames = []
        record.append(self)

    def render(self, frame):
        assert not self.closed and frame == self.next_frame
        self.next_frame += 1
        self.frames.append(frame)
        if frame == self.fail:
            raise RuntimeError('private runtime failure must not reach protocol')
        self.value = (self.value * 13 + frame + 17) % 256
        value = (self.value + int(frame == self.mismatch)) % 256
        return bytes([value]) * 4

    def close(self):
        if self.cleanup:
            raise RuntimeError('private cleanup failure')
        self.closed = True


def expected(last):
    engine = Engine([])
    return [engine.render(n) for n in range(last + 1)]


def test_random_seeks_match_complete_sequential_reference_with_one_live_engine():
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY, max_prefix_frames=50)
    reference = expected(39)
    order = list(range(40))
    random.Random(610).shuffle(order)
    try:
        for frame in [8, 0, 4, 8, 9, 1, *order]:
            assert replay.render(frame) == reference[frame]
            assert sum(not engine.closed for engine in engines) == 1
            assert replay.snapshot()['phase'] == 'ready'
            assert replay.snapshot()['requested_frame'] == frame
            assert all(engine.frames == list(range(len(engine.frames))) for engine in engines)
        assert len(replay.receipts) == 40
        assert replay.snapshot()['receipt_bytes'] == 40 * 64
        assert all(receipt.frame == n for n, receipt in enumerate(replay.receipts))
    finally:
        replay.close()
    assert all(engine.closed for engine in engines)


def test_first_prefix_compares_every_frame_before_receipt_commit():
    engines, progress = [], []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY)
    try:
        assert replay.render(5, progress=progress.append) == expected(5)[5]
        assert [engine.frames for engine in engines] == [list(range(6)), list(range(6))]
        assert replay.snapshot()['current_request_frames'] == 12
        assert all(row['verified_prefix_frames'] == 0 for row in progress[:-1])
        assert progress[-1]['verified_prefix_frames'] == 6
        assert {row['phase'] for row in progress} == {'replaying', 'verifying', 'ready'}
        with pytest.raises(FrozenInstanceError):
            replay.receipts[0].frame = 4
    finally:
        replay.close()


def test_forward_extension_reverifies_full_prefix_but_same_frame_uses_verified_live_result():
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY)
    try:
        replay.render(2)
        before = replay.snapshot()['replayed_frames']
        assert replay.render(2) == expected(2)[2]
        assert replay.snapshot()['replayed_frames'] == before
        replay.render(4)
        assert [engine.frames for engine in engines] == [list(range(3)), list(range(5)), list(range(5))]
        assert replay.snapshot()['current_request_frames'] == 7
        assert replay.snapshot()['verification_rounds'] == 2
    finally:
        replay.close()


def test_backward_seek_and_resource_yield_reconstruct_from_zero_using_receipts():
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY)
    try:
        replay.render(7)
        receipts = replay.receipts
        replay.release()
        assert replay.receipts is receipts
        assert replay.snapshot()['live_next_frame'] == 0
        assert replay.render(4) == expected(4)[4]
        assert engines[-1].frames == list(range(5))
        assert replay.render(1) == expected(1)[1]
        assert engines[-1].frames == [0, 1]
        assert replay.receipts is receipts
    finally:
        replay.close()


@pytest.mark.parametrize('cancel_at', [1, 3, 4, 6])
def test_cancel_at_either_pass_never_commits_partial_receipts(cancel_at):
    engines, seen = [], []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY)
    with pytest.raises(ReplayCancelled):
        replay.render(2, cancelled=lambda: replay.current_request_frames >= cancel_at,
                      progress=seen.append)
    assert replay.receipts == ()
    assert replay.snapshot()['phase'] == 'cancelled'
    assert all(engine.closed for engine in engines)
    assert replay.render(1) == expected(1)[1]
    replay.close()


def test_cancel_extension_preserves_old_proofs_but_discards_live_state():
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY)
    try:
        replay.render(2)
        receipts = replay.receipts
        with pytest.raises(ReplayCancelled):
            replay.render(7, cancelled=lambda: replay.current_request_frames >= 3)
        assert replay.receipts is receipts
        assert replay.pipeline is None
        assert replay.render(2) == expected(2)[2]
    finally:
        replay.close()


def test_mismatch_at_unrequested_earlier_frame_is_sticky_and_revokes_all_receipts():
    engines = []
    replay = VerifiedPrefixReplay(
        lambda: Engine(engines, mismatch=1 if len(engines) == 1 else None), IDENTITY)
    with pytest.raises(StatefulSeekUnavailable) as error:
        replay.render(5)
    assert error.value.code == 'stateful_seek_unavailable'
    assert error.value.reason_code == 'replay_mismatch'
    assert replay.receipts == () and replay.pipeline is None
    assert engines[-1].frames == [0, 1]
    with pytest.raises(StatefulSeekUnavailable, match='replay_mismatch'):
        replay.render(0)
    assert len(engines) == 2
    replay.close()


def test_runtime_identity_change_cannot_reuse_same_pixel_digest():
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines, identity=IDENTITY if not engines else 'b' * 64),
                                  IDENTITY)
    with pytest.raises(StatefulSeekUnavailable, match='runtime_identity_changed'):
        replay.render(1)
    assert replay.receipts == ()
    assert all(engine.closed for engine in engines)


def test_prefix_and_actual_render_budgets_fail_explicitly_and_allow_a_shorter_seek():
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY,
                                  max_prefix_frames=6, max_request_frames=8)
    try:
        with pytest.raises(StatefulSeekUnavailable, match='prefix_limit_exceeded'):
            replay.render(6)
        assert not engines
        with pytest.raises(StatefulSeekUnavailable, match='replay_budget_exceeded'):
            replay.render(4)
        assert all(not engine.frames and engine.closed for engine in engines)
        replay.render(3)
        assert replay.snapshot()['current_request_frames'] == 8
    finally:
        replay.close()


def test_engine_exception_is_sanitized_and_preserves_only_prior_verified_prefix():
    engines = []
    fail = False
    def factory():
        return Engine(engines, fail=5 if fail else None)
    replay = VerifiedPrefixReplay(factory, IDENTITY)
    try:
        replay.render(2)
        receipts = replay.receipts
        replay.release()
        fail = True
        with pytest.raises(StatefulSeekUnavailable) as error:
            replay.render(7)
        assert str(error.value) == 'stateful_seek_unavailable: native_render_failed'
        assert replay.receipts is receipts
        assert replay.pipeline is None
        fail = False
        assert replay.render(1) == expected(1)[1]
    finally:
        replay.close()


def test_failed_cleanup_blocks_replacement_and_remains_reachable_for_retry():
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines, cleanup=True), IDENTITY)
    with pytest.raises(StatefulSeekUnavailable, match='native_cleanup_failed'):
        replay.render(1)
    assert replay.pipeline is engines[0]
    with pytest.raises(StatefulSeekUnavailable, match='native_cleanup_failed'):
        replay.render(0)
    assert len(engines) == 1
    engines[0].cleanup = False
    replay.close()
    assert engines[0].closed


def test_input_revocation_clears_receipts_and_refuses_future_work():
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY)
    replay.render(2)
    replay.invalidate('input_identity_changed')
    assert replay.receipts == () and replay.pipeline is None
    with pytest.raises(StatefulSeekUnavailable, match='input_identity_changed'):
        replay.render(2)
    replay.close()


@pytest.mark.parametrize('frame', [-1, True, 1.1, '3', None])
def test_invalid_frame_does_not_allocate_native_state(frame):
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY)
    with pytest.raises(ValueError):
        replay.render(frame)
    assert not engines


def test_closed_renderer_does_not_reopen():
    engines = []
    replay = VerifiedPrefixReplay(lambda: Engine(engines), IDENTITY)
    replay.close()
    with pytest.raises(StatefulSeekUnavailable, match='renderer_closed'):
        replay.render(0)
    assert not engines
