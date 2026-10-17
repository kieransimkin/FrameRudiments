"""Per-word and enhanced LRC timings on the *original* source audio clock.

Both karaoke highlighting and an optional separate word-subtitle lane use this
ledger.  No BPM correction is ever applied to lyrics or audio cue times.
"""
from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import re
import unicodedata

from .timeline import half_up

STAMP = re.compile(r"\[(\d{1,9}):([0-5]\d)(?:\.(\d{1,30}))?\]")
ENHANCED = re.compile(r"<(\d{1,9}):([0-5]\d)(?:\.(\d{1,30}))?>")
OFFSET = re.compile(r"\[offset:\s*([+-]?\d{1,9})\s*\]", re.I)
METADATA = re.compile(r"\[[A-Za-z][A-Za-z0-9_-]*:[^\]]*\]")
MAX_WORDS = 100_000
MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class WordCue:
    time: Fraction
    text: str
    order: int


def _time(match):
    return Fraction(int(match[1]) * 60 + int(match[2])) + Fraction("0." + (match[3] or "0"))


def _word(raw):
    value = raw.strip()
    if value and len(value.split()) != 1:
        raise ValueError(f"Per-word timestamp contains multiple words: {value!r}")
    return value


def parse_word_lrc(path):
    """Accept [mm:ss.ff]word rows and [line]<mm:ss.ff>word <...>word.

    Times are exact Fractions until ledger rounding; BOM, source offsets and
    Unicode lyrics are supported. A timestamp with no word explicitly clears.
    """
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Word timing LRC exceeds 4 MiB")
    text = path.read_text(encoding="utf-8-sig")
    offset = Fraction(0)
    found_offset = False
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        off = OFFSET.fullmatch(line)
        if off:
            if found_offset:
                raise ValueError("Only one word timing offset is supported")
            found_offset = True
            # Match FrameRudiments' existing LRC offset convention for both files.
            offset = Fraction(int(off[1]), 1000)
            continue
        if METADATA.fullmatch(line):
            continue
        stamps = []
        i = 0
        while match := STAMP.match(line, i):
            stamps.append(_time(match))
            i = match.end()
        if not stamps:
            raise ValueError(f"Expected a leading LRC word timestamp: {line[:60]!r}")
        tail = line[i:]
        enhanced = list(ENHANCED.finditer(tail))
        if enhanced:
            if len(stamps) != 1:
                raise ValueError("Enhanced word LRC needs one parent line timestamp")
            if tail[:enhanced[0].start()].strip():
                raise ValueError("Enhanced word LRC requires a timestamp before each word")
            for j, tag in enumerate(enhanced):
                end = enhanced[j + 1].start() if j + 1 < len(enhanced) else len(tail)
                value = _word(tail[tag.end():end])
                # Enhanced time is authoritative; the parent line timestamp
                # is not an additional offset.
                result.append(WordCue(_time(tag) + offset, value, len(result)))
        else:
            value = _word(tail)
            for stamp in stamps:
                result.append(WordCue(stamp + offset, value, len(result)))
        if len(result) > MAX_WORDS:
            raise ValueError("Word timing LRC exceeds 100,000 timed words")
    if not result:
        raise ValueError("Word LRC contains no timed words")
    if any(event.time < 0 for event in result):
        raise ValueError("Word timestamps must be nonnegative")
    return tuple(sorted(result, key=lambda x: (x.time, x.order)))


def word_frame_ledger(events, *, start_sample, end_sample, sample_rate, fps, repeats, frame_count):
    """Map words into excerpt/repeat output frame positions, without carry-over."""
    start = Fraction(start_sample, sample_rate)
    span = Fraction(end_sample - start_sample, sample_rate)
    selected = [event for event in events if start <= event.time < start + span]
    if len(selected) * repeats > 1_000_000:
        raise ValueError("Word timing would create more than one million output events")
    result = []
    for cycle in range(repeats):
        # An empty event at the beginning of every repeat prevents the last word
        # in the previous cycle carrying into the next before its first word.
        begin = half_up(cycle * span * fps)
        if begin < frame_count:
            result.append(dict(frame=begin, text="", source_seconds=float(start),
                               source_order=-1, cycle=cycle, output_seconds=float(cycle * span)))
        for event in selected:
            relative = event.time - start + cycle * span
            frame = half_up(relative * fps)
            if frame >= frame_count:
                continue
            result.append(dict(frame=frame, text=event.text, source_seconds=float(event.time),
                               source_order=event.order, cycle=cycle, output_seconds=float(relative)))
    return sorted(result, key=lambda row: (row["frame"], row["cycle"], row["source_order"]))


def _normalised(text):
    text = unicodedata.normalize("NFKC", text.casefold()).replace("’", "'")
    return "".join(ch for ch in text if ch.isalnum())


def assign_words_to_lines(word_rows, line_rows):
    """Attach word index/line identity; reject words that cannot fit that line.

    Skipped words in a line remain visible but unhighlighted. This allows partial
    word-alignment exports without hallucinating absent word timing.
    """
    if not line_rows:
        return word_rows
    frames = [row["frame"] for row in line_rows]
    cursor = {}
    for word in word_rows:
        word["line_frame"] = None
        word["word_index"] = None
        if not word["text"]:
            continue
        index = bisect_right(frames, word["frame"]) - 1
        if index < 0:
            raise ValueError(f"Word {word['text']!r} predates the first line")
        line = line_rows[index]
        if word["cycle"] != line["cycle"] or not line["text"]:
            raise ValueError(f"Word {word['text']!r} is outside an active line")
        key = (line["cycle"], line["frame"], line["source_order"])
        tokens = line["text"].split()
        start = cursor.get(key, 0)
        wanted = _normalised(word["text"])
        matched = next((i for i in range(start, len(tokens)) if _normalised(tokens[i]) == wanted), None)
        if matched is None:
            raise ValueError(f"Timed word {word['text']!r} does not match its active lyric line: {line['text']!r}")
        cursor[key] = matched + 1
        word["word_index"] = matched
        word["line_frame"] = line["frame"]
        word["line_order"] = line["source_order"]
    return word_rows


class WordTrack:
    def __init__(self, events):
        self.events = events
        self.frames = [row["frame"] for row in events]

    def at(self, frame, line=None):
        index = bisect_right(self.frames, frame) - 1
        if index < 0:
            return None
        word = self.events[index]
        if line is not None and (word.get("line_frame") != line["frame"] or
                                 word.get("line_order") != line["source_order"] or
                                 word["cycle"] != line["cycle"]):
            return None
        return word if word["text"] else None

    def state(self, frame, line=None):
        current = self.at(frame, line)
        if current is None:
            return dict(text="", active=None, completed=())
        active = current.get("word_index")
        if line is None or active is None:
            return dict(text=current["text"], active=active, completed=())
        # Only source observations within the current line can be highlighted.
        start = bisect_right(self.frames, line["frame"] - 1)
        end = bisect_right(self.frames, frame)
        completed = tuple(sorted({row["word_index"] for row in self.events[start:end]
                                 if row.get("line_frame") == line["frame"] and
                                 row.get("line_order") == line["source_order"] and
                                 row["cycle"] == line["cycle"] and row.get("word_index") is not None and
                                 row["word_index"] < active}))
        return dict(text=current["text"], active=active, completed=completed)


def draw_highlighted_line(block, image, text, *, active, completed=(), colour="#ffe082",
                          completed_colour="#9ad9d5"):
    """Reuse original TextBlock layout and overlay timed words at matching glyph positions."""
    bounds = block.draw(image, text)
    if not text or active is None:
        return bounds
    from PIL import ImageColor, ImageDraw
    draw = ImageDraw.Draw(image)
    active_colour = ImageColor.getcolor(colour, "RGBA")
    past_colour = ImageColor.getcolor(completed_colour, "RGBA")
    font, rows, boxes, height = block.layout(text)
    left, top, right, bottom = block.box
    y = top + (bottom - top - height) / 2
    count = 0
    for line, bbox in zip(rows, boxes):
        x = left + (right - left - (bbox[2] - bbox[0])) / 2 - bbox[0]
        for match in re.finditer(r"\S+", line):
            ink = active_colour if count == active else past_colour if count in completed else None
            if ink:
                # Same baseline as TextBlock.draw(), preserving line wrapping.
                offset = draw.textlength(line[:match.start()], font=font)
                draw.text((x + offset, y - bbox[1]), match.group(), font=font, fill=ink,
                          stroke_width=block.stroke, stroke_fill="#12111c")
            count += 1
        y += bbox[3] - bbox[1] + block.spacing
    return bounds


def word_export_ledger(word_events, line_events=None):
    """Bound word SRT/ASS intervals to line changes and excerpt boundaries."""
    output = [dict(row) for row in word_events]
    if line_events:
        output.extend(dict(frame=row["frame"], text="", source_seconds=row.get("source_seconds", 0),
                           cycle=row["cycle"], source_order=-2) for row in line_events)
    return sorted(output, key=lambda row: (row["frame"], row["source_order"]))
