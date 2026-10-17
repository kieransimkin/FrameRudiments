"""Export the same frame ledger used by the raster captions."""

from html import escape
from pathlib import Path
import re
from .timeline import half_up
from fractions import Fraction


def intervals(events, frame_count):
    # Stable last event wins when more than one event maps to one frame.
    unique = {e["frame"]: e for e in events if e["frame"] < frame_count}
    ordered = sorted(unique.items())
    for index, (frame, event) in enumerate(ordered):
        end = ordered[index + 1][0] if index + 1 < len(ordered) else frame_count
        if event["text"] and end > frame:
            yield frame, end, event["text"]


def stamp(frame, fps, *, ass=False):
    units = 100 if ass else 1000
    value = half_up(Fraction(frame * units, fps))
    hours, value = divmod(value, 3600 * units)
    minutes, value = divmod(value, 60 * units)
    seconds, fraction = divmod(value, units)
    return (
        f"{hours}:{minutes:02}:{seconds:02}.{fraction:02}"
        if ass
        else f"{hours:02}:{minutes:02}:{seconds:02},{fraction:03}"
    )


def export_subtitles(events, fps, frame_count, directory, width, height, *, basename="captions"):
    if not isinstance(basename, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,60}", basename):
        raise ValueError("Invalid subtitle export name")
    directory = Path(directory)
    rows = list(intervals(events, frame_count))
    srt = "\n\n".join(
        f"{i}\n{stamp(a, fps)} --> {stamp(b, fps)}\n{escape(text)}" for i, (a, b, text) in enumerate(rows, 1)
    )
    (directory / f"{basename}.srt").write_text(srt + "\n", encoding="utf-8")
    header = (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n"
        "\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
        "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,48,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,2,0,5,20,20,20,1\n"
        "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for a, b, text in rows:
        # Strip ASS override delimiters; text is never interpreted as executable styling.
        inert = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{stamp(a, fps, ass=True)},{stamp(b, fps, ass=True)},Default,,0,0,0,,{inert}"
        )
    (directory / f"{basename}.ass").write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
