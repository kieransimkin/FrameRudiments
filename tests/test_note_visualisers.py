"""Independent wire/timing expectations and real compositor note pixels."""

from copy import deepcopy
from hashlib import sha256
import socket
import wave

import mido
import pytest

import test_midi_controls_m15 as midi_fixture
from framerudiments import Compositor, LayerFrameCache, NoteTimeline, Project
from framerudiments.midi_notes import MIDINoteCapture, NoteEvents, decode_note_message, import_midi_notes
from framerudiments.note_renderers import key_geometry

backend = midi_fixture.backend


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.wav"
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 48000, 96000, "NONE", "not compressed"))
        out.writeframes(b"\0\0" * 96000)
    return Project(
        {
            "schema": "framerudiments.project.v1",
            "width": 320,
            "height": 180,
            "fps": 60,
            "purpose": "visualiser",
            "clock": {"bpm": 120, "authority": "synthetic test", "status": "synthetic"},
            "audio": {
                "source": {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()},
                "sample_rate": 48000,
                "start_sample": 24000,
                "end_sample": 72000,
                "repeats": 2,
                "fade_seconds": 0,
            },
            "render_quality": {"enabled": False},
            "layers": [],
        },
        tmp_path,
    )


def interval(key=60, start=0.25, end=0.75, **kwargs):
    return dict(key=key, start_seconds=start, end_seconds=end, **kwargs)


def write_notes(source, rows=None, name="notes.json"):
    return NoteTimeline.from_notes(
        source, rows if rows is not None else [interval()], provenance={"kind": "authored synthetic test"}
    ).write(source.base / name)


def project_with(source, pin, view="piano-roll", **cfg):
    data = deepcopy(source.data)
    data["layers"] = [
        dict(id="notes", type="notes", file=pin, view=view, bounds=[0, 0, source.width, source.height], **cfg)
    ]
    return Project(data, source.base)


def test_wire_vectors_and_sustain(source):
    events = NoteEvents(source)
    for seconds, raw in [
        (0.0, [0x90, 60, 100]),
        (0.25, [0xB0, 64, 127]),
        (0.5, [0x90, 60, 0]),
        (1.0, [0xB0, 64, 0]),
    ]:
        events.add(seconds, raw)
    (note,) = events.finish(provenance={"kind": "wire vectors"}).notes
    assert (note.start_seconds, note.release_seconds, note.end_seconds) == (0.0, 0.5, 1.0)
    assert note.velocity == 100 and note.channel == 1
    assert decode_note_message([0x9F, 127, 1]) == ("on", 16, 127, 1)
    assert decode_note_message([0x8F, 127, 64]) == ("off", 16, 127, 64)


@pytest.mark.parametrize(
    "message", [[0x90, 60], [0x90, 128, 2], [0x90, True, 2], [0xA0, 60, 40], [0xF0, 1, 2], [0xB0, 7, 2]]
)
def test_unsupported_or_invalid_wire(message):
    with pytest.raises(ValueError):
        decode_note_message(message)


def test_repeated_pitch_channels_and_panic(source):
    events = NoteEvents(source)
    for seconds, raw in [
        (0.0, [0x90, 60, 20]),
        (0.1, [0x90, 60, 110]),
        (0.2, [0x91, 60, 90]),
        (0.3, [0x80, 60, 0]),
        (0.4, [0xB0, 64, 127]),
        (0.5, [0xB0, 123, 0]),
        (0.6, [0xB0, 120, 0]),
        (0.7, [0x81, 60, 0]),
    ]:
        events.add(seconds, raw)
    a, b, c = events.finish(provenance={}).notes
    assert (a.release_seconds, a.end_seconds) == (0.3, 0.3)
    assert (b.release_seconds, b.end_seconds) == (0.5, 0.6)
    assert (c.channel, c.release_seconds, c.end_seconds) == (2, 0.7, 0.7)


def test_boundary_intervals_and_detached_document(source):
    take = NoteTimeline.from_notes(source, [interval()], provenance={})
    assert take.active(0.25)[0].key == 60
    assert take.active(0.75) == ()
    data = take.data
    data["notes"][0]["key"] = 62
    assert take.data["notes"][0]["key"] == 60
    assert take.window(0.75, 1) == ()


@pytest.mark.parametrize(
    "change",
    [
        dict(key=128),
        dict(channel=0),
        dict(velocity=0),
        dict(track=128),
        dict(start_seconds=-1),
        dict(end_seconds=3),
        dict(release_seconds=0.8),
        dict(extra=True),
        dict(key=True),
        dict(start_seconds=float("nan")),
    ],
)
def test_interval_rejection(source, change):
    row = {**interval(), **change}
    with pytest.raises(ValueError):
        NoteTimeline.from_notes(source, [row], provenance={})


def test_source_rejection_temporal_compatibility_and_exclusive_write(source):
    take = NoteTimeline.from_notes(source, [interval()], provenance={})
    data = take.data
    data["source"]["repeats"] = 2.0
    with pytest.raises(ValueError):
        NoteTimeline(source, data)
    path = source.base / "exclusive.json"
    pin = take.write(path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        take.write(path)
    assert path.read_bytes() == before
    from framerudiments.super_sampling import _virtual_project

    virtual = _virtual_project(source, 4)
    assert NoteTimeline.load(virtual, pin).notes == take.notes
    changed = deepcopy(source.data)
    changed["audio"]["start_sample"] = 0
    with pytest.raises(ValueError, match="source identity"):
        NoteTimeline(Project(changed, source.base), take.data)


def test_index_matches_linear_windows(source):
    rows = [interval(key=40 + i % 50, start=i / 1000, end=min(2.0, i / 1000 + 0.035)) for i in range(1900)]
    rows.append(interval(key=99, start=0, end=2))
    take = NoteTimeline.from_notes(source, rows, provenance={})
    for low, high in [(0.0, 0.1), (0.5, 0.75), (1.99, 2.0), (0.3, 0.3)]:
        expected = tuple(n for n in take.notes if n.start_seconds <= high and n.end_seconds > low)
        assert take.window(low, high) == expected


def test_independent_running_status_smf_vector(source):
    # Type 0, PPQN 480. At tick480 running-status note-on velocity0 ends C4.
    payload = bytes.fromhex("4d546864000000060000000101e04d54726b0000000c00903c6483603c0000ff2f00")
    path = source.base / "independent.mid"
    path.write_bytes(payload)
    take = import_midi_notes(
        source, {"path": str(path), "sha256": sha256(payload).hexdigest()}, offset_seconds=0
    )
    (note,) = take.notes
    assert (note.key, note.start_seconds, note.release_seconds) == (60, 0.0, 0.5)


def test_smf_tempo_change_and_explicit_alignment(source):
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    midi.tracks.append(
        mido.MidiTrack(
            [
                mido.MetaMessage("set_tempo", tempo=500000, time=0),
                mido.MetaMessage("set_tempo", tempo=1000000, time=480),
            ]
        )
    )
    midi.tracks.append(
        mido.MidiTrack(
            [
                mido.Message("note_on", note=60, velocity=90, time=0),
                mido.Message("note_off", note=60, time=480),
                mido.Message("note_on", note=64, velocity=100, time=0),
                mido.Message("note_off", note=64, time=480),
            ]
        )
    )
    path = source.base / "tempo.mid"
    midi.save(path)
    pin = {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}
    take = import_midi_notes(source, pin, offset_seconds=0)
    assert [(n.start_seconds, n.end_seconds, n.track) for n in take.notes] == [(0.0, 0.5, 1), (0.5, 1.5, 1)]
    shifted = import_midi_notes(source, pin, offset_seconds=-0.25)
    assert [(n.start_seconds, n.end_seconds) for n in shifted.notes] == [(0.0, 0.25), (0.25, 1.25)]


@pytest.mark.parametrize("kind,ppqn", [(2, 480), (0, -24)])
def test_unsupported_smf_time_base(source, kind, ppqn):
    midi = mido.MidiFile(type=kind, ticks_per_beat=ppqn)
    midi.tracks.append(mido.MidiTrack())
    path = source.base / "unsupported.mid"
    midi.save(path)
    with pytest.raises(ValueError, match="PPQN"):
        import_midi_notes(
            source, {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}, offset_seconds=0
        )


def test_native_note_capture_ownership_and_replay(source, backend, monkeypatch):
    capture = MIDINoteCapture(source, api="jack", port_name="Exact MIDI input")
    assert backend.instances == []
    with capture:
        native = backend.instances[-1]
        for frame in range(source.frame_count):
            if frame == 15:
                native.queue.append([0x90, 60, 104])
            if frame == 45:
                native.queue.append([0x90, 60, 0])
            capture.sample(frame)
    assert native.deleted and capture.receiver.closed and capture.receiver.native is None
    take = capture.freeze()
    (note,) = take.notes
    assert (note.start_seconds, note.end_seconds) == (0.25, 0.75)
    pin = take.write(source.base / "captured.json")
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("Replay opened a socket"))
    before = len(backend.instances)
    compositor = Compositor(project_with(source, pin, note_range=[60, 72]))
    try:
        a = compositor.frame(15)[0].tobytes()
        compositor.frame(80)
        assert compositor.frame(15)[0].tobytes() == a
        assert compositor.frame_reports()["notes"]["active_keys"] == [60]
    finally:
        compositor.close()
    assert len(backend.instances) == before


def test_capture_failure_cannot_freeze(source, backend):
    capture = MIDINoteCapture(source, api="jack", port_name="Exact MIDI input")
    with capture:
        with pytest.raises(ValueError, match="starting at zero"):
            capture.sample(1)
    assert backend.instances[0].deleted
    with pytest.raises(ValueError, match="healthy"):
        capture.freeze()


def test_keyboard_geometry_and_release_pixels(source):
    geometry = key_geometry(60, 72)
    assert geometry[60] == (0.0, 1 / 8, False)
    sharp_left, sharp_right, black = geometry[61]
    assert black and (sharp_left + sharp_right) / 2 == pytest.approx(1 / 8)
    pin = write_notes(source, [interval(key=60), interval(key=61, start=0.5, end=1)])
    with_compositor = Compositor(project_with(source, pin, note_range=[60, 72]), layer_cache=False)
    try:
        a = with_compositor.frame(15)[0]
        assert with_compositor.frame_reports()["notes"]["active_keys"] == [60]
        b = with_compositor.frame(45)[0]
        assert with_compositor.frame_reports()["notes"]["active_keys"] == [61]
        assert a.tobytes() != b.tobytes()
        assert a.getpixel((15, 165)) != b.getpixel((15, 165))
    finally:
        with_compositor.close()


def test_rising_view_does_not_show_future_notes(source):
    pin = write_notes(source)
    empty = write_notes(source, [], "empty.json")
    a = Compositor(project_with(source, pin, direction="rising", note_range=[60, 72]))
    b = Compositor(project_with(source, empty, direction="rising", note_range=[60, 72]))
    try:
        assert a.frame(0)[0].tobytes() == b.frame(0)[0].tobytes()
        assert a.frame_reports()["notes"]["visible_notes"] == 0
        assert a.frame(30)[0].tobytes() != b.frame(30)[0].tobytes()
    finally:
        a.close()
        b.close()


def test_highway_requires_complete_authored_map(source):
    pin = write_notes(source)
    with pytest.raises(ValueError, match="explicit note_map"):
        Compositor(project_with(source, pin, view="note-highway"))
    with pytest.raises(ValueError, match="no explicit highway lane"):
        Compositor(project_with(source, pin, view="note-highway", note_map={"61": 0}))
    compositor = Compositor(project_with(source, pin, view="note-highway", note_map={"60": 2}))
    try:
        a = compositor.frame(0)[0].tobytes()
        b = compositor.frame(15)[0].tobytes()
        assert a != b and compositor.frame_reports()["notes"]["active_keys"] == [60]
    finally:
        compositor.close()


def test_cache_reuses_other_note_layer_and_preserves_reports(source, monkeypatch):
    pin = write_notes(source)
    project = project_with(source, pin, note_range=[60, 72])
    project.data["layers"].append({**deepcopy(project.data["layers"][0]), "id": "other", "opacity": 0.3})
    cache = LayerFrameCache(source.base / "cache")
    first = Compositor(project, layer_cache=cache)
    try:
        first.frame(15)
    finally:
        first.close()
    changed = deepcopy(project.data)
    changed["layers"][0]["opacity"] = 0.5
    second = Compositor(Project(changed, source.base), layer_cache=cache)
    counts = {"notes": 0, "other": 0}
    for layer in second.layers:
        draw = layer.draw

        def counted(image, ctx, layer=layer, draw=draw):
            counts[layer.id] += 1
            return draw(image, ctx)

        monkeypatch.setattr(layer, "draw", counted)
    try:
        cached = second.frame(15)[0].tobytes()
        assert counts == {"notes": 1, "other": 0}
        second.frame_reports()["other"]["active_keys"].append(127)
        second.frame(15)
        assert second.frame_reports()["other"]["active_keys"] == [60]
        reference = Compositor(Project(changed, source.base), layer_cache=False)
        try:
            assert reference.frame(15)[0].tobytes() == cached
        finally:
            reference.close()
    finally:
        second.close()
        cache.close()


def test_true_temporal_sampling_accepts_pinned_notes(source):
    pin = write_notes(source)
    project = project_with(source, pin, note_range=[60, 72])
    project.data["render_quality"] = {
        "enabled": True,
        "spatial_scale": 2,
        "temporal_samples": 4,
        "antialias": 0,
    }
    project = Project(project.data, source.base)
    compositor = Compositor(project)
    try:
        image, _ = compositor.frame(16)
        assert image.size == (320, 180)
        assert compositor.frame_reports()["notes"]["source_sha256"] == pin["sha256"]
    finally:
        compositor.close()


def test_shared_manifest_keeps_note_source_evidence(source):
    pin = write_notes(source)
    owner = Compositor(project_with(source, pin))
    try:
        owner.frame(15)
        proof = owner.evidence()
        assert proof["note_layers"]["notes"]["sha256"] == pin["sha256"]
        assert proof["note_layers"]["notes"]["clock"].startswith("continuous output seconds")
        assert proof["motions"] == {}
    finally:
        owner.close()


def test_reopening_note_owners_keeps_input_plan_and_checks_bytes(source):
    pin = write_notes(source)
    project = project_with(source, pin)
    first = Compositor(project)
    first.close()
    before = deepcopy(project.summary())
    second = Compositor(project)
    try:
        assert project.summary() == before
        assert len(project.inputs) == 2  # soundtrack and one note timeline
    finally:
        second.close()
    path = source.base / "notes.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Input hash mismatch"):
        NoteTimeline.load(project, pin)


def test_note_cli_import_and_project_validation(source, capsys, monkeypatch):
    import json
    from framerudiments.midi_m15_cli import main

    path = source.base / "source-project.json"
    path.write_text(json.dumps(source.data), encoding="utf-8")
    midi = source.base / "wire.mid"
    midi.write_bytes(bytes.fromhex("4d546864000000060000000101e04d54726b0000000c00903c6483603c0000ff2f00"))
    output = source.base / "cli-notes.json"
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("File CLI opened a socket"))
    assert main(["import-notes", str(path), str(midi), "--offset-seconds", "0", "--output", str(output)]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["status"] == "imported" and imported["notes"]["notes"] == 1
    assert imported["file"]["sha256"] == sha256(output.read_bytes()).hexdigest()
    project = project_with(source, imported["file"])
    path.write_text(json.dumps(project.data), encoding="utf-8")
    assert main(["validate-notes", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "validated" and result["replay_device_activity"] is False
    assert result["note_layers"][0]["sha256"] == imported["file"]["sha256"]


def test_note_cli_requires_explicit_alignment(capsys):
    from framerudiments.midi_m15_cli import main

    with pytest.raises(SystemExit) as error:
        main(["import-notes", "missing-project.json", "missing.mid", "--output", "unused.json"])
    assert error.value.code == 2
    assert "--offset-seconds" in capsys.readouterr().err
