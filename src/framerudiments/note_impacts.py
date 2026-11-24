"""Note attacks compose the existing native ParticleEmitter; no parallel simulation."""

from .io import fields, integer, number
from .particle_engine import ParticleEmitter, MAX_ACTIVE, KINDS


class NoteImpactsView:
    def __init__(self, layer, settings):
        fields(
            settings,
            {"kind", "style", "particles_per_note", "max_events", "max_particles", "origin_y"},
            "note impacts",
        )
        self.layer = layer
        self.count = integer(
            settings.get("particles_per_note", 12), "Note particle count", minimum=1, maximum=128
        )
        self.max_events = integer(
            settings.get("max_events", 128), "Note event budget", minimum=1, maximum=256
        )
        self.max_particles = integer(
            settings.get("max_particles", MAX_ACTIVE), "Note particle budget", minimum=1, maximum=MAX_ACTIVE
        )
        self.origin = number(
            settings.get("origin_y", 1 - layer.keyboard_fraction), "Note effect origin", minimum=0, maximum=1
        )
        style = settings.get("style", {})
        if not isinstance(style, dict):
            raise ValueError("Note impact style must be an object")
        reserved = {"rate", "burst_count", "emitter_x", "emitter_y", "emitter_width", "emitter_height"}
        if reserved.intersection(style):
            raise ValueError("Note attacks own emission rate/count and their key-aligned origin")
        kind = settings.get("kind", "sparks")
        if kind not in KINDS:
            raise ValueError("Unknown native note particle kind")
        self.emitter = ParticleEmitter(
            layer.project,
            dict(
                id=layer.id + "-native-burst",
                type="emitter",
                kind=kind,
                bounds=list(layer.bounds),
                style={**style, "rate": 0, "emitter_width": 0, "emitter_height": 0},
                emission={"mode": "continuous"},
            ),
            layer.context_at or (lambda _frame: None),
        )
        self.horizon = self.emitter.base["lifetime"] * 1.15
        self.lookup = {id(n): i for i, n in enumerate(layer.timeline.notes)}
        self.frame_report = {}
        self.evidence = dict(
            shared_engine="framerudiments.particle_engine.ParticleEmitter",
            trigger="one bounded burst per source note-on; no note-off suppression of zero-duration hits",
            particles_per_note=self.count,
            max_events=self.max_events,
            max_particles=self.max_particles,
            time="continuous note seconds; identity seeds independent of video FPS",
        )

    def query_window(self, now):
        return now - self.horizon, now

    def close(self):
        self.emitter.close()

    def draw(self, canvas, now, notes, active, sounding):
        events = [n for n in notes if 0 <= now - n.start_seconds < self.horizon]
        events.sort(key=lambda n: (n.start_seconds, self.lookup[id(n)]), reverse=True)
        chosen = events[: self.max_events]
        total = 0
        if min(canvas.size) >= 12:
            w, h = canvas.size
            pad = max(0, min(round(w * 0.012), (w - 1) // 2, (h - 1) // 2))
            for note in chosen:
                a, b, _black = self.layer.geometry[note.key]
                x = (pad + (a + b) / 2 * (w - 2 * pad)) / max(1, w - 1)
                count = max(1, round(self.count * note.velocity / 127))
                result = self.emitter.draw_event_burst(
                    canvas,
                    seconds=note.start_seconds,
                    event_id=self.lookup[id(note)],
                    now=now,
                    count=count,
                    point=(x, self.origin),
                    colour=self.layer._colour(note),
                    max_active=self.max_particles - total,
                    raster_scale=w / 480,
                )
                total += result["active"]
                if total >= self.max_particles:
                    break
        self.frame_report = dict(
            shared_particle_engine=True,
            active_particles=total,
            recent_note_events=len(events),
            events_considered=len(chosen),
            event_budget_dropped=max(0, len(events) - len(chosen)),
            particle_budget=self.max_particles,
            trigger="note-on",
        )
