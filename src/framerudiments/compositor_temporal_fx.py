"""M11.3 compositor bridge: preflighted observed native taps and metadata rollback.

No recursive call to Compositor.frame: both ordinary rendering and feedback
recurrence evaluate this adapter, whose inputs are the same M11.1 native
scheduler outputs. The logical frame retains its authoritative audio/beat
context and route surfaces even when the visible pixels are time remapped.
"""
from __future__ import annotations

from copy import deepcopy

from .compositor_temporal import _metadata, _restore, render_scheduled_frame, temporal_plan
from .feedback_history import FeedbackCancelled

MAX_FEEDBACK_TAP_WORK = 4096


def temporal_fx_plan(compositor, frame):
    """Plan all native reads, including exact per-tap M11.1 preflight."""
    spec = compositor.post_pipeline.temporal_fx
    if spec is None:
        return None
    plan = spec.plan(frame)
    for index in (*plan.source_frames, frame):
        temporal_plan(compositor, index)
    return plan


def temporal_fx_contract(compositor):
    spec = compositor.post_pipeline.temporal_fx
    if spec is None:
        return None
    from .temporal_fx_m11 import temporal_fx_bounds

    past, future = temporal_fx_bounds(spec.evidence['settings'])
    return {'schema': spec.evidence['schema'], 'config': deepcopy(spec.evidence),
            'source_read_bounds': {'past_frames': past, 'future_frames': future},
            'max_unique_frames': 48, 'max_retained_rgb_bytes': 256 * 1024 * 1024,
            'scope': 'pre_feedback_native_RGB8', 'checkpointed': False,
            'rational_interpolation': True, 'external_engine_replay': False}


def render_temporal_fx_frame(compositor, frame, *, apply_quality=True,
                             cancelled=None, progress=None):
    """Evaluate a fully bounded, shuffle-safe temporal FX frame transaction."""
    spec = compositor.post_pipeline.temporal_fx
    if spec is None:
        return render_scheduled_frame(compositor, frame,
            apply_quality=apply_quality, cancelled=cancelled)
    if type(apply_quality) is not bool:
        raise ValueError('temporal_fx apply_quality must be boolean')
    if cancelled is not None and not callable(cancelled):
        raise ValueError('temporal_fx cancellation callback must be callable')
    if progress is not None and not callable(progress):
        raise ValueError('temporal_fx progress callback must be callable')

    def check():
        if cancelled is not None and cancelled():
            raise FeedbackCancelled('superseded native temporal FX request')

    check()
    plan = temporal_fx_plan(compositor, frame)
    before = _metadata(compositor)
    last_schedule = deepcopy(getattr(compositor, '_last_temporal_schedule', None))
    last_fx = deepcopy(getattr(compositor, '_last_temporal_fx', None))
    pixels = {}
    executed = 0
    try:
        # Observe historical sources first and render the authoritative target
        # last. That retains correct lyrics, extents, route masks and reports;
        # retiming does not rewrite the source clock of this output frame.
        for index in (*[x for x in plan.source_frames if x != frame], frame):
            check()
            image, context = render_scheduled_frame(compositor, index,
                apply_quality=False, cancelled=cancelled)
            if index in plan.source_frames:
                pixels[index] = image.copy()
            executed += 1
            if progress is not None:
                progress({'schema': plan.document()['schema'], 'frame': frame,
                    'phase': 'native_taps', 'completed': executed,
                    'total': len(plan.source_frames) + int(frame not in plan.source_frames)})
        target_metadata = _metadata(compositor)
        target_schedule = deepcopy(getattr(compositor, '_last_temporal_schedule', None))
        check()
        result, report = spec.apply(pixels, plan, context)
        check()
        _restore(compositor, target_metadata)
        compositor.post_pipeline.last_report['temporal_fx'] = report
        compositor._last_temporal_schedule = target_schedule
        compositor._last_temporal_fx = {'schema': report['schema'], 'frame': frame,
            'source_frames': list(plan.source_frames), 'native_tap_callbacks': executed,
            'plan': plan.document(), 'completed': True}
        final = compositor._final_output(result, context, apply_quality)
        check()
        return final
    except BaseException:
        _restore(compositor, before)
        compositor._last_temporal_schedule = last_schedule
        compositor._last_temporal_fx = last_fx
        virtual = getattr(compositor, '_temporal_virtual', None)
        if virtual is not None:
            try:
                virtual.close()
            finally:
                compositor.__dict__.pop('_temporal_virtual', None)
        raise
    finally:
        pixels.clear()


def preflight_feedback_tap_work(compositor, start_frame, target_frame, cancelled):
    """Admit a bounded delayed-feedback reconstruction before pixel work."""
    if compositor.post_pipeline.temporal_fx is None:
        return
    count = 0
    for frame in range(start_frame, target_frame + 1):
        if cancelled():
            raise FeedbackCancelled('superseded temporal FX reconstruction')
        plan = temporal_fx_plan(compositor, frame)
        count += len(plan.source_frames) + int(frame not in plan.source_frames)
        if count > MAX_FEEDBACK_TAP_WORK:
            raise ValueError('feedback plus temporal_fx exceeds 4096 preflighted native frame callbacks')
