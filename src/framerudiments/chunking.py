"""Frame-exact musical chunk planning for long renders."""

from math import ceil


DEFAULT_THRESHOLD_SECONDS = 30.0
DEFAULT_TARGET_SECONDS = 30.0
DEFAULT_MIN_SECONDS = 8.0
DEFAULT_CUE_SEARCH_SECONDS = 12.0


def should_chunk(project, threshold_seconds=DEFAULT_THRESHOLD_SECONDS):
    if type(threshold_seconds) not in (int, float) or threshold_seconds <= 0:
        raise ValueError("Chunk threshold must be a positive number of seconds")
    return project.frame_count > round(project.fps * threshold_seconds)


def plan_chunks(
    project,
    *,
    target_seconds=DEFAULT_TARGET_SECONDS,
    min_seconds=DEFAULT_MIN_SECONDS,
    cue_search_seconds=DEFAULT_CUE_SEARCH_SECONDS,
):
    """Split the complete frame ledger without changing its clock.

    Structural cue frames are preferred near balanced targets. Every boundary
    is start-inclusive/end-exclusive, and the returned chunks cover every
    project frame exactly once.
    """
    for value, label in (
        (target_seconds, "target_seconds"),
        (min_seconds, "min_seconds"),
        (cue_search_seconds, "cue_search_seconds"),
    ):
        if type(value) not in (int, float) or value <= 0:
            raise ValueError(f"{label} must be a positive number")
    if min_seconds >= target_seconds:
        raise ValueError("Chunk min_seconds must be smaller than target_seconds")

    total = project.frame_count
    fps = project.fps
    count = max(2, ceil(total / (target_seconds * fps)))
    min_frames = max(1, round(min_seconds * fps))
    search_frames = max(1, round(cue_search_seconds * fps))
    if total < count * min_frames:
        min_frames = max(1, total // count)

    cues = []
    for cue in project.cues:
        frame = cue.get("frame")
        if type(frame) is int and 0 < frame < total:
            cues.append((frame, cue.get("text", ""), cue.get("source_seconds")))
    cues.sort(key=lambda row: row[0])

    boundaries = [0]
    boundary_evidence = []
    for index in range(1, count):
        ideal = round(index * total / count)
        lower = boundaries[-1] + min_frames
        upper = total - min_frames * (count - index)
        if lower > upper:
            raise ValueError("Project is too short for the requested chunk constraints")
        balanced = min(upper, max(lower, ideal))
        eligible = [row for row in cues if lower <= row[0] <= upper]
        nearby = [row for row in eligible if abs(row[0] - balanced) <= search_frames]
        if nearby:
            frame, label, source_seconds = min(
                nearby, key=lambda row: (abs(row[0] - balanced), row[0])
            )
            reason = "structural_cue"
        else:
            frame, label, source_seconds = balanced, None, None
            reason = "balanced_frame_fallback"
        boundaries.append(frame)
        boundary_evidence.append(
            dict(
                frame=frame,
                output_seconds=frame / fps,
                reason=reason,
                cue_label=label,
                cue_source_seconds=source_seconds,
                ideal_frame=ideal,
                displacement_frames=frame - ideal,
            )
        )
    boundaries.append(total)

    chunks = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        if end <= start:
            raise ValueError("Chunk plan contains an empty or reversed interval")
        chunks.append(
            dict(
                index=index,
                start_frame=start,
                end_frame_exclusive=end,
                frames=end - start,
                start_seconds=start / fps,
                end_seconds=end / fps,
                boundary_after=(boundary_evidence[index] if index < len(boundary_evidence) else None),
            )
        )
    if chunks[0]["start_frame"] != 0 or chunks[-1]["end_frame_exclusive"] != total:
        raise AssertionError("Chunk plan does not cover the full project")
    if sum(item["frames"] for item in chunks) != total:
        raise AssertionError("Chunk plan duplicates or omits project frames")
    return dict(
        schema="framerudiments.chunk-plan.v1",
        mode="sequential-frame-exact",
        total_frames=total,
        fps=fps,
        threshold_seconds=DEFAULT_THRESHOLD_SECONDS,
        target_seconds=target_seconds,
        min_seconds=min_seconds,
        cue_search_seconds=cue_search_seconds,
        chunks=chunks,
        continuity=(
            "Composition, temporal filters and feedback advance from frame zero in absolute project time. "
            "Resume replays completed frames before emitting the first missing chunk."
        ),
        audio="Canonical programme is muxed once after video-only stream-copy concatenation.",
    )
