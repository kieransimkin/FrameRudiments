"""Authored string/fret positions shared by a fretboard and scrolling tablature."""

from PIL import ImageDraw
from .io import fields, integer, number
from .note_view_support import axis, font, label, text, validate_axis
from .note_pitch_wheel import PITCH_NAMES


class FingeringView:
    def __init__(self, layer, settings):
        fields(
            settings,
            {
                "notes_sha256",
                "tuning",
                "capo",
                "fret_count",
                "positions",
                "string_labels",
                "time_mode",
                "grid_seconds",
                "label_fraction",
                "decay_seconds",
            },
            "authored fingering",
        )
        self.layer, self.settings = layer, settings
        if settings.get("notes_sha256") != layer.timeline.evidence["sha256"]:
            raise ValueError("Fingering must name the exact admitted note-file SHA-256")
        tuning = settings.get("tuning")
        if not isinstance(tuning, list) or not 1 <= len(tuning) <= 12:
            raise ValueError("Fingering needs 1..12 declared string pitches, string1 first")
        self.tuning = tuple(integer(k, "Open string pitch", maximum=127) for k in tuning)
        self.capo = integer(settings.get("capo", 0), "Authored capo", maximum=24)
        self.frets = integer(settings.get("fret_count", 24), "Visible fret count", minimum=1, maximum=36)
        self.names = settings.get(
            "string_labels", [f"{PITCH_NAMES[k % 12]}{k // 12 - 1}" for k in self.tuning]
        )
        if not isinstance(self.names, list) or len(self.names) != len(tuning):
            raise ValueError("String labels must match the tuning")
        self.names = tuple(text(v, "String label", maximum=16) for v in self.names)
        rows = settings.get("positions")
        if not isinstance(rows, list) or len(rows) > len(layer.timeline.notes):
            raise ValueError("Fingering needs a bounded explicit note-position list")
        self.positions = {}
        for row in rows:
            fields(row, {"note_index", "string", "fret", "finger", "technique"}, "authored note fingering")
            index = integer(
                row.get("note_index"), "Fingering note index", maximum=len(layer.timeline.notes) - 1
            )
            if index in self.positions:
                raise ValueError("A note occurrence has duplicate fingerings")
            string = integer(row.get("string"), "String number", minimum=1, maximum=len(tuning))
            fret = integer(row.get("fret"), "Authored fret", maximum=self.frets)
            if self.tuning[string - 1] + self.capo + fret != layer.timeline.notes[index].key:
                raise ValueError("String, capo and fret do not match the declared MIDI pitch")
            finger = (
                integer(row["finger"], "Authored finger", minimum=1, maximum=5) if "finger" in row else None
            )
            technique = row.get("technique", "none")
            if technique not in {"none", "slide", "bend", "hammer-on", "pull-off", "harmonic", "tap"}:
                raise ValueError("Unsupported authored technique label")
            self.positions[index] = dict(string=string, fret=fret, finger=finger, technique=technique)
        selected = [i for i, n in enumerate(layer.timeline.notes) if layer._selected(n)]
        if any(i not in self.positions for i in selected):
            raise ValueError("Every selected guitar note needs an authored fingering")
        self.lookup = {id(n): i for i, n in enumerate(layer.timeline.notes)}
        self.mode = settings.get("time_mode", "scroll")
        if self.mode not in {"scroll", "overview"}:
            raise ValueError("Tab time_mode needs scroll or overview")
        validate_axis(settings)
        self.decay = number(settings.get("decay_seconds", 0.65), "Fret attack decay", minimum=0.05, maximum=8)
        self.frame_report = {}
        self.evidence = dict(
            notes_sha256=settings["notes_sha256"],
            tuning=self.tuning,
            capo=self.capo,
            fret_count=self.frets,
            mapped_notes=len(self.positions),
            mapping="authored occurrence/string/fret; no fingering inference",
        )

    def query_window(self, now):
        if self.layer.view == "tablature":
            return (
                (0, float(self.layer.project.duration))
                if self.mode == "overview"
                else (now - self.layer.history, now + self.layer.ahead)
            )
        return now - self.decay, now

    def draw(self, canvas, now, notes, active, sounding):
        playing = [(self.lookup[id(n)], n) for n in sounding]
        unique = sorted({(self.positions[i]["string"], self.positions[i]["fret"]) for i, _n in playing})
        self.frame_report = dict(
            sounding_positions=[dict(string=s, fret=f) for s, f in unique],
            authored_note_occurrences=[i for i, _ in playing][:256],
            sounding_occurrence_count=len(playing),
            fingering_inferred=False,
        )
        if min(canvas.size) < 12:
            return
        if self.layer.view == "tablature":
            self._tab(canvas, now, notes, sounding)
            return
        w, h = canvas.size
        draw = ImageDraw.Draw(canvas)
        left, right = w * 0.1, w * 0.96
        top, bottom = h * 0.15, h * 0.88

        def y(string):
            return top + (string - 0.5) / len(self.tuning) * (bottom - top)

        denom = 1 - 2 ** (-self.frets / 12)

        def x(fret):
            return left + (right - left) * (1 - 2 ** (-fret / 12)) / denom

        def center(fret):
            return left - w * 0.025 if fret == 0 else (x(fret - 1) + x(fret)) / 2

        for fret in range(self.frets + 1):
            px = x(fret)
            draw.line((px, top, px, bottom), fill="#738b9b", width=max(1, round(w / (240 if fret else 120))))
            if fret in (1, 3, 5, 7, 9, 12, 15, 17, 19, 21, 24, 27, 29, 31, 33, 36):
                label(draw, (center(fret) - w * 0.01, bottom + h * 0.025), str(fret), self.layer, w * 0.024)
        for index, name in enumerate(self.names):
            py = y(index + 1)
            draw.line(
                (left, py, right, py), fill="#b5c2cc", width=max(1, round(h * (0.002 + index * 0.0003)))
            )
            label(draw, (w * 0.012, py - h * 0.02), name, self.layer, w * 0.028, max_width=w * 0.075)
        for index, note in playing:
            pos = self.positions[index]
            cx, cy = center(pos["fret"]), y(pos["string"])
            radius = min(h / len(self.tuning) * 0.27, w * 0.018)
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=self.layer._colour(note),
                outline="#f1ffff",
                width=max(1, round(w / 480)),
            )
            if self.layer.labels and pos["finger"] is not None:
                draw.text(
                    (cx, cy),
                    str(pos["finger"]),
                    font=font(self.layer, radius * 1.3),
                    fill="#0b1928",
                    anchor="mm",
                )
        # Attack rings use source note time; fret locations remain authored.
        for note in notes:
            age = now - note.start_seconds
            if not 0 <= age < self.decay:
                continue
            pos = self.positions[self.lookup[id(note)]]
            cx, cy = center(pos["fret"]), y(pos["string"])
            phase = age / self.decay
            radius = min(h / len(self.tuning) * 0.27, w * 0.018) + w * 0.028 * phase
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                outline=(*self.layer._colour(note)[:3], round(220 * (1 - phase))),
                width=max(1, round(w / 320)),
            )

    def _tab(self, canvas, now, notes, sounding):
        w, h = canvas.size
        draw = ImageDraw.Draw(canvas)
        left, top, right, bottom, x = axis(draw, self.layer, now, canvas.size, self.settings, mode=self.mode)
        height = (bottom - top) / len(self.tuning)
        for string, name in enumerate(self.names, 1):
            cy = top + (string - 0.5) * height
            draw.line((left, cy, right, cy), fill="#6d8295", width=max(1, round(w / 960)))
            label(
                draw,
                (w * 0.01, cy - height * 0.15),
                f"{string}: {name}",
                self.layer,
                min(height * 0.3, w * 0.038),
                max_width=left - w * 0.02,
            )
        for note in notes:
            pos = self.positions[self.lookup[id(note)]]
            px = x(note.start_seconds)
            end = min(right, x(note.end_seconds))
            if end < left or px > right:
                continue
            cy = top + (pos["string"] - 0.5) * height
            tint = self.layer._colour(note)
            draw.line(
                (max(left, px), cy + height * 0.22, max(left, end), cy + height * 0.22),
                fill=tint,
                width=max(1, round(h / 180)),
            )
            if px < left:
                continue
            if self.layer.labels:
                value = str(pos["fret"])
                draw.text(
                    (px, cy),
                    value,
                    font=font(self.layer, min(height * 0.5, w * 0.036)),
                    fill=tint,
                    stroke_width=max(1, round(w / 480)),
                    stroke_fill=self.layer.panel,
                    anchor="mm",
                )
            else:
                draw.ellipse((px - 2, cy - 2, px + 2, cy + 2), fill=tint)
        draw.line((x(now), top, x(now), bottom), fill="#f1ffff", width=max(1, round(w / 480)))
