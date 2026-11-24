"""Real native pixels and authored input rejection across the full note-view suite."""

from contextlib import closing
from copy import deepcopy
from hashlib import sha256
import os
from pathlib import Path
import shutil
import socket
from PIL import Image, ImageDraw
import pytest
from framerudiments import Compositor, Project
from framerudiments.particle_engine import ParticleEmitter
import test_note_visualisers as native_fixtures
from test_note_visualisers import interval, write_notes, project_with

source = native_fixtures.source


@pytest.fixture
def named(source):
    path = source.base / "font.ttf"
    shutil.copyfile(Path(os.environ["FRAMERUDIMENTS_TEST_FONT"]), path)
    data = deepcopy(source.data)
    data["font"] = {"path": path.name, "sha256": sha256(path.read_bytes()).hexdigest()}
    return Project(data, source.base)


def settings(source, pin, view):
    if view in ("drum-lanes", "pad-grid"):
        return {"drums": {"pads": [{"id": "kick", "label": "Kick", "keys": [60], "shape": "circle"}]}}
    if view in ("fretboard", "tablature"):
        return {
            "fretboard": {
                "notes_sha256": pin["sha256"],
                "tuning": [60],
                "fret_count": 12,
                "positions": [{"note_index": 0, "string": 1, "fret": 0}],
            }
        }
    if view == "score":
        path = source.base / "page.png"
        if not path.exists():
            image = Image.new("RGBA", (100, 50), "white")
            ImageDraw.Draw(image).rectangle((20, 10, 40, 40), fill="black")
            image.save(path)
        return {
            "score": {
                "notes_sha256": pin["sha256"],
                "reviewed": True,
                "provenance": "Authored test rectangle, not a musical engraving",
                "pages": [
                    {
                        "image": {"path": path.name, "sha256": sha256(path.read_bytes()).hexdigest()},
                        "start_seconds": 0,
                        "end_seconds": 2,
                    }
                ],
                "glyphs": [{"page": 0, "note_indices": [0], "bounds": [20, 10, 40, 40]}],
            }
        }
    return {}


VIEWS = (
    "arrangement",
    "drum-lanes",
    "pad-grid",
    "pitch-wheel",
    "fretboard",
    "tablature",
    "score",
    "note-impacts",
)


@pytest.mark.parametrize("view", VIEWS)
def test_all_views_render_and_seek_without_devices(named, view, monkeypatch):
    pin = write_notes(named)
    project = project_with(named, pin, view, **settings(named, pin, view))
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("Rendering opened a socket"))
    import framerudiments.midi_protocol_m15 as protocol

    monkeypatch.setattr(protocol, "_module", lambda *a, **kw: pytest.fail("Rendering opened MIDI"))
    with closing(Compositor(project, layer_cache=False)) as owner:
        before = owner.frame(30)[0].tobytes()
        owner.frame(75)
        assert owner.frame(30)[0].tobytes() == before
        report = owner.frame_reports()["notes"]
        assert report["view"] == view and report["active_keys"] == [60]


@pytest.mark.parametrize("view", VIEWS)
def test_tiny_native_viewports_do_not_emit_invalid_geometry(named, view):
    pin = write_notes(named)
    project = project_with(named, pin, view, **settings(named, pin, view))
    project.data["layers"][0]["bounds"] = [0, 0, 1, 1]
    with closing(Compositor(Project(project.data, project.base))) as owner:
        owner.frame(30)
        assert owner.frame_reports()["notes"]["view"] == view


def test_zero_duration_percussion_hit_is_visible_after_note_off(source):
    pin = write_notes(source, [interval(start=0.25, end=0.25)])
    project = project_with(source, pin, "pad-grid", labels=False, **settings(source, pin, "pad-grid"))
    with closing(Compositor(project, layer_cache=False)) as owner:
        off = owner.frame(0)[0].tobytes()
        hit = owner.frame(15)[0].tobytes()
        report = owner.frame_reports()["notes"]
        assert report["active_keys"] == []
        assert report["pads"][0]["recent_hits"] == 1
        assert off != hit
        owner.frame(60)
        assert owner.frame_reports()["notes"]["pads"][0]["recent_hits"] == 0


@pytest.mark.parametrize(
    "pads",
    [
        [],
        [{"label": "Kick", "keys": [36]}],
        [{"label": "A", "keys": [60]}, {"label": "B", "keys": [60]}],
        [{"id": "same", "label": "A", "keys": [60]}, {"id": "same", "label": "B", "keys": [61]}],
        [{"label": "A", "keys": [60], "shape": "explosion"}],
        [{"label": "", "keys": [60]}],
    ],
)
def test_percussion_map_is_authored_and_unambiguous(source, pads):
    pin = write_notes(source)
    with pytest.raises(ValueError):
        Compositor(project_with(source, pin, "drum-lanes", labels=False, drums={"pads": pads}))


def test_wheel_retains_octaves_and_tracks_instead_of_naming_a_chord(source):
    pin = write_notes(source, [interval(key=60), interval(key=72, track=2, channel=3)])
    with closing(Compositor(project_with(source, pin, "pitch-wheel", labels=False))) as owner:
        owner.frame(30)
        report = owner.frame_reports()["notes"]
        assert report["pitch_classes"] == [
            dict(pitch_class=0, name="C", keys=[60, 72], voices=2, channels=[1, 3], tracks=[0, 2])
        ]
        assert report["harmonic_inference"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"notes_sha256": "0" * 64},
        {"tuning": [59]},
        {"positions": []},
        {"positions": [{"note_index": 0, "string": 2, "fret": 0}]},
        {"positions": [{"note_index": 0, "string": 1, "fret": 1}]},
        {"positions": [{"note_index": 0, "string": 1, "fret": 0}] * 2},
        {"positions": [{"note_index": True, "string": 1, "fret": 0}]},
        {"positions": [{"note_index": 0, "string": 1, "fret": 0, "technique": "automatic"}]},
    ],
)
def test_fingering_does_not_guess_or_accept_a_stale_source(source, change):
    pin = write_notes(source)
    cfg = settings(source, pin, "fretboard")["fretboard"]
    cfg.update(change)
    with pytest.raises(ValueError):
        Compositor(project_with(source, pin, "fretboard", labels=False, fretboard=cfg))


def test_capo_relative_fret_zero_is_a_declared_open_string(source):
    pin = write_notes(source)
    cfg = settings(source, pin, "fretboard")["fretboard"]
    cfg.update(tuning=[58], capo=2)
    with closing(Compositor(project_with(source, pin, "fretboard", labels=False, fretboard=cfg))) as owner:
        owner.frame(30)
        assert owner.frame_reports()["notes"]["sounding_positions"] == [dict(string=1, fret=0)]
        assert owner.frame_reports()["notes"]["fingering_inferred"] is False


def test_score_highlight_uses_reviewed_image_coordinates_and_actual_note_time(source):
    pin = write_notes(source, [interval(end=1.5)])
    cfg = settings(source, pin, "score")
    with closing(Compositor(project_with(source, pin, "score", **cfg), layer_cache=False)) as owner:
        before = owner.frame(0)[0]
        assert before.getpixel((130, 90))[:3] == (0, 0, 0)
        active = owner.frame(30)[0]
        assert active.getpixel((130, 90))[:3] == (82, 214, 207)
        assert owner.frame_reports()["notes"]["highlighted_glyphs"] == [0]
        assert owner.frame(100)[0].getpixel((130, 90))[:3] == (0, 0, 0)


@pytest.mark.parametrize(
    "change",
    [
        {"reviewed": False},
        {"notes_sha256": "0" * 64},
        {"provenance": ""},
        {"glyphs": []},
        {"glyphs": [{"page": 0, "note_indices": [0], "bounds": [-1, 10, 40, 40]}]},
        {"glyphs": [{"page": 0, "note_indices": [0], "bounds": [20, 10, 40, 51]}]},
        {"glyphs": [{"page": 1, "note_indices": [0], "bounds": [20, 10, 40, 40]}]},
    ],
)
def test_score_needs_review_source_binding_and_covered_note_boxes(source, change):
    pin = write_notes(source)
    cfg = settings(source, pin, "score")["score"]
    cfg.update(change)
    with pytest.raises(ValueError):
        Compositor(project_with(source, pin, "score", score=cfg))


def test_equivalent_score_pin_paths_have_identical_layer_cache_descriptors(source):
    pin = write_notes(source)
    cfg = settings(source, pin, "score")
    with closing(Compositor(project_with(source, pin, "score", **cfg))) as owner:
        original = owner.layers[0].cache_config
    other = deepcopy(cfg)
    other["score"]["pages"][0]["image"]["path"] = str(source.base / "page.png")
    with closing(Compositor(project_with(source, pin, "score", **other))) as owner:
        assert owner.layers[0].cache_config["score"] == original["score"]


def test_event_burst_uses_existing_particle_engine_with_fps_independent_seeds(source):
    images = []
    for fps in (60, 120):
        data = deepcopy(source.data)
        data["fps"] = fps
        data["fps_reason"] = "Compare continuous-time event seeds"
        project = Project(data, source.base)
        emitter = ParticleEmitter(
            project,
            dict(
                id="event",
                type="emitter",
                bounds=[0, 0, 320, 180],
                style=dict(rate=0, lifetime=1, fade_in=0.1, emitter_width=0, emitter_height=0),
            ),
            lambda frame: None,
        )
        try:
            image = Image.new("RGBA", (320, 180))
            report = emitter.draw_event_burst(
                image, seconds=0.12345, event_id=7, now=0.4, count=12, point=(0.5, 0.7), raster_scale=1
            )
            assert report["active"] == 12 and image.getbbox() is not None
            images.append(image.tobytes())
        finally:
            emitter.close()
    assert images[0] == images[1]


def test_impact_budgets_and_zero_length_note_attacks_are_explicit(source):
    pin = write_notes(source, [interval(start=0.25, end=0.25)] * 20)
    with closing(
        Compositor(
            project_with(source, pin, "note-impacts", impacts={"max_events": 2, "max_particles": 3}),
            layer_cache=False,
        )
    ) as owner:
        owner.frame(20)
        report = owner.frame_reports()["notes"]
        assert report["active_particles"] <= 3
        assert report["recent_note_events"] == 20 and report["events_considered"] == 2
        assert report["event_budget_dropped"] == 18
        assert report["shared_particle_engine"] is True


def test_settings_cannot_be_silently_used_for_another_view(source):
    pin = write_notes(source)
    with pytest.raises(ValueError):
        Compositor(
            project_with(source, pin, "pitch-wheel", labels=False, arrangement={"time_mode": "overview"})
        )
