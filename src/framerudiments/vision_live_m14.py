"""M14.5: opt-in adaptive preview prediction admission on the native render thread.

Native stream S2 owns revision, latest-intent and section-cache invalidation.
This adapter supplies only bounded, Q-tagged predictions to that existing
pipeline; it cannot publish pixels or claim audio/geometry provenance.
"""
from __future__ import annotations

from .vision_m14 import VisionCancelled
from .vision_temporal_m14 import plan_preview

SCHEMA = "framerudiments.vision-live-preview.v1"


def prepare_live_frame(owner, frame, profile_id, *, cancelled):
    """Verify all selected predictions before a native preview renders pixels.

    Cache fills are content-addressed; a seek/edit cancellation interrupts the
    isolated model subprocess and leaves no completed output from that request.
    Q5's exact-frame policy matches authored samples (not a flow interpolation).
    The normal compositor never calls this API.
    """
    if (not isinstance(profile_id, str) or len(profile_id) != 2 or
            profile_id not in {"Q0", "Q1", "Q2", "Q3", "Q4", "Q5"}):
        raise ValueError("M14.5 requires an explicit Q0..Q5 preview profile")
    if not callable(cancelled):
        raise ValueError("M14.5 preview needs a revision cancellation callback")
    if cancelled():
        raise VisionCancelled("Superseded prediction request")
    quality = int(profile_id[1])
    if getattr(owner.project, "_vision_preview_quality", None) != quality:
        raise ValueError("M14.5 renderer and prediction quality are not the same")
    report = plan_preview(owner, [frame], quality=quality)
    touched = set()
    verified = 0
    for tap in report["taps"]:
        if cancelled():
            raise VisionCancelled("Superseded prediction request")
        if tap["cache_key"] is None:
            continue
        identity = (tap["provider"], tap["cache_key"])
        if identity in touched:
            continue
        touched.add(identity)
        # The M14.2 cache verifies *actual* float32 bytes and pins on each
        # access, not merely the cache directory's existence.
        image = owner.source(tap["source"], frame, quality=quality)
        try:
            if cancelled():
                raise VisionCancelled("Superseded prediction request")
            verified += 1
        finally:
            image.close()
    return {"schema": SCHEMA, "frame": frame, "profile_id": profile_id,
            "quality_rank": quality, "verified_predictions": verified,
            "reused_taps": report["reused_taps"], "model_estimated": True,
            "native_geometry": False, "authored_output_unchanged": True}
