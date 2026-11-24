"""Pitch-class geometry and retained voicing; no inferred chord or key labels."""

import math
from PIL import ImageDraw
from .io import fields, number
from .note_view_support import font, text

PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


class PitchWheelView:
    def __init__(self, layer, settings):
        fields(settings, {"pitch_names", "connections", "decay_seconds", "start_angle"}, "pitch wheel")
        self.layer = layer
        self.names = settings.get("pitch_names", list(PITCH_NAMES))
        if not isinstance(self.names, list) or len(self.names) != 12:
            raise ValueError("Pitch wheel needs twelve declared pitch-class names")
        self.names = tuple(text(n, "Pitch-class name", maximum=8) for n in self.names)
        self.connections = settings.get("connections", True)
        if type(self.connections) is not bool:
            raise ValueError("Wheel connections must be boolean")
        self.decay = number(
            settings.get("decay_seconds", 0.65), "Wheel attack decay", minimum=0.05, maximum=8
        )
        self.angle = number(settings.get("start_angle", -90), "Wheel start angle", minimum=-360, maximum=360)
        self.frame_report = {}
        self.evidence = dict(
            pitch_names=self.names,
            aggregation="pitch class; exact MIDI keys retained in voicing",
            harmonic_inference=False,
            attack_decay_seconds=self.decay,
        )

    def query_window(self, now):
        return now - self.decay, now

    def draw(self, canvas, now, notes, active, sounding):
        groups = {i: [] for i in range(12)}
        for note in sounding:
            groups[note.key % 12].append(note)
        self.frame_report = dict(
            pitch_classes=[
                dict(
                    pitch_class=i,
                    name=self.names[i],
                    keys=sorted({n.key for n in group}),
                    voices=len(group),
                    channels=sorted({n.channel for n in group}),
                    tracks=sorted({n.track for n in group}),
                )
                for i, group in groups.items()
                if group
            ],
            harmonic_inference=False,
        )
        if min(canvas.size) < 12:
            return
        w, h = canvas.size
        cx, cy = w * 0.5, h * 0.5
        r = min(w, h) * 0.32
        draw = ImageDraw.Draw(canvas)
        points = [
            (
                cx + math.cos(math.radians(self.angle + i * 30)) * r,
                cy + math.sin(math.radians(self.angle + i * 30)) * r,
            )
            for i in range(12)
        ]
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline="#314d62", width=max(1, round(w / 320)))
        occupied = [i for i, group in groups.items() if group]
        if self.connections and len(occupied) > 1:
            edges = list(zip(occupied, occupied[1:] + occupied[:1]))
            for a, b in edges:
                draw.line((*points[a], *points[b]), fill="#55768c", width=max(1, round(w / 240)))
        for index, (x, y) in enumerate(points):
            group = groups[index]
            tint = self.layer._colour(max(group, key=lambda n: n.velocity)) if group else "#213b50"
            radius = r * 0.105
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=tint,
                outline="#dceff7" if group else "#456176",
                width=max(1, round(w / 480)),
            )
            if self.layer.labels:
                angle = math.radians(self.angle + index * 30)
                face = font(self.layer, min(w, h) * 0.047)
                draw.text(
                    (cx + math.cos(angle) * r * 1.32, cy + math.sin(angle) * r * 1.32),
                    self.names[index],
                    font=face,
                    fill="#dceaf1",
                    anchor="mm",
                )
        latest = {}
        for note in notes:
            if now - self.decay < note.start_seconds <= now:
                previous = latest.get(note.key % 12)
                if previous is None or note.start_seconds >= previous.start_seconds:
                    latest[note.key % 12] = note
        for note in latest.values():
            age = now - note.start_seconds
            if not 0 <= age < self.decay:
                continue
            x, y = points[note.key % 12]
            phase = age / self.decay
            radius = r * (0.12 + 0.22 * phase)
            tint = (*self.layer._colour(note)[:3], round(220 * (1 - phase)))
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius), outline=tint, width=max(1, round(w / 240))
            )
