"""Explicit percussion lanes and pad matrices, driven by recorded note-on instants."""

from dataclasses import dataclass
import math
import re
from PIL import ImageDraw
from .io import fields, integer, number
from .note_view_support import axis, colour, ids, label, text, validate_axis


@dataclass(frozen=True)
class Pad:
    id: str
    label: str
    keys: frozenset
    colour: tuple
    shape: str


class PercussionView:
    def __init__(self, layer, settings):
        fields(
            settings,
            {"pads", "columns", "decay_seconds", "grid_seconds", "label_fraction"},
            "percussion view",
        )
        validate_axis(settings)
        self.layer, self.settings = layer, settings
        rows = settings.get("pads")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
            raise ValueError("Drum/pad views need 1..32 explicitly named pads")
        self.pads, self.mapping, seen = [], {}, set()
        for index, row in enumerate(rows):
            fields(row, {"id", "label", "keys", "colour", "shape"}, "percussion pad")
            identity = row.get("id", f"pad-{index + 1}")
            if (
                not isinstance(identity, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", identity)
                or identity in seen
            ):
                raise ValueError("Pad ids must be unique bounded identifiers")
            seen.add(identity)
            keys = ids(row.get("keys"), "Pad MIDI keys")
            if self.mapping.keys() & keys:
                raise ValueError("Every percussion key must map to only one pad")
            shape = row.get("shape", "circle")
            if shape not in {"circle", "cross", "diamond", "square", "bar"}:
                raise ValueError("Pad shape must be circle, cross, diamond, square or bar")
            tint = (
                colour(row["colour"], "Pad colour")
                if "colour" in row
                else layer.palette[index % len(layer.palette)]
            )
            self.pads.append(Pad(identity, text(row.get("label"), "Pad label"), keys, tint, shape))
            self.mapping.update({key: index for key in keys})
        if any(n.key not in self.mapping for n in layer.timeline.notes if layer._selected(n)):
            raise ValueError("Every selected percussion note needs an explicit pad map")
        self.columns = integer(
            settings.get("columns", min(8, math.ceil(math.sqrt(len(rows))))),
            "Pad columns",
            minimum=1,
            maximum=8,
        )
        self.decay = number(settings.get("decay_seconds", 0.45), "Pad decay", minimum=0.05, maximum=8)
        self.frame_report = {}
        self.evidence = dict(
            pads=[dict(id=p.id, label=p.label, keys=sorted(p.keys), shape=p.shape) for p in self.pads],
            mapping="explicit authored pad map; no inferred GM kit",
            decay_seconds=self.decay,
        )

    def query_window(self, now):
        return (
            (now - self.decay, now)
            if self.layer.view == "pad-grid"
            else (now - self.layer.history, now + self.layer.ahead)
        )

    @staticmethod
    def glyph(draw, x, y, r, shape, tint, stroke=1):
        if shape == "cross":
            draw.line((x - r, y - r, x + r, y + r), fill=tint, width=max(1, round(r * 0.5)))
            draw.line((x - r, y + r, x + r, y - r), fill=tint, width=max(1, round(r * 0.5)))
        elif shape == "diamond":
            draw.polygon(((x, y - r), (x + r, y), (x, y + r), (x - r, y)), fill=tint)
        elif shape == "square":
            draw.rectangle((x - r, y - r, x + r, y + r), fill=tint)
        elif shape == "bar":
            draw.rectangle((x - r * 1.2, y - r * 0.35, x + r * 1.2, y + r * 0.35), fill=tint)
        else:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=tint)

    def draw(self, canvas, now, notes, active, sounding):
        recent = [n for n in notes if 0 <= now - n.start_seconds < self.decay]
        hits = [[n for n in recent if self.mapping[n.key] == i] for i in range(len(self.pads))]
        self.frame_report = dict(
            pads=[
                dict(id=p.id, recent_hits=len(h), velocity=max((n.velocity for n in h), default=0))
                for p, h in zip(self.pads, hits)
            ],
            trigger="note-on, including zero-duration hits",
        )
        if min(canvas.size) < 12:
            return
        w, h = canvas.size
        draw = ImageDraw.Draw(canvas)
        if self.layer.view == "pad-grid":
            rows = math.ceil(len(self.pads) / self.columns)
            cw = w / self.columns
            ch = h / rows
            for index, (pad, events) in enumerate(zip(self.pads, hits)):
                col, row = index % self.columns, index // self.columns
                x0, y0 = col * cw + cw * 0.035, row * ch + ch * 0.035
                x1, y1 = (col + 1) * cw - cw * 0.035, (row + 1) * ch - ch * 0.035
                energy = max(
                    (n.velocity / 127 * (1 - (now - n.start_seconds) / self.decay) for n in events), default=0
                )
                tint = tuple(round(c * (0.2 + 0.8 * energy)) for c in pad.colour[:3]) + (255,)
                draw.rounded_rectangle(
                    (x0, y0, x1, y1),
                    radius=min(cw, ch) * 0.06,
                    fill="#12283b",
                    outline=tint,
                    width=max(1, round(w / 160)),
                )
                radius = min(cw, ch) * (0.12 + 0.12 * energy)
                self.glyph(draw, (x0 + x1) / 2, y0 + (y1 - y0) * 0.39, radius, pad.shape, tint)
                label(
                    draw,
                    (x0 + cw * 0.07, y0 + (y1 - y0) * 0.7),
                    pad.label,
                    self.layer,
                    min(ch * 0.14, cw * 0.12),
                    max_width=x1 - x0 - cw * 0.12,
                )
        else:
            left, top, right, bottom, x = axis(draw, self.layer, now, canvas.size, self.settings)
            height = (bottom - top) / len(self.pads)
            for index, pad in enumerate(self.pads):
                cy = top + (index + 0.5) * height
                draw.line((left, cy, right, cy), fill="#3c566c", width=max(1, round(w / 960)))
                label(
                    draw,
                    (w * 0.012, cy - height * 0.17),
                    pad.label,
                    self.layer,
                    min(height * 0.3, w * 0.042),
                    max_width=left - w * 0.02,
                )
            for note in notes:
                px = x(note.start_seconds)
                if not left <= px <= right:
                    continue
                index = self.mapping[note.key]
                pad = self.pads[index]
                cy = top + (index + 0.5) * height
                tint = (*pad.colour[:3], round(pad.colour[3] * (0.4 + 0.6 * note.velocity / 127)))
                self.glyph(draw, px, cy, max(1, min(height * 0.2, w * 0.012)), pad.shape, tint)
            draw.line((x(now), top, x(now), bottom), fill="#f0ffff", width=max(1, round(w / 320)))
