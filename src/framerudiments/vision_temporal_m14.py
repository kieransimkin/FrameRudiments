"""M14.4: deterministic prediction-tap scheduling for explicit preview Q0..Q5.

Frame identity is source-clock based. These plans never interpolate model flow,
run ONNX, or alter the authored renderer's exact frame selection. Caches are
reported as present/unverified until a real read independently validates them.
"""
from __future__ import annotations


from .vision_graph_m14 import VisionGraphSources

SCHEMA = "framerudiments.vision-preview-plan.v1"
MAX_INSPECTION_FRAMES = 256
MAX_TAPS = 4096


def _validated(owner, frames, sources, quality):
    if not isinstance(owner, VisionGraphSources):
        raise ValueError("M14.4 preview plan needs a prepared graph-source owner")
    if type(quality) is not int or not 0 <= quality <= 5:
        raise ValueError("M14.4 quality must be explicit Q0..Q5")
    if (not isinstance(frames, (list, tuple)) or not 1 <= len(frames) <= MAX_INSPECTION_FRAMES or
            any(type(n) is not int or not 0 <= n < owner.project.frame_count for n in frames)):
        raise ValueError("M14.4 preview plan requires 1..256 valid integer frames")
    if sources is None:
        source_ids = sorted(owner.source_ids)
    else:
        if (not isinstance(sources, (list, tuple)) or not sources or
                len(sources) != len(set(sources)) or
                any(not isinstance(s, str) or s not in owner.source_ids for s in sources)):
            raise ValueError("M14.4 preview source selection is invalid")
        source_ids = sorted(sources)
    frames = tuple(sorted(set(frames)))
    if len(frames) * len(source_ids) > MAX_TAPS:
        raise ValueError("M14.4 preview plan is too large")
    return frames, source_ids


def plan_preview(owner, frames, *, quality, sources=None):
    """Return bounded, read-only source taps and provider-specific cache keys."""
    frames, names = _validated(owner, frames, sources, quality)
    rows = []
    unique = set()
    for frame in frames:
        for name in names:
            chosen, spec, reason = owner.selection(name, frame, quality=quality)
            profile = owner.sources[name][0]
            pin = owner.provider_pins.get(name)
            provider = pin[0] if pin is not None else "CPUExecutionProvider"
            if spec is None:
                rows.append({"frame": frame, "source": name, "selected_frame": None,
                             "selection": "transparent", "provider": provider,
                             "profile_sha256": profile.digest, "request_sha256": None,
                             "cache_key": None, "cache_present_unverified": False})
                continue
            if pin is not None:
                from .vision_provider_cache_m14 import provider_cache_key

                key = provider_cache_key(profile, spec, *pin, chosen)
                location = owner.cache / ("gpu-" + key)
            else:
                key = owner._identity(profile, spec)
                location = owner.cache / key
            unique.add((provider, key))
            rows.append({"frame": frame, "source": name, "selected_frame": chosen,
                         "selection": reason, "provider": provider,
                         "profile_sha256": profile.digest,
                         "request_sha256": spec.manifest_sha256, "cache_key": key,
                         "cache_present_unverified": location.is_dir() and not location.is_symlink()})
    return {"schema": SCHEMA, "quality": quality, "frames": list(frames),
            "sources": names, "taps": rows, "unique_predictions": len(unique),
            "reused_taps": sum(row["selection"] == "preview_reuse" for row in rows),
            "model_estimated": True, "native_geometry": False,
            "cache_presence_is_verification": False,
            "authored_render_unchanged": True}


def warm_preview(owner, frames, *, quality, sources=None):
    """Explicit opt-in prewarming of preview taps, never a background worker.

    CPU-only misses use the existing bounded M14.1 subprocess. GPU misses fail
    until a pinned, independently verified provider batch has been imported.
    """
    plan = plan_preview(owner, frames, quality=quality, sources=sources)
    seen = set()
    warmed = []
    for tap in plan["taps"]:
        if tap["cache_key"] is None:
            continue
        key = (tap["provider"], tap["cache_key"])
        if key in seen:
            continue
        seen.add(key)
        image = owner.source(tap["source"], tap["frame"], quality=quality)
        try:
            warmed.append({"source": tap["source"], "selected_frame": tap["selected_frame"],
                           "provider": tap["provider"], "cache_key": tap["cache_key"],
                           "was_present_unverified": tap["cache_present_unverified"],
                           "verified_read": True})
        finally:
            image.close()
    return {"schema": "framerudiments.vision-preview-warm.v1",
            "quality": quality, "planned": len(plan["taps"]),
            "unique_verified": len(warmed), "entries": warmed,
            "model_estimated": True, "physical_gpu_certified": False}
