"""Server-owned seek capabilities and native temporal dependency windows."""
from .preview_dependencies import TemporalReach


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
        if past or future:
            return TemporalReach('bounded', past_frames=past, future_frames=future)
    return TemporalReach()


def seek_status(document):
    reach = temporal_reach(document)
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
