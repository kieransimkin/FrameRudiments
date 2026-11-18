"""Server-owned seek capabilities and native temporal dependency windows."""
from math import ceil

from .preview_dependencies import TemporalReach


def temporal_reach(document):
    """Use engine bounds, never client-provided invalidation/reset hints."""
    if document.get('effects'):
        # There is no native checkpoint export or proved mid-project reset.
        return TemporalReach('feedback')
    post = document.get('post_effects', {})
    if isinstance(post, dict) and 'temporal' in post:
        from .quality_effects import TemporalShutter
        # Modulations can reach the full native bound even when the base span
        # is smaller. Conservative across repeats; never miss a read boundary.
        return TemporalReach('bounded', past_frames=ceil(TemporalShutter._RANGES['shutter_frames'][1]))
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
    return dict(enabled=reach.mode == 'bounded',
                mode='bounded_history' if reach.mode == 'bounded' else 'independent',
                phase='ready', past_frames=reach.past_frames, future_frames=reach.future_frames,
                checkpoint_restore=False, production_equivalent=False)
