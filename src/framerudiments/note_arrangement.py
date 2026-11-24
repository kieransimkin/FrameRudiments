"""Horizontal source-bound multi-part arrangement with scroll and overview views."""

from dataclasses import dataclass
import re
from PIL import Image, ImageDraw
from .io import fields, integer
from .note_view_support import axis, colour, ids, label, text, validate_axis


@dataclass(frozen=True)
class Part:
    id: str
    label: str
    tracks: frozenset
    channels: frozenset
    keys: frozenset
    low: int
    high: int
    colour: tuple | None


class ArrangementView:
    def __init__(self, layer, settings):
        fields(settings, {"parts", "group_by", "time_mode", "grid_seconds", "label_fraction"}, "arrangement")
        validate_axis(settings)
        self.layer, self.settings = layer, settings
        self.mode = settings.get("time_mode", "scroll")
        if self.mode not in {"scroll", "overview"}:
            raise ValueError("Arrangement time_mode needs scroll or overview")
        grouping = settings.get("group_by", "track")
        if grouping not in {"track", "channel"}:
            raise ValueError("Arrangement group_by needs track or channel")
        self.indexed = tuple((i, n) for i, n in enumerate(layer.timeline.notes) if layer._selected(n))
        selected = tuple(n for _, n in self.indexed)
        rows = settings.get("parts")
        if rows is None:
            groups = sorted({getattr(n, grouping) for n in selected})
            names = layer.timeline.data["provenance"].get("track_names", {})
            if not isinstance(names, dict):
                raise ValueError("Track names provenance must be a mapping")
            rows = [
                {
                    "id": f"part-{i + 1}",
                    "label": names.get(str(value), f"Track {value + 1}")
                    if grouping == "track"
                    else f"Channel {value}",
                    grouping + "s": [value],
                }
                for i, value in enumerate(groups)
            ]
            if not rows:
                rows = [{"id": "part-1", "label": "No notes"}]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
            raise ValueError("Arrangement needs 1..32 parts; select tracks/channels for a dense take")
        self.parts, self.assignment = [], {}
        seen = set()
        for index, row in enumerate(rows):
            fields(
                row, {"id", "label", "tracks", "channels", "keys", "note_range", "colour"}, "arrangement part"
            )
            identity = row.get("id", f"part-{index + 1}")
            if (
                not isinstance(identity, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", identity)
                or identity in seen
            ):
                raise ValueError("Arrangement part ids must be unique bounded identifiers")
            seen.add(identity)
            tracks = ids(row.get("tracks", list(range(128))), "Part tracks")
            channels = ids(row.get("channels", list(range(1, 17))), "Part channels", low=1, high=16)
            keys = ids(row.get("keys", list(range(128))), "Part keys")
            matching = [n for n in selected if n.track in tracks and n.channel in channels and n.key in keys]
            default = [
                max(0, min((n.key for n in matching), default=60) - 2),
                min(127, max((n.key for n in matching), default=72) + 2),
            ]
            pitch = row.get("note_range", default)
            if not isinstance(pitch, list) or len(pitch) != 2:
                raise ValueError("Part note_range needs two MIDI keys")
            low = integer(pitch[0], "Part lowest pitch", maximum=127)
            high = integer(pitch[1], "Part highest pitch", minimum=low, maximum=127)
            if any(not low <= n.key <= high for n in matching):
                raise ValueError("Part pitch range excludes a selected note")
            self.parts.append(
                Part(
                    identity,
                    text(row.get("label", identity), "Part label"),
                    tracks,
                    channels,
                    keys,
                    low,
                    high,
                    colour(row["colour"], "Part colour") if "colour" in row else None,
                )
            )
        for index, note in self.indexed:
            owners = [
                i
                for i, p in enumerate(self.parts)
                if note.track in p.tracks and note.channel in p.channels and note.key in p.keys
            ]
            if len(owners) != 1:
                raise ValueError("Every selected note must belong to exactly one arrangement part")
            self.assignment[index] = owners[0]
        self.lookup = {id(n): i for i, n in self.indexed}
        self._overview = None
        self.frame_report = {}
        self.evidence = dict(
            time_mode=self.mode,
            group_by=grouping,
            parts=[
                dict(
                    id=p.id,
                    label=p.label,
                    note_range=[p.low, p.high],
                    notes=sum(self.assignment[i] == j for i, _ in self.indexed),
                )
                for j, p in enumerate(self.parts)
            ],
            time_axis="continuous output seconds; existing source clock",
        )

    def close(self):
        if self._overview is not None:
            self._overview.close()
            self._overview = None

    def query_window(self, now):
        return (now, now) if self.mode == "overview" else (now - self.layer.history, now + self.layer.ahead)

    def _layout(self, canvas, now):
        w, h = canvas.size
        draw = ImageDraw.Draw(canvas)
        layout = axis(draw, self.layer, now, canvas.size, self.settings, mode=self.mode)
        left, top, right, bottom, x = layout
        height = (bottom - top) / len(self.parts)
        for index, part in enumerate(self.parts):
            y0, y1 = top + index * height, top + (index + 1) * height
            draw.rectangle((0, y0, left - 1, y1), fill="#122436" if index % 2 == 0 else "#0e1e2f")
            draw.line((0, y1, right, y1), fill="#496076", width=max(1, round(w / 960)))
            label(
                draw,
                (w * 0.012, y0 + height * 0.22),
                part.label,
                self.layer,
                min(height * 0.28, w * 0.048),
                max_width=max(1, left - w * 0.025),
            )
        return draw, layout, height

    def _bars(self, canvas, notes, layout, height, *, outline=False):
        draw = ImageDraw.Draw(canvas)
        left, top, right, _bottom, x = layout
        for note in notes:
            index = self.lookup[id(note)]
            part_index = self.assignment[index]
            part = self.parts[part_index]
            y0 = top + part_index * height + (part.high - note.key) / (part.high - part.low + 1) * height
            y1 = min(
                top + (part_index + 1) * height - 1, y0 + max(1, height / (part.high - part.low + 1) * 0.86)
            )
            x0, x1 = (
                max(left, x(note.start_seconds)),
                min(right, max(x(note.end_seconds), x(note.start_seconds) + 1)),
            )
            if x1 <= x0 or y1 < y0:
                continue
            tint = part.colour or self.layer._colour(note)
            draw.rounded_rectangle(
                (x0, y0, x1, y1),
                radius=min((y1 - y0) / 3, 3),
                fill=None if outline else tint,
                outline="#f0ffff" if outline else None,
                width=max(1, round(canvas.width / 640)),
            )

    def draw(self, canvas, now, notes, active, sounding):
        self.frame_report = dict(time_mode=self.mode, playhead_seconds=now)
        if min(canvas.size) < 12:
            return
        if self.mode == "overview":
            if self._overview is not None and self._overview.size != canvas.size:
                self.close()
            retain = canvas.width * canvas.height * 4 <= 32 * 1024 * 1024
            if self._overview is None:
                base = Image.new("RGBA", canvas.size, self.layer.panel)
                _draw, layout, height = self._layout(base, 0)
                self._bars(base, (n for _, n in self.indexed), layout, height)
                if retain:
                    self._overview = base
                    self._overview_geometry = (layout, height)
                else:
                    canvas.alpha_composite(base)
                    base.close()
            if retain:
                canvas.alpha_composite(self._overview)
                layout, height = self._overview_geometry
        else:
            _draw, layout, height = self._layout(canvas, now)
            self._bars(canvas, notes, layout, height)
        self._bars(canvas, sounding, layout, height, outline=True)
        draw = ImageDraw.Draw(canvas)
        left, top, right, bottom, x = layout
        playhead = x(now)
        if left <= playhead <= right:
            draw.line(
                (playhead, top, playhead, bottom), fill="#eefff9", width=max(1, round(canvas.width / 320))
            )
        self.frame_report = dict(
            time_mode=self.mode,
            playhead_seconds=now,
            window_seconds=[0, float(self.layer.project.duration)]
            if self.mode == "overview"
            else [now - self.layer.history, now + self.layer.ahead],
            parts=[
                dict(id=p.id, sounding=sum(self.assignment[self.lookup[id(n)]] == i for n in sounding))
                for i, p in enumerate(self.parts)
            ],
            visible_notes=len(self.indexed) if self.mode == "overview" else len(notes),
        )
