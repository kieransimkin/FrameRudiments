"""Independent arrangement geometry, grouping, onset and shared-cache checks."""

from copy import deepcopy
from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import shutil
import pytest
from framerudiments import Compositor as NativeCompositor, LayerFrameCache, NoteTimeline, Project
import test_note_visualisers as native_fixtures
from test_note_visualisers import interval, write_notes, project_with

source = native_fixtures.source


def Compositor(*args, **kwargs):
    return closing(NativeCompositor(*args, **kwargs))


def arrangement(source, rows=None, **settings):
    pin = write_notes(source, rows)
    return project_with(source, pin, "arrangement", labels=False, arrangement=settings)


def test_independent_horizontal_note_geometry_and_playhead(source):
    data = deepcopy(source.data)
    data.update(width=400, height=240)
    base = Project(data, source.base)
    pin = write_notes(base, [interval(start=0.5, end=1.5, velocity=127)])
    project = project_with(
        base,
        pin,
        "arrangement",
        labels=False,
        lookahead_seconds=1,
        history_seconds=1,
        palette=["#00ff00"],
        arrangement={"parts": [{"label": "Piano", "note_range": [60, 71]}]},
    )
    with Compositor(project, layer_cache=False) as owner:
        image = owner.frame(60)[0]
        # Seconds .5..1.5 map to x146..310, pitch60 is the bottom row.
        assert image.getpixel((260, 218))[:3] == (0, 255, 0)
        assert image.getpixel((140, 218))[:3] != (0, 255, 0)
        assert image.getpixel((228, 90))[:3] == (238, 255, 249)
        report = owner.frame_reports()["notes"]
        assert report["window_seconds"] == [0, 2]
        assert report["parts"] == [{"id": "part-1", "sounding": 1}]


def test_track_groups_and_explicit_channel_selection(source):
    project = arrangement(source, [interval(track=1), interval(key=67, track=2, channel=3)])
    with Compositor(project) as owner:
        assert [p["label"] for p in owner.layers[0].extra.evidence["parts"]] == ["Track 2", "Track 3"]
        owner.frame(30)
        assert [p["sounding"] for p in owner.frame_reports()["notes"]["parts"]] == [1, 1]
    pin = project.data["layers"][0]["file"]
    selected = project_with(source, pin, "arrangement", labels=False, tracks=[2])
    with Compositor(selected) as owner:
        owner.frame(30)
        assert owner.frame_reports()["notes"]["active_keys"] == [67]


def test_channel_grouping_keeps_simultaneous_equal_pitch_voices(source):
    with Compositor(arrangement(source, [interval(), interval(channel=2)], group_by="channel")) as owner:
        assert len(owner.layers[0].extra.parts) == 2
        owner.frame(30)
        assert owner.frame_reports()["notes"]["active_notes"] == 2


def test_overview_is_static_except_for_current_playhead_and_active_notes(source):
    project = arrangement(source, [interval(), interval(key=67, start=1, end=1.5)], time_mode="overview")
    with Compositor(project, layer_cache=False) as owner:
        first = owner.frame(20)[0].tobytes()
        static = owner.layers[0].extra._overview
        owner.frame(80)
        assert owner.layers[0].extra._overview is static
        assert owner.frame(20)[0].tobytes() == first
        assert owner.frame_reports()["notes"]["window_seconds"] == [0, 2]


@pytest.mark.parametrize(
    "settings",
    [
        {"time_mode": "automatic"},
        {"group_by": "instrument"},
        {"parts": []},
        {"parts": [{"label": "A", "tracks": [1]}]},
        {"parts": [{"id": "same", "label": "A"}, {"id": "same", "label": "B"}]},
        {"parts": [{"label": "A"}, {"label": "B"}]},
        {"parts": [{"label": "A", "note_range": [61, 72]}]},
        {"parts": [{"label": "A", "tracks": [True]}]},
        {"parts": [{"label": "A\nB"}]},
        {"grid_seconds": 0},
        {"label_fraction": 0.9},
        {"unknown": True},
    ],
)
def test_reject_ambiguous_unassigned_or_invalid_parts(source, settings):
    with pytest.raises(ValueError):
        Compositor(arrangement(source, **settings))


def test_default_named_parts_require_a_real_pinned_font(source):
    pin = write_notes(source)
    with pytest.raises(ValueError, match="font"):
        Compositor(project_with(source, pin, "arrangement"))


def test_default_part_names_and_font_are_consumed(source):
    path = Path(os.environ["FRAMERUDIMENTS_TEST_FONT"])
    target = source.base / "font.ttf"
    shutil.copyfile(path, target)
    data = deepcopy(source.data)
    data["font"] = {"path": "font.ttf", "sha256": sha256(target.read_bytes()).hexdigest()}
    base = Project(data, source.base)
    take = NoteTimeline.from_notes(
        base, [interval(track=1)], provenance={"track_names": {"1": "Authored bass"}}
    )
    pin = take.write(source.base / "named.json")
    with Compositor(project_with(base, pin, "arrangement")) as owner:
        assert owner.layers[0].labels is True
        assert owner.layers[0].cfg["labels"] is True
        assert owner.layers[0].extra.parts[0].label == "Authored bass"
        owner.frame(30)


def test_point_hits_have_an_indexed_onset_even_with_zero_duration(source):
    take = NoteTimeline.from_notes(
        source, [interval(start=0.25, end=0.25), interval(start=0.5, end=0.75)], provenance={}
    )
    assert take.window(0.25, 0.25) == ()
    assert take.onsets(0.25, 0.25) == (take.notes[0],)
    assert take.onsets(0.25, 0.5) == take.notes
    with pytest.raises(ValueError):
        take.onsets(0.5, 0.25)


def test_edit_one_arrangement_preserves_a_separate_keyboard_cache(source, tmp_path):
    pin = write_notes(source, [interval(), interval(key=67, start=1, end=1.5)])
    data = deepcopy(source.data)
    data["layers"] = [
        dict(
            id="arrangement",
            type="notes",
            view="arrangement",
            labels=False,
            file=pin,
            bounds=[0, 0, 160, 180],
        ),
        dict(id="keys", type="notes", view="piano-roll", file=pin, bounds=[160, 0, 320, 180]),
    ]
    cache = LayerFrameCache(tmp_path / "layer-cache")
    with Compositor(Project(data, source.base), layer_cache=cache) as owner:
        owner.frame(30)
    changed = deepcopy(data)
    changed["layers"][0]["arrangement"] = {"time_mode": "overview"}
    with Compositor(Project(changed, source.base), layer_cache=cache) as owner:
        calls = {"arrangement": 0, "keys": 0}
        for layer in owner.layers:
            original, name = layer.draw, layer.id

            def draw(image, ctx, original=original, name=name):
                calls[name] += 1
                return original(image, ctx)

            layer.draw = draw
        actual = owner.frame(30)[0].tobytes()
        assert calls == {"arrangement": 1, "keys": 0}
    with Compositor(Project(changed, source.base), layer_cache=False) as owner:
        assert owner.frame(30)[0].tobytes() == actual
    cache.close()


@pytest.mark.parametrize("view", ["arrangement", "pitch-wheel"])
def test_non_keyboard_views_admit_full_midi_pitch_range(source, view):
    pin = write_notes(source, [interval(key=0), interval(key=127)])
    with Compositor(project_with(source, pin, view, labels=False)) as owner:
        owner.frame(30)
        assert owner.frame_reports()["notes"]["active_keys"] == [0, 127]
        assert owner.layers[0].evidence["selected_notes"] == 2
