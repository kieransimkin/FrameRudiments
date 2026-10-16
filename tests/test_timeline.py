from fractions import Fraction
import pytest
from framerudiments.timeline import BeatClock, Event, EventTrack, frame_ledger, parse_lrc, parse_cue, half_up
from framerudiments.subtitles import intervals


def test_half_up_and_simultaneous_events_have_one_visible_state():
    assert half_up(Fraction(1, 2)) == 1
    assert half_up(Fraction(-1, 2)) == 0
    events = [Event(.025, "first", 0), Event(.0251, "last", 1), Event(.5, "", 2)]
    ledger = frame_ledger(events, start_sample=0, end_sample=48000, sample_rate=48000, fps=60)
    assert EventTrack(ledger).at(2)["text"] == "last"
    assert EventTrack(ledger).at(30)["text"] == ""
    assert list(intervals(ledger, 60)) == [(2, 30, "last")]


def test_mid_line_excerpt_carries_and_loop_boundary_resets():
    events = [Event(0, "carry", 0), Event(1.5, "new", 1), Event(2, "out", 2)]
    ledger = frame_ledger(events, start_sample=48000, end_sample=96000, sample_rate=48000, fps=60, repeats=2)
    assert [(e["frame"], e["text"]) for e in ledger] == [(0, "carry"), (30, "new"), (60, "carry"), (90, "new")]
    assert ledger[0]["carried"]
    assert all(abs(e["frame_error_seconds"]) <= 1/120 for e in ledger)


def test_lrc_multiple_stamps_clears_structure_and_utf8(tmp_path):
    path = tmp_path / "lyrics.lrc"
    path.write_text("\ufeff[offset:100]\n[00:00.00][00:02.00]Café — it's {data}\\N\n[00:01.00]\n[00:03.00][Chorus]\n", encoding="utf-8")
    result = parse_lrc(path)
    assert [e.source_seconds for e in result] == [.1, 1.1, 2.1, 3.1]
    assert result[0].text == "Café — it's {data}\\N"
    assert result[1].text == result[3].text == ""
    assert parse_lrc(path, lyrics=False)[-1].text == "[Chorus]"
    path.write_text("[00:60.00]invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_lrc(path)


def test_cue_is_75_hz_not_video_fps(tmp_path):
    path = tmp_path / "sections.cue"
    path.write_text('TITLE "verse"\nINDEX 01 00:01:30\n', encoding="utf-8")
    assert parse_cue(path)[0].source_seconds == 1.4


def test_negative_pips_and_variable_grid():
    assert BeatClock(bpm=120).pip_at(-.001) == -1
    grid = BeatClock(beats=[1, 1.5, 2.25])
    assert grid.beat_at(1.875) == 1.5
    assert grid.beat_at(.5) == -1
    assert BeatClock.quantize_ticks(24) == 32
    assert BeatClock.quantize_ticks(40) == 48
    with pytest.raises(ValueError):
        BeatClock()
    with pytest.raises(ValueError):
        BeatClock(beats=[1, 1])
