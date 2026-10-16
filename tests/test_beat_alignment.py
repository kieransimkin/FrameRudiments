import copy
import wave

import pytest

from framerudiments.io import sha256, write_json
from framerudiments.project import Project
from framerudiments.timeline import parse_lrc


def fixture(tmp_path):
    audio = tmp_path / "master.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 48000 * 4)
    reading = dict(
        schema="stemlab.beat-alignment.v1",
        source_sha256=sha256(audio),
        source_audio_verified=True,
        source_audio=dict(sample_rate=48000, num_frames=192000),
        bpm=120,
        exact_bpm="120",
        bpm_basis="combined_fit",
        bpm_status="estimated",
        phase_status="estimated",
        analysis_id="consensus",
        evidence_sha256="b" * 64,
        report_sha256="c" * 64,
        first_beat_seconds=0.125,
        first_beat_exact_seconds="1/8",
        offset_pips=-16,
        exact_offset_pips="-16",
        pips_per_beat=64,
        canonical_first_beat_pip=0,
    )
    path = tmp_path / "alignment.json"
    write_json(path, reading)
    data = dict(
        schema="framerudiments.project.v1",
        purpose="canvas",
        width=180,
        height=320,
        fps=60,
        audio=dict(
            source=dict(path="master.wav", sha256=sha256(audio)),
            sample_rate=48000,
            start_sample=0,
            end_sample=192000,
            fade_seconds=0,
        ),
        clock=dict(
            authority="Synthetic source-bound test reading",
            status="estimated",
            beat_alignment=dict(path="alignment.json", sha256=sha256(path)),
        ),
        layers=[],
        background=dict(colour="#101b2e"),
    )
    return data, reading, path


def test_first_beat_and_negative_prebeat_pips_preserve_source_timing(tmp_path):
    data, reading, path = fixture(tmp_path)
    project = Project(data, tmp_path)
    assert project.clock.pip_at(0.125) == 0
    assert project.clock.pip_at(0) == -16
    assert project.clock.pip_at(0.625) == 64
    assert project.source_time(0) == 0
    assert project.summary()["clock"]["alignment"]["report_sha256"] == "c" * 64
    assert project.summary()["clock"]["phase_status"] == "estimated"
    lyrics = tmp_path / "lyrics.lrc"
    lyrics.write_text("[00:00.125]Unshifted words\n[00:01.125]Later words\n", encoding="utf-8")
    ledger = project.ledger(parse_lrc(lyrics))
    assert [r["frame"] for r in ledger if r["text"]] == [8, 68]
    assert next(r for r in ledger if r["text"])["source_seconds"] == 0.125
    baseline = copy.deepcopy(data)
    baseline["clock"] = dict(bpm=120, authority="Original source clock", status="estimated")
    assert ledger == Project(baseline, tmp_path).ledger(parse_lrc(lyrics))


def test_excerpt_and_repeat_mapping_precedes_alignment_once(tmp_path):
    data, _, _ = fixture(tmp_path)
    data["audio"].update(start_sample=30000, end_sample=78000, repeats=2)
    project = Project(data, tmp_path)
    assert project.source_time(0) == 0.625
    assert project.clock.pip_at(project.source_time(0)) == 64
    assert project.clock.pip_at(project.source_time(60)) == 64
    assert project.continuous_beat(60) == pytest.approx(project.continuous_beat(0) + 2)


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "a" * 64},
        {"exact_offset_pips": "-15"},
        {"source_audio_verified": False},
        {"first_beat_seconds": 4},
        {"phase_status": "automatic"},
        {"exact_bpm": "1e999"},
    ],
)
def test_conflicting_readings_are_rejected(tmp_path, change):
    data, reading, path = fixture(tmp_path)
    reading.update(change)
    write_json(path, reading)
    data["clock"]["beat_alignment"]["sha256"] = sha256(path)
    with pytest.raises(ValueError):
        Project(data, tmp_path)


@pytest.mark.parametrize(
    "change",
    [{"origin": 0}, {"beat_grid": {}}, {"bpm": 145}, {"status": "confirmed"}, {"phase_status": "confirmed"}],
)
def test_no_double_origin_or_authority_promotion(tmp_path, change):
    data, _, _ = fixture(tmp_path)
    data["clock"].update(change)
    with pytest.raises(ValueError):
        Project(data, tmp_path)


def test_recording_clock_and_source_sample_bounds_remain_independent(tmp_path):
    from dancerudiments_authoring.recording import Recording, RecordingClock, write_recording
    from framerudiments.motion import Motion

    data, _, _ = fixture(tmp_path)
    project = Project(data, tmp_path)
    recording = Recording(
        "source_capture",
        ("level",),
        tuple((i / 511,) for i in range(512)),
        RecordingClock(120, "synthetic source capture", "confirmed", 0, "supplied", 0),
    )
    path = tmp_path / "recording.json"
    write_recording(path, recording)
    motion = Motion(
        dict(kind="recording", file=dict(path=str(path), sha256=sha256(path))), tmp_path, project.clock
    )
    assert motion.sample(0.5)[0] == pytest.approx(64 / 511)
    assert project.clock.pip_at(0.5) == 48
    wrong = copy.deepcopy(data)
    wrong["audio"]["end_sample"] = 192001
    with pytest.raises(ValueError, match="frame bounds"):
        Project(wrong, tmp_path)
