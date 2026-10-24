from types import SimpleNamespace

import pytest

from framerudiments.chunking import plan_chunks, should_chunk


def project(frames=3600, fps=60, cues=()):
    return SimpleNamespace(frame_count=frames, fps=fps, cues=list(cues))


def test_more_than_thirty_seconds_chunks_by_default():
    assert not should_chunk(project(frames=1800))
    assert should_chunk(project(frames=1801))


def test_plan_prefers_nearby_structural_cues_and_covers_every_frame_once():
    p = project(
        frames=7200,
        cues=[
            {"frame": 2370, "text": "Chorus", "source_seconds": 39.5},
            {"frame": 4810, "text": "Interlude", "source_seconds": 80.166},
        ],
    )
    plan = plan_chunks(p, target_seconds=40, cue_search_seconds=4)
    assert [item["end_frame_exclusive"] for item in plan["chunks"][:-1]] == [2370, 4810]
    assert [item["boundary_after"]["reason"] for item in plan["chunks"][:-1]] == [
        "structural_cue",
        "structural_cue",
    ]
    assert sum(item["frames"] for item in plan["chunks"]) == p.frame_count
    assert [item["start_frame"] for item in plan["chunks"]][1:] == [
        item["end_frame_exclusive"] for item in plan["chunks"][:-1]
    ]


def test_plan_uses_balanced_fallback_without_a_nearby_cue():
    plan = plan_chunks(project(frames=3601), target_seconds=30, cue_search_seconds=1)
    assert len(plan["chunks"]) == 3
    assert all(
        item["boundary_after"]["reason"] == "balanced_frame_fallback"
        for item in plan["chunks"][:-1]
    )


def test_forced_short_plan_reduces_minimum_without_empty_chunks():
    plan = plan_chunks(project(frames=61), target_seconds=0.6, min_seconds=0.2)
    assert len(plan["chunks"]) == 2
    assert all(item["frames"] > 0 for item in plan["chunks"])


@pytest.mark.parametrize("value", [0, -1, True, "30"])
def test_invalid_chunk_target_fails_closed(value):
    with pytest.raises(ValueError):
        plan_chunks(project(), target_seconds=value)
