"""Real native editor/Q5 parity for the new note consumers; no renderer doubles."""

from hashlib import sha256
import json

from framerudiments import NoteTimeline, Project
from framerudiments.preview_native_renderer import NativePreviewRenderer
from framerudiments.preview_refinement_process import RefinementSpec, SpawnQ5Renderer
from test_preview_native_project import fixture
import pytest


@pytest.mark.parametrize(
    "view,direction", [("piano-roll", "falling"), ("piano-roll", "rising"), ("note-highway", "falling")]
)
def test_notes_survive_native_quality_views_and_real_spawned_cache(tmp_path, view, direction):
    path, data = fixture(tmp_path)
    notes = NoteTimeline.from_notes(
        Project(data, tmp_path),
        [
            {"key": 60, "start_seconds": 0.25, "end_seconds": 0.75},
            {"key": 64, "start_seconds": 1, "end_seconds": 1.75},
        ],
        provenance={"kind": "native preview fixture"},
    )
    pin = notes.write(tmp_path / "notes.json")
    settings = {"note_range": [60, 72]} if view == "piano-roll" else {"note_map": {"60": 0, "64": 3}}
    data["layers"].append(
        dict(
            id="notes",
            type="notes",
            view=view,
            direction=direction,
            file={"path": "notes.json", "sha256": pin["sha256"]},
            bounds=[0, 0, data["width"], data["height"]],
            **settings,
        )
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    before = {
        name: sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in ("project.json", "notes.json", "source.wav")
    }
    parent = tmp_path / "sessions"
    foreground = NativePreviewRenderer(path, data, {}, validate_draft=lambda a, b: None, temp_parent=parent)
    try:
        assert foreground(31, "Q0").startswith(b"\x89PNG")
        expected = foreground(31, "Q5")
        cache = foreground.last_layer_cache
        assert cache["layers"]["layer:notes"]["writes"] > 0
    finally:
        foreground.close()
    worker = SpawnQ5Renderer(RefinementSpec.from_snapshot(path, data, data, {}, parent))
    try:
        assert worker(31, lambda: False) == expected
        assert worker.last_layer_cache["layers"]["layer:notes"]["hits"] > 0
        assert worker.last_layer_cache["layers"]["layer:notes"]["writes"] == 0
        worker(61, lambda: False)
        assert worker(31, lambda: False) == expected
    finally:
        worker.close()
    assert worker.process is None and not list(parent.iterdir())
    assert {name: sha256((tmp_path / name).read_bytes()).hexdigest() for name in before} == before
