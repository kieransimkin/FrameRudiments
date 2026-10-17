"""Source time -> output time -> one authoritative encoded-frame ledger."""

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
import math
import re
from pathlib import Path

from .io import number

STAMP = re.compile(r"\[(\d+):(\d{1,2}(?:\.\d+)?)\]")
SECTION = re.compile(
    r"\[(?:intro|verse|chorus|bridge|outro|instrumental|spoken(?: word)?|pre[- ]?chorus)[^]]*\]", re.I
)


def half_up(value):
    value = value if isinstance(value, Fraction) else Fraction(str(value))
    return math.floor(value + Fraction(1, 2))


@dataclass(frozen=True)
class Event:
    source_seconds: float
    text: str
    order: int


def parse_lrc(path, *, lyrics=True):
    """Multiple stamps, BOM, empty clears and offset; structural text stays inert."""
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    offsets = [int(m[1]) for line in lines if (m := re.fullmatch(r"\[offset:([+-]?\d+)\]", line.strip()))]
    if len(offsets) > 1:
        raise ValueError("Multiple LRC offsets are ambiguous")
    offset = (offsets[0] if offsets else 0) / 1000
    out = []
    for line in lines:
        if not line.strip():
            continue
        matches = list(STAMP.finditer(line))
        if not matches:
            if re.fullmatch(r"\[[a-zA-Z][a-zA-Z0-9]*:[^\]]*\]", line.strip()):
                continue
            raise ValueError(f"Unrecognised timed line: {line!r}")
        if matches[0].start() != 0 or any(a.end() != b.start() for a, b in zip(matches, matches[1:])):
            raise ValueError(f"Malformed timestamps: {line!r}")
        text = line[matches[-1].end() :].strip()
        if lyrics and SECTION.fullmatch(text):
            text = ""  # retain the clear boundary; never render a structural label
        for m in matches:
            if float(m[2]) >= 60:
                raise ValueError(f"Invalid seconds: {m[0]}")
            out.append(Event(int(m[1]) * 60 + float(m[2]) + offset, text, len(out)))
    if not out:
        raise ValueError("No timed events")
    return sorted(out, key=lambda e: (e.source_seconds, e.order))


def parse_cue(path):
    """CD CUE INDEX 01 uses 75 subframes/second, independent of video fps."""
    title, out = "", []
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line.upper().startswith("TITLE "):
            title = line[6:].strip().strip('"')
        if m := re.fullmatch(r"INDEX\s+01\s+(\d+):(\d{2}):(\d{2})", line, re.I):
            if int(m[2]) >= 60 or int(m[3]) >= 75:
                raise ValueError(f"Invalid CUE index: {line}")
            out.append(Event(int(m[1]) * 60 + int(m[2]) + int(m[3]) / 75, title, len(out)))
    if not out or any(a.source_seconds > b.source_seconds for a, b in zip(out, out[1:])):
        raise ValueError("CUE needs ordered INDEX 01 events")
    return out


def frame_ledger(events, *, start_sample, end_sample, sample_rate, fps, repeats=1):
    start = Fraction(start_sample, sample_rate)
    duration = Fraction(end_sample - start_sample, sample_rate)
    result = []
    for cycle in range(repeats):
        before = [e for e in events if Fraction(str(e.source_seconds)) < start]
        selected = ([before[-1]] if before else []) + [
            e for e in events if start <= Fraction(str(e.source_seconds)) < start + duration
        ]
        if not selected or Fraction(str(selected[0].source_seconds)) > start:
            selected.insert(0, Event(float(start), "", -1))
        for e in selected:
            relative = max(Fraction(0), Fraction(str(e.source_seconds)) - start) + cycle * duration
            frame = half_up(relative * fps)
            result.append(
                dict(
                    source_seconds=e.source_seconds,
                    output_seconds=float(relative),
                    frame=frame,
                    frame_error_seconds=float(Fraction(frame, fps) - relative),
                    text=e.text,
                    source_order=e.order,
                    cycle=cycle,
                    carried=e.source_seconds < float(start),
                )
            )
    return result


class EventTrack:
    def __init__(self, events):
        self.events = events
        self.frames = [e["frame"] for e in events]

    def at(self, frame):
        index = bisect_right(self.frames, frame) - 1
        return self.events[index] if index >= 0 else None

    def neighbours(self, frame):
        index = bisect_right(self.frames, frame) - 1
        current = self.events[index] if index >= 0 else None
        previous = next((x for x in reversed(self.events[: max(0, index)]) if x["text"]), None)
        following = next((x for x in self.events[index + 1 :] if x["text"]), None)
        return previous, current, following


class BeatClock:
    def __init__(self, *, bpm=None, origin=0.0, beats=None):
        self.origin = number(origin, "beat origin")
        self.bpm = None if bpm is None else number(bpm, "BPM", minimum=0.00001)
        self.beats = [number(t, "beat time") for t in (beats or [])]
        if self.beats and (len(self.beats) < 2 or any(a >= b for a, b in zip(self.beats, self.beats[1:]))):
            raise ValueError("Beat grid needs two or more strictly increasing times")
        if not self.beats and self.bpm is None:
            raise ValueError("Supply a justified BPM or saved beat grid")

    def beat_at(self, source_seconds):
        if not self.beats:
            return (source_seconds - self.origin) * self.bpm / 60
        index = max(0, min(len(self.beats) - 2, bisect_right(self.beats, source_seconds) - 1))
        return index + (source_seconds - self.beats[index]) / (self.beats[index + 1] - self.beats[index])

    def pip_at(self, source_seconds, rate=1.0, phase_beats=0.0):
        return math.floor((self.beat_at(source_seconds) * rate + phase_beats) * 64)

    @staticmethod
    def quantize_ticks(ticks):
        ticks = number(ticks, "ticks", minimum=0)
        return half_up(ticks) if ticks <= 16 else half_up(ticks / 16) * 16
