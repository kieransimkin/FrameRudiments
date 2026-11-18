"""Exact causal recurrence, real checkpoint state, transactionality and bounds."""
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from hashlib import sha256
import json
import random

import pytest

from framerudiments.feedback_history import (
    DelayedFeedback, FeedbackCancelled, FeedbackCheckpoint, FeedbackUnavailable,
)

IDENTITY = sha256(b'independent deterministic feedback fixture').hexdigest()


def sample(frame, history):
    value = (frame * 17 + 3 + sum((index + 1) * item[0]
             for index, item in enumerate(history) if item is not None)) % 251
    return bytes((value, frame % 256, (value + frame) % 256, 255))


def oracle(count, delays, *, span=None, reset='programme'):
    values = []
    for frame in range(count):
        taps = []
        for delay in delays:
            index = frame - delay
            admitted = index >= 0 and (reset == 'programme' or index // span == frame // span)
            taps.append(values[index] if admitted else None)
        values.append(sample(frame, taps))
    return values


def engine(callback=sample, **options):
    defaults = dict(frame_count=40, result_bytes=4, delays=(1,), checkpoint_interval=4,
                    max_checkpoints=2, max_request_frames=100)
    defaults.update(options)
    return DelayedFeedback(callback, IDENTITY, **defaults)


@pytest.mark.parametrize('delays', [(1,), (3,), (1, 3, 1)])
@pytest.mark.parametrize('span', [None, Fraction(6), Fraction(49, 10), Fraction(1, 2)])
def test_full_sequential_and_shuffled_results_match_independent_recurrence(delays, span):
    reset = 'programme' if span is None else 'repeat'
    expected = oracle(40, delays, span=span, reset=reset)
    calls = []

    def evaluate(frame, history):
        calls.append((frame, history))
        return sample(frame, history)

    replay = engine(evaluate, delays=delays, repeat_span=span, reset=reset)
    for frame in range(40):
        assert replay.render(frame) == expected[frame]
    order = list(range(40))
    random.Random(112).shuffle(order)
    for frame in [17, 0, 39, 17, *order]:
        before = len(calls)
        planned = replay.plan(frame)
        assert replay.render(frame) == expected[frame]
        observed = [n for n, _history in calls[before:]]
        assert observed == list(range(planned.start_frame, frame + 1))
        snapshot = replay.snapshot()
        assert snapshot['last_execution']['evaluated_frames'] == observed
        assert snapshot['last_execution']['executed_steps'] == planned.steps
        assert observed[-1] == frame  # Even an exact checkpoint hit redraws target metadata.
        assert snapshot['checkpoint_count'] <= 2
        assert snapshot['retained_state_bytes'] + snapshot['working_reserve_bytes'] <= snapshot['limits']['max_state_bytes']
    replay.close()
    assert replay.snapshot()['retained_state_bytes'] == 0


def test_ordered_duplicate_delays_and_missing_seed_ports_reach_callback_exactly():
    calls = []
    replay = engine(lambda frame, history: calls.append((frame, history)) or sample(frame, history), delays=(3, 1, 3))
    replay.render(4)
    expected = oracle(5, (3, 1, 3))
    assert calls[0] == (0, (None, None, None))
    assert calls[2] == (2, (None, expected[1], None))
    assert calls[4] == (4, (expected[1], expected[3], expected[1]))
    assert replay.plan(4).history_refs == (1, 3, 1)


def test_plan_is_immutable_json_and_never_calls_renderer():
    replay = engine(lambda *_args: pytest.fail('plan called the renderer'), delays=(1, 4))
    plan = replay.plan(3)
    assert plan.start_frame == plan.reset_frame == 0 and plan.steps == 4
    assert plan.history_refs == (2, None)
    assert plan.origin == 'reset' and plan.checkpoint_sha256 is None
    assert json.loads(json.dumps(plan.document()))['target_frame'] == 3
    with pytest.raises(FrozenInstanceError):
        plan.steps = 100
    assert replay.checkpoint() is None


def test_fractional_reset_starts_at_first_real_encoded_frame_of_cycle():
    replay = engine(repeat_span=Fraction(49, 10), reset='repeat', delays=(1, 3))
    replay.render(9)
    assert replay.checkpoint().reset_frame == 5
    assert replay.plan(10).reset_frame == replay.plan(10).start_frame == 10
    assert replay.plan(10).history_refs == (None, None)
    assert replay.render(10) == sample(10, (None, None))


def test_nearest_checkpoint_saves_real_callbacks_and_oldest_points_are_evicted():
    calls = []
    replay = engine(lambda frame, history: calls.append(frame) or sample(frame, history))
    replay.render(15)
    assert replay.snapshot()['checkpoint_frames'] == [12, 16]
    assert replay.snapshot()['checkpoint_evictions'] > 0
    before = len(calls)
    plan = replay.plan(13)
    assert (plan.start_frame, plan.steps, plan.origin) == (12, 2, 'checkpoint')
    assert replay.render(13) == oracle(14, (1,))[13]
    assert calls[before:] == [12, 13]
    assert replay.plan(3).start_frame == 0


def test_actual_checkpoint_restores_new_instance_and_pins_payload_identity():
    original = engine(delays=(3,))
    original.render(15)
    checkpoint = original.checkpoint()
    assert checkpoint.history == tuple(enumerate(oracle(16, (3,)), start=0))[-3:]
    assert checkpoint.next_frame == 16 and len(checkpoint.digest) == 64
    document = checkpoint.document()
    assert document['payload_bytes'] == 12
    assert document['payload_sha256'] == checkpoint.payload_sha256
    assert document['sha256'] == checkpoint.digest
    assert json.loads(json.dumps(document)) == document
    with pytest.raises(FrozenInstanceError):
        checkpoint.next_frame = 12
    calls = []
    restored = engine(lambda frame, history: calls.append(frame) or sample(frame, history), delays=(3,))
    restored.restore(checkpoint, expected_sha256=checkpoint.digest)
    planned = restored.plan(19)
    assert planned.origin == 'checkpoint' and planned.checkpoint_sha256 == checkpoint.digest
    assert planned.start_frame == 16 and planned.steps == 4
    assert restored.render(19) == oracle(20, (3,))[19]
    assert calls == [16, 17, 18, 19]
    assert restored.snapshot()['restores'] == 1


def test_checkpoint_pin_is_mandatory_and_wrong_pin_does_not_mutate_current_state():
    replay = engine()
    replay.render(5)
    original = replay.checkpoint()
    with pytest.raises(TypeError):
        replay.restore(original)
    for expected in ('', 'a' * 63, 'F' * 64, '0' * 64, True):
        with pytest.raises(ValueError, match='checksum'):
            replay.restore(original, expected_sha256=expected)
        assert replay.checkpoint() is original


def test_changed_payload_and_foreign_identity_are_rejected_by_separate_expected_pin():
    replay = engine()
    replay.render(6)
    checkpoint = replay.checkpoint()
    changed = replace(checkpoint, history=((6, b'evil'),))
    with pytest.raises(ValueError, match='checksum'):
        replay.restore(changed, expected_sha256=checkpoint.digest)
    foreign = replace(checkpoint, identity='0' * 64)
    with pytest.raises(ValueError, match='identity'):
        replay.restore(foreign, expected_sha256=foreign.digest)
    assert replay.checkpoint() is checkpoint


@pytest.mark.parametrize('changes', [
    {'delays': (2,)}, {'checkpoint_interval': 5}, {'max_state_bytes': 200},
    {'frame_count': 39}, {'repeat_span': Fraction(6), 'reset': 'repeat'},
])
def test_checkpoint_rejects_configuration_changes(changes):
    original = engine()
    original.render(4)
    checkpoint = original.checkpoint()
    restored = engine(**changes)
    with pytest.raises(ValueError, match='configuration'):
        restored.restore(checkpoint, expected_sha256=checkpoint.digest)
    assert restored.checkpoint() is None


@pytest.mark.parametrize('history', [(), [(0, b'abcd')], ((True, b'abcd'),), ((1, b'abcd'),),
    ((0, bytearray(b'abcd')),), ((0, b'bad'),), ((0, b'abcd'), (0, b'abcd'))])
def test_invalid_checkpoint_structure_is_rejected(history):
    with pytest.raises(ValueError):
        FeedbackCheckpoint(IDENTITY, '1' * 64, 1, 0, 4, history)


def test_truncated_contiguous_history_cannot_restore_even_with_its_own_new_hash():
    replay = engine(delays=(3,))
    replay.render(7)
    good = replay.checkpoint()
    short = replace(good, history=good.history[-1:])
    with pytest.raises(ValueError, match='exact required history'):
        replay.restore(short, expected_sha256=short.digest)
    assert replay.checkpoint() is good


@pytest.mark.parametrize('options', [
    {'frame_count': True}, {'frame_count': 0}, {'frame_count': 10_000_001},
    {'result_bytes': 0}, {'result_bytes': 64 * 1024**2 + 1},
    {'delays': []}, {'delays': ()}, {'delays': (0,)}, {'delays': (-1,)},
    {'delays': (True,)}, {'delays': (Fraction(1),)}, {'delays': (257,)}, {'delays': (1,) * 33},
    {'reset': 'wrap'}, {'reset': 'repeat'}, {'repeat_span': .5}, {'repeat_span': True},
    {'repeat_span': Fraction(0)}, {'repeat_span': Fraction(1, 1_000_000_001)},
    {'max_checkpoints': 0}, {'max_checkpoints': 65}, {'checkpoint_interval': 0},
    {'checkpoint_interval': 3601}, {'max_request_frames': 0}, {'max_request_frames': 36001},
    {'max_state_bytes': True}, {'max_state_bytes': 512 * 1024**2 + 1}, {'max_state_bytes': 11},
])
def test_invalid_configuration_rejects_before_callbacks(options):
    with pytest.raises(ValueError):
        engine(lambda *_args: pytest.fail('invalid construction rendered'), **options)


@pytest.mark.parametrize('frame', [-1, True, 1.5, Fraction(1), 40])
def test_invalid_request_rejects_before_callbacks(frame):
    replay = engine(lambda *_args: pytest.fail('invalid request rendered'))
    with pytest.raises(ValueError):
        replay.render(frame)
    assert replay.checkpoint() is None and replay.snapshot()['current_request_frames'] == 0


def test_step_limit_is_admitted_before_work_and_forward_continuation_is_bounded():
    calls = []
    replay = engine(lambda frame, history: calls.append(frame) or sample(frame, history),
                    max_request_frames=3, max_checkpoints=1)
    with pytest.raises(FeedbackUnavailable) as exc:
        replay.render(3)
    assert exc.value.reason_code == 'replay_budget_exceeded' and not calls
    replay.render(0)
    replay.render(3)
    replay.render(4)
    assert calls == [0, 1, 2, 3, 4]
    checkpoint = replay.checkpoint()
    with pytest.raises(FeedbackUnavailable):
        replay.render(3)
    assert replay.checkpoint() is checkpoint and calls == [0, 1, 2, 3, 4]


def test_payload_budget_includes_working_and_committed_ring_and_evicts_checkpoints():
    replay = engine(delays=(2,), max_state_bytes=20)
    expected = oracle(25, (2,))
    for frame in (19, 3, 24, 0, 15):
        assert replay.render(frame) == expected[frame]
        receipt = replay.snapshot()
        assert receipt['retained_state_bytes'] <= 8
        assert receipt['last_execution']['peak_owned_payload_bytes'] <= 20
        assert receipt['checkpoint_count'] <= 2


@pytest.mark.parametrize('failure', ['cancel_before', 'cancel_after', 'callback', 'progress', 'final_progress', 'result'])
def test_failed_reconstruction_preserves_prior_committed_state_and_checkpoints(failure):
    bad = False
    callbacks = []

    def evaluate(frame, history):
        callbacks.append(frame)
        if bad and failure == 'callback' and frame == 8:
            raise RuntimeError('private callback detail')
        if bad and failure == 'result' and frame == 8:
            return bytearray(b'bad!')
        return sample(frame, history)

    def progress(receipt):
        if bad and failure == 'progress' and receipt['executed_steps'] == 2:
            raise RuntimeError('private observer detail')
        if bad and failure == 'final_progress' and receipt['completed']:
            raise RuntimeError('private observer detail')

    replay = engine(evaluate, delays=(3,))
    replay.render(6)
    committed, snapshot = replay.checkpoint(), replay.snapshot()
    bad = True
    with pytest.raises((FeedbackCancelled, FeedbackUnavailable)):
        replay.render(12, cancelled=lambda: failure == 'cancel_before' or
            (failure == 'cancel_after' and replay.current_request_frames >= 2), progress=progress)
    assert replay.checkpoint() is committed
    assert replay.snapshot()['checkpoint_frames'] == snapshot['checkpoint_frames']
    assert replay.snapshot()['completed_requests'] == snapshot['completed_requests']
    assert replay.snapshot()['last_execution'] is None
    bad = False
    assert replay.render(12) == oracle(13, (3,))[12]


def test_observers_cannot_mutate_committed_execution_lists():
    replay = engine()

    def observer(receipt):
        receipt['history_refs'].clear()
        if 'evaluated_frames' in receipt:
            receipt['evaluated_frames'].clear()

    replay.render(6, progress=observer)
    first = replay.snapshot()
    assert first['last_execution']['evaluated_frames'] == list(range(7))
    assert first['last_execution']['history_refs'] == [5]
    first['last_execution']['evaluated_frames'].clear()
    assert replay.snapshot()['last_execution']['evaluated_frames'] == list(range(7))


def test_owner_admission_can_retire_stale_diagnostics_without_discarding_checkpoint_state():
    replay = engine()
    replay.render(7)
    checkpoint, before = replay.checkpoint(), replay.snapshot()
    replay.clear_request(19)
    assert replay.snapshot()['last_execution'] is None
    assert replay.snapshot()['current_request_frames'] == 0
    assert replay.snapshot()['requested_frame'] == 19
    replay.clear_request(19, reason_code='state_budget_exceeded')
    assert replay.snapshot()['phase'] == 'unavailable'
    assert replay.snapshot()['last_reason'] == 'state_budget_exceeded'
    assert replay.checkpoint() is checkpoint
    assert replay.snapshot()['checkpoint_frames'] == before['checkpoint_frames']
    with pytest.raises(ValueError, match='unknown feedback admission'):
        replay.clear_request(19, reason_code='private path must not appear')
    assert replay.render(8) == oracle(9, (1,))[8]


@pytest.mark.parametrize('action', ['render', 'restore', 'close'])
def test_reentrant_state_changes_are_rejected_without_partial_commit(action):
    replay = engine()
    replay.render(1)
    old = replay.checkpoint()

    def evaluate(frame, history):
        if action == 'render':
            replay.render(0)
        elif action == 'restore':
            replay.restore(old, expected_sha256=old.digest)
        else:
            replay.close()
        return sample(frame, history)

    replay.evaluate = evaluate
    with pytest.raises(FeedbackUnavailable) as exc:
        replay.render(2)
    assert exc.value.reason_code == 'invalid_feedback_state'
    assert replay.checkpoint() is old


def test_retained_exception_traceback_has_no_scheduler_owned_partial_payload_containers():
    replay = engine(delays=(3,))
    replay.render(4)
    saved = replay.checkpoint()

    def fail_progress(receipt):
        if receipt['completed']:
            raise RuntimeError('retained failure')

    with pytest.raises(FeedbackUnavailable) as caught:
        replay.render(12, progress=fail_progress)
    frames = []
    traceback = caught.value.__traceback__
    while traceback:
        if traceback.tb_frame.f_code.co_name == 'render':
            frames.append(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next
    assert frames and all(row['ring'] == row['pending'] == {} for row in frames)
    assert all(not {'history', 'value', 'state', 'completed', 'candidates', 'committed'} & row.keys() for row in frames)
    assert replay.checkpoint() is saved


def test_closed_renderer_releases_owned_state_and_refuses_new_work_or_restore():
    replay = engine()
    replay.render(7)
    checkpoint = replay.checkpoint()
    replay.close()
    assert replay.snapshot()['retained_state_bytes'] == replay.snapshot()['checkpoint_count'] == 0
    for operation in (lambda: replay.plan(0), lambda: replay.render(0), lambda: replay.checkpoint(),
                      lambda: replay.restore(checkpoint, expected_sha256=checkpoint.digest)):
        with pytest.raises(FeedbackUnavailable) as exc:
            operation()
        assert exc.value.reason_code == 'renderer_closed'
