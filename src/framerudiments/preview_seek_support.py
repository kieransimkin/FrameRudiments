"""Server-owned seek capabilities and native temporal dependency windows."""
from fractions import Fraction
from math import ceil

from .preview_dependencies import TemporalReach


def _feedback(document):
    post = document.get('post_effects', {})
    if not isinstance(post, dict):
        raise ValueError('post_effects must be an object')
    value = post.get('feedback')
    if value is not None and not isinstance(value, dict):
        raise ValueError('native feedback must be an object')
    return value


def _native_resets(document, feedback):
    """Derive resets from source clocks, never caller-supplied frame hints.

    A very large repeat ledger keeps a conservative programme-wide closure
    instead of allocating an unbounded invalidation list. The renderer still
    computes every actual reset exactly on demand.
    """
    if not isinstance(feedback, dict) or feedback.get('reset', 'repeat') != 'repeat':
        return ()
    audio = document.get('audio', {})
    if not isinstance(audio, dict):
        return ()
    start, end = audio.get('start_sample', 0), audio.get('end_sample')
    rate, fps, repeats = audio.get('sample_rate'), document.get('fps', 60), audio.get('repeats', 1)
    if (any(type(value) is not int for value in (start, end, rate, fps, repeats))
            or start < 0 or end <= start or rate <= 0 or fps <= 0 or not 1 <= repeats <= 8192):
        return ()
    span = Fraction((end - start) * fps, rate)
    count = ceil(span * repeats)
    return tuple(sorted({ceil(span * cycle) for cycle in range(1, repeats)
                         if ceil(span * cycle) < min(count, 10_000_000)}))


def temporal_reach(document):
    """Use engine bounds, never client-provided invalidation/reset hints."""
    if document.get('effects'):
        # There is no native checkpoint export or proved mid-project reset.
        return TemporalReach('feedback')
    if document:
        from .compositor_temporal import temporal_read_bounds

        # The engine owns both shutter history and fractional forward reads.
        # A whole-frame closure rounds a nonzero subframe reach upward; it can
        # invalidate extra boundary frames but cannot omit a real source read.
        past, future = temporal_read_bounds(document)
        feedback = _feedback(document)
        if feedback is not None:
            return TemporalReach('feedback', past_frames=past, future_frames=future,
                                 reset_frames=_native_resets(document, feedback))
        if past or future:
            return TemporalReach('bounded', past_frames=past, future_frames=future)
    return TemporalReach()


def seek_status(document):
    reach = temporal_reach(document)
    feedback = _feedback(document)
    if feedback is not None and not document.get('effects'):
        return dict(enabled=True, mode='checkpoint_replay', phase='idle',
                    requested_frame=None, verified_prefix_frames=0,
                    current_request_frames=0, replayed_frames=0, live_next_frame=0,
                    max_prefix_frames=None, max_request_frames=feedback.get('max_replay_frames', 3600),
                    checkpoint_count=0, restored_from_frame=None,
                    last_reason=None, checkpoint_restore=True,
                    cache_scope='native_section_dependency_identity', production_equivalent=False)
    if reach.mode == 'feedback':
        return dict(enabled=True, mode='verified_prefix_replay', phase='idle',
                    requested_frame=None, verified_prefix_frames=0,
                    current_request_frames=0, replayed_frames=0, live_next_frame=0,
                    max_prefix_frames=1800, max_request_frames=3600,
                    last_reason=None, checkpoint_restore=False,
                    cache_scope='current_stream_qualification', production_equivalent=False)
    # Forward samples within one encoded frame require dependency coverage but
    # do not turn the existing seek UI into a feedback/history reconstruction.
    history = reach.mode == 'bounded' and reach.past_frames > 0
    return dict(enabled=history,
                mode='bounded_history' if history else 'independent',
                phase='ready', past_frames=reach.past_frames, future_frames=reach.future_frames,
                checkpoint_restore=False, production_equivalent=False)
