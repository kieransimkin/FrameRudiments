"""StemLab/DanceMoves full tempo map + per-line/per-word LRC regression tests."""

import copy
from fractions import Fraction
import os
from pathlib import Path
import wave

from PIL import Image
import pytest

from framerudiments.beat_timeline import BeatTimelineClock, phase_pips_at_frame
from framerudiments.io import sha256, write_json
from framerudiments.word_timing import (
    parse_word_lrc,
    word_frame_ledger,
    assign_words_to_lines,
    WordTrack,
    word_export_ledger,
    draw_highlighted_line,
)


SHA = "a" * 64


def beat_document(origin=0):
    frames = [1000, 5000, 9000, 14160, 18080]
    doc = dict(
        schema="stemlab.beat-timeline.v1",
        pips_per_beat=64,
        source_audio=dict(sha256=SHA, sample_rate=8000, num_frames=24000, num_channels=1),
        consensus_sha256="b" * 64,
        origin_beat=origin,
        review_status="estimated",
        beats=[dict(beat=i, frame=f, source="consensus", reference_frame=f) for i, f in enumerate(frames)],
    )
    left = [(i, f) for i, f in enumerate(frames)]
    offset = phase_pips_at_frame(left, origin, 0)
    selected = frames[origin]
    doc["sync"] = dict(
        beat_1_index=origin,
        beat_1_source_frame=selected,
        beat_1_start_seconds=selected / 8000,
        beat_1_start_pips=float(-offset),
        sync_phase_offset_pips=float(offset),
        exact_phase_offset_pips=str(offset),
        canonical_first_beat_pip=0,
        pips_per_beat=64,
    )
    doc["tempo_segments"] = [
        dict(
            from_beat=i,
            to_beat=i + 1,
            start_frame=a,
            end_frame=b,
            bpm=60 * 8000 / (b - a),
            bpm_exact=str(Fraction(60 * 8000, b - a)),
        )
        for i, (a, b) in enumerate(zip(frames, frames[1:]))
    ]
    doc["beat_timeline"] = [
        dict(
            beat=i, source_frame=f, time_seconds=f / 8000, pip=64 * (i - origin), consensus_reference_frame=f
        )
        for i, f in enumerate(frames)
    ]
    return doc


def clock(data=None):
    return BeatTimelineClock(data or beat_document(), source_sha256=SHA, sample_rate=8000, end_sample=21000)


def test_native_sample_frames_and_variable_bpm_are_authoritative():
    c = clock()
    assert c.phase_pips_at_frame(1000) == 0
    assert c.phase_pips_at_frame(5000) == 64
    assert c.phase_pips_at_frame(9000) == 128
    assert c.phase_pips_at_frame(14160) == 192
    assert c.phase_pips_at_frame(0) == -16
    assert c.beat_at(0.125) == 0
    assert c.beat_at(0.625) == 1
    assert c.beat_at(1.125) == 2
    assert c.beat_at((9000 + 2580) / 8000) == pytest.approx(2.5)
    assert c.tempo_at(0.625) == pytest.approx(120)
    assert c.tempo_at(1.3) == pytest.approx(60 * 8000 / 5160)
    assert c.tempo_at(2.5) == pytest.approx(60 * 8000 / 3920)
    assert c.pip_at(0.125) == 0
    assert c.bpm == 120  # display-only hint, not used for phase


def test_beat_1_can_be_later_and_extrapolation_is_fractional():
    c = clock(beat_document(origin=2))
    assert c.pip_at(1.125) == 0
    assert c.phase_pips_at_frame(0) == -144
    assert c.phase_pips_at_frame(19080) == Fraction(128) + Fraction(64 * 1000, 3920)
    assert c.beat_at(0) == -2.25


@pytest.mark.parametrize(
    "modify",
    [
        lambda d: d["source_audio"].update(sha256="c" * 64),
        lambda d: d["source_audio"].update(sample_rate=16000),
        lambda d: d["source_audio"].update(num_frames=16000),
        lambda d: d.update(pips_per_beat=16),
        lambda d: d.update(origin_beat=99),
        lambda d: d["beats"][1].update(frame=1000),
        lambda d: d["beats"][2].update(source="consensus", reference_frame=8000),
        lambda d: d["beats"][2].update(frame=8000, source="manual", reference_frame=9000),
        lambda d: d["sync"].update(exact_phase_offset_pips="-15"),
        lambda d: d["tempo_segments"][0].update(bpm=90),
        lambda d: d["beat_timeline"][0].update(pip=999),
        lambda d: d.update(review_status="confirmed"),
        lambda d: d.update(consensus_sha256="bad"),
    ],
)
def test_conflicting_timelines_fail_closed(modify):
    doc = beat_document()
    modify(doc)
    with pytest.raises(ValueError):
        clock(doc)


def test_word_parser_accepts_precise_plain_and_enhanced_lrc(tmp_path):
    p = tmp_path / "words.lrc"
    p.write_text("\ufeff[offset:0]\n[00:00.125]Hello\n[00:00.500]world!\n", encoding="utf-8")
    rows = parse_word_lrc(p)
    assert [w.text for w in rows] == ["Hello", "world!"]
    assert rows[0].time == Fraction(1, 8)
    p.write_text("[00:00.12]<00:00.125>Hello <00:00.500>world!\n", encoding="utf-8")
    assert [(w.text, w.time) for w in parse_word_lrc(p)] == [(w.text, w.time) for w in rows]
    p.write_text("[00:00.00]<00:00.10>Two words\n", encoding="utf-8")
    with pytest.raises(ValueError, match="multiple words"):
        parse_word_lrc(p)


def test_word_ledger_repeats_without_carrying_stale_word(tmp_path):
    p = tmp_path / "words.lrc"
    p.write_text("[00:00.10]We\n[00:00.40]go\n[00:00.80]\n", encoding="utf-8")
    ev = word_frame_ledger(
        parse_word_lrc(p),
        start_sample=0,
        end_sample=8000,
        sample_rate=8000,
        fps=20,
        repeats=2,
        frame_count=40,
    )
    t = WordTrack(ev)
    assert t.at(1) is None
    assert t.at(2)["text"] == "We"
    assert t.at(8)["text"] == "go"
    assert t.at(16) is None
    assert t.at(20) is None
    assert t.at(22)["text"] == "We"
    assert t.at(38) is None
    assert [r["frame"] for r in ev if r["text"]] == [2, 8, 22, 28]


def test_words_match_line_ledger_with_punctuation_and_skipped_tokens():
    lines = [
        dict(frame=0, text="Hello, lovely world!", cycle=0, source_order=0),
        dict(frame=20, text="Next line", cycle=0, source_order=1),
    ]
    words = [
        dict(frame=3, text="Hello", cycle=0, source_order=0),
        dict(frame=11, text="world", cycle=0, source_order=1),
        dict(frame=24, text="Next", cycle=0, source_order=2),
    ]
    assign_words_to_lines(words, lines)
    track = WordTrack(words)
    assert track.state(11, lines[0]) == dict(text="world", active=2, completed=(0,))
    assert track.state(19, lines[0])["active"] == 2
    assert track.state(24, lines[1])["active"] == 0
    with pytest.raises(ValueError, match="does not match"):
        assign_words_to_lines([dict(frame=3, text="different", cycle=0, source_order=0)], lines)


def test_export_word_interval_is_cut_on_line_change():
    words = [
        dict(frame=4, text="hello", cycle=0, source_order=0),
        dict(frame=9, text="world", cycle=0, source_order=1),
    ]
    lines = [
        dict(frame=0, text="hello world", cycle=0, source_order=0),
        dict(frame=12, text="new words", cycle=0, source_order=1),
    ]
    track = word_export_ledger(words, lines)
    assert [t["frame"] for t in track] == [0, 4, 9, 12]
    assert track[-1]["text"] == ""


def test_karaoke_overlay_uses_existing_textblock_layout():
    from PIL import ImageFont

    font_file = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font_file:
        font_file = next(
            (
                path
                for path in (
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "C:/Windows/Fonts/arialbd.ttf",
                    "C:/Windows/Fonts/arial.ttf",
                )
                if Path(path).is_file()
            ),
            None,
        )
    if not font_file or not Path(font_file).is_file():
        pytest.skip("Requires an installed pinned test font")
    from PIL import ImageDraw

    class Block:
        def __init__(self):
            self.box = (0, 0, 360, 90)
            self.stroke = 0
            self.spacing = 0
            self.font = ImageFont.truetype(font_file, 32)

        def layout(self, _):
            return self.font, ("Hello world",), ((0, 0, 200, 40),), 40

        def draw(self, image, value):
            ImageDraw.Draw(image).text((80, 24), value, font=self.font, fill="white")
            return [(80, 24, 280, 64)]

    b = Block()
    original = Image.new("RGBA", (360, 90))
    changed = original.copy()
    draw_highlighted_line(b, changed, "Hello world", active=1, colour="#ffdd00")
    assert changed.getbbox() is not None
    assert original.tobytes() != changed.tobytes()


def test_full_project_exact_beat_map_and_dual_subtitles(tmp_path):
    font = os.environ.get("FRAMERUDIMENTS_TEST_FONT")
    if not font:
        pytest.skip("Set FRAMERUDIMENTS_TEST_FONT for full compositor integration")
    from framerudiments.project import Project
    from framerudiments.compositor import Compositor

    wav = tmp_path / "master.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * 24000)
    doc = beat_document()
    doc["source_audio"]["sha256"] = sha256(wav)
    mp = tmp_path / "beat-timeline.json"
    write_json(mp, doc)
    line_path = tmp_path / "lines.lrc"
    word_path = tmp_path / "words.lrc"
    line_path.write_text("[00:00.00]Hello world\n[00:01.50]Next line\n", encoding="utf-8")
    word_path.write_text(
        "[00:00.125]Hello\n[00:00.625]world\n[00:01.625]Next\n[00:01.875]line\n", encoding="utf-8"
    )
    cfg = dict(
        schema="framerudiments.project.v1",
        purpose="canvas",
        width=360,
        height=180,
        audio=dict(
            source=dict(path=str(wav), sha256=sha256(wav)),
            sample_rate=8000,
            start_sample=0,
            end_sample=24000,
            fade_seconds=0,
        ),
        clock=dict(
            authority="edited synthetic beat grid",
            status="estimated",
            beat_timeline=dict(path=str(mp), sha256=sha256(mp)),
        ),
        font=dict(path=font, sha256=sha256(font)),
        lyrics=dict(path=str(line_path), sha256=sha256(line_path)),
        word_lyrics=dict(path=str(word_path), sha256=sha256(word_path)),
        captions=dict(box=[15, 35, 340, 90], font_size=22, word_mode="progressive"),
        word_captions=dict(box=[60, 105, 300, 150], font_size=18),
        layers=[],
        background=dict(colour="#121b26"),
    )
    p = Project(cfg, tmp_path)
    assert p.clock.beat_at(0.625) == 1
    assert p.words and p.words[1]["word_index"] == 0
    comp = Compositor(p)
    before = comp.frame(5)[0]
    during = comp.frame(40)[0]
    assert before.tobytes() != during.tobytes()
    comp.frame(60)
    assert comp.frame(40)[0].tobytes() == during.tobytes()
    assert comp.context(40).word["text"] == "world"
    assert comp.evidence()["word_subtitles"]["enabled"] is True
    assert p.summary()["clock"]["beat_timeline"]["schema"] == "stemlab.beat-timeline.v1"
    # A repeated excerpt does not stretch either LRC.
    repeat = copy.deepcopy(cfg)
    repeat["audio"].update(start_sample=4000, end_sample=16000, repeats=2)
    p2 = Project(repeat, tmp_path)
    assert p2.continuous_beat(90) == pytest.approx(
        p2.continuous_beat(0) + p2.clock.beat_at(2.0) - p2.clock.beat_at(0.5)
    )
