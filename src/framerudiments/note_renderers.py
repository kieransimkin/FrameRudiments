"""Deterministic piano-keyboard and perspective note-highway layer views."""

from copy import deepcopy
import math
import re

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .effects import alpha_scaled
from .io import fields, integer, number, rectangle
from .note_timeline import NoteTimeline
from .note_view_dispatch import SETTINGS, LABELLED, make_view

PALETTE = ("#52d6cf", "#f6ad54", "#ac98ed", "#ef829f", "#70b8f4", "#d4d866")
BLACK = frozenset((1, 3, 6, 8, 10))


def key_geometry(low, high):
    def before(key):
        return sum(p % 12 not in BLACK for p in range(key))

    rows = []
    for key in range(low, high + 1):
        base = before(key)
        black = key % 12 in BLACK
        rows.append((key, base - 0.31 if black else base, base + 0.31 if black else base + 1, black))
    left = min(r[1] for r in rows)
    span = max(r[2] for r in rows) - left
    return {key: ((a - left) / span, (b - left) / span, black) for key, a, b, black in rows}


class NoteLayer:
    def __init__(self, project, config, context_at=None):
        fields(
            config,
            {
                "id",
                "type",
                "file",
                "tracks",
                *set(SETTINGS.values()),
                "view",
                "bounds",
                "note_range",
                "channels",
                "lookahead_seconds",
                "history_seconds",
                "direction",
                "keyboard_fraction",
                "palette",
                "colour_by",
                "labels",
                "panel",
                "opacity",
                "lane_count",
                "note_map",
                "start_seconds",
                "end_seconds",
                "cue_labels",
            },
            "note layer",
        )
        if (
            config.get("type") != "notes"
            or not isinstance(config.get("id"), str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", config["id"])
        ):
            raise ValueError("Note layer needs type notes and a bounded unique id")
        self.project, self.cfg, self.id = project, deepcopy(config), config["id"]
        self.context_at = context_at
        self.motion = None  # Shared compositor manifest contract; note time is not rudiment motion.
        self.view = config.get("view", "piano-roll")
        if self.view not in ("piano-roll", "note-highway", *SETTINGS):
            raise ValueError("Unsupported native note view")
        for field in set(SETTINGS.values()):
            if field in config and field != SETTINGS.get(self.view):
                raise ValueError("Note settings belong to a different view")
        self.bounds = rectangle(config["bounds"], "Note viewport", project.width, project.height)
        if (
            any(int(x) != x for x in self.bounds)
            or min(self.bounds[2] - self.bounds[0], self.bounds[3] - self.bounds[1]) < 1
        ):
            raise ValueError("Note viewport needs integer pixels and at least one pixel per axis")
        self.bounds = tuple(int(x) for x in self.bounds)
        self.timeline = NoteTimeline.load(project, config["file"])
        self._indices = {id(n): i for i, n in enumerate(self.timeline.notes)}
        self.ahead = number(config.get("lookahead_seconds", 3), "Note lookahead", minimum=0.05, maximum=60)
        self.history = number(config.get("history_seconds", 3), "Note history", minimum=0.05, maximum=60)
        self.direction = config.get("direction", "falling")
        if (
            self.direction not in ("falling", "rising")
            or self.view != "piano-roll"
            and self.direction != "falling"
        ):
            raise ValueError("Only piano-roll accepts a rising direction")
        self.keyboard_fraction = number(
            config.get("keyboard_fraction", 0.22), "Keyboard fraction", minimum=0.1, maximum=0.45
        )
        self.opacity = number(config.get("opacity", 1), "Note opacity", minimum=0, maximum=1)
        self.colour_by = config.get("colour_by", "channel")
        if self.colour_by not in ("channel", "track"):
            raise ValueError("Note colours follow explicit channel or track identity")
        palette = config.get("palette", list(PALETTE))
        if not isinstance(palette, list) or not 1 <= len(palette) <= 16:
            raise ValueError("Note palette needs 1 to 16 CSS colours")
        self.palette = tuple(ImageColor.getcolor(c, "RGBA") for c in palette)
        self.panel = ImageColor.getcolor(
            config.get("panel", "#00000000" if self.view == "note-impacts" else "#0c1929"), "RGBA"
        )
        self.labels = config.get("labels", self.view in LABELLED)
        self.cfg["labels"] = self.labels
        if type(self.labels) is not bool or self.labels and not project.font_ref:
            raise ValueError("Note labels need a boolean and an explicit pinned project font")
        channels = config.get("channels", list(range(1, 17)))
        if (
            not isinstance(channels, list)
            or not 1 <= len(channels) <= 16
            or any(type(c) is not int or not 1 <= c <= 16 for c in channels)
            or len(set(channels)) != len(channels)
        ):
            raise ValueError("Note channels need unique integers 1..16")
        self.channels = frozenset(channels)
        from .note_view_support import ids

        self.tracks = ids(config.get("tracks", list(range(128))), "Note tracks")
        interval = config.get("note_range", [21, 108] if self.view == "piano-roll" else [0, 127])
        if not isinstance(interval, list) or len(interval) != 2:
            raise ValueError("Note range needs two MIDI keys")
        self.low = integer(interval[0], "Lowest note", maximum=127)
        self.high = integer(interval[1], "Highest note", minimum=self.low, maximum=127)
        self.geometry = key_geometry(self.low, self.high)
        self.lane_count = integer(config.get("lane_count", 5), "Highway lane count", minimum=2, maximum=8)
        self.note_map = {}
        if self.view == "note-highway":
            mapping = config.get("note_map")
            if not isinstance(mapping, dict) or not 1 <= len(mapping) <= 128:
                raise ValueError("Highway needs an explicit note_map; pitch is not a guitar fingering")
            for key, lane in mapping.items():
                if (
                    not isinstance(key, str)
                    or not key.isascii()
                    or not key.isdecimal()
                    or str(int(key)) != key
                ):
                    raise ValueError("Highway note_map keys must be canonical MIDI-key strings")
                pitch = integer(int(key), "Highway mapped key", maximum=127)
                self.note_map[pitch] = integer(lane, "Highway lane", maximum=self.lane_count - 1)
            if any(n.key not in self.note_map for n in self.timeline.notes if self._selected(n)):
                raise ValueError("A selected note has no explicit highway lane")
        elif "note_map" in config or "lane_count" in config:
            raise ValueError("Lane mapping applies only to note-highway")
        for name in ("start_seconds", "end_seconds"):
            if name in config:
                number(config[name], "Note visibility time", minimum=0)
        if config.get("end_seconds", math.inf) <= config.get("start_seconds", 0):
            raise ValueError("Note visibility interval must be increasing")
        if "cue_labels" in config and (
            not isinstance(config["cue_labels"], list)
            or not config["cue_labels"]
            or any(not isinstance(x, str) for x in config["cue_labels"])
        ):
            raise ValueError("Note cue labels need a nonempty string list")
        self.evidence = {
            **self.timeline.evidence,
            "view": self.view,
            "direction": self.direction,
            "note_range": [self.low, self.high],
            "selected_notes": sum(self._selected(n) for n in self.timeline.notes),
            "lane_mapping": "explicit authored MIDI-key map" if self.note_map else None,
        }
        self.extra = make_view(self)
        if self.extra is not None:
            self.evidence["layout"] = self.extra.evidence
        from .preview_stream_contract import canonical_digest
        from .compositor_layer_cache import _pins

        self.cache_config = deepcopy(self.cfg)
        for field in set(SETTINGS.values()):
            if field in self.cache_config:
                self.cache_config[field] = {
                    "validated_settings_sha256": canonical_digest(_pins(self.cache_config[field]))
                }
        self.frame_report = None

    def close(self):
        if self.extra is not None and hasattr(self.extra, "close"):
            self.extra.close()
        from .note_view_support import _font

        _font.cache_clear()

    def _selected(self, note):
        return (
            note.channel in self.channels and note.track in self.tracks and self.low <= note.key <= self.high
        )

    def _colour(self, note):
        index = note.channel - 1 if self.colour_by == "channel" else note.track
        colour = self.palette[index % len(self.palette)]
        return (*colour[:3], round(colour[3] * (0.4 + 0.6 * note.velocity / 127)))

    def draw(self, image, ctx):
        if not self.cfg.get("start_seconds", 0) <= ctx.source_seconds < self.cfg.get("end_seconds", math.inf):
            self.frame_report = None
            return None
        if "cue_labels" in self.cfg and (not ctx.cue or ctx.cue["text"] not in self.cfg["cue_labels"]):
            self.frame_report = None
            return None
        now = ctx.output_seconds
        low, high = (now - self.history, now) if self.direction == "rising" else (now, now + self.ahead)
        if self.extra is not None:
            low, high = self.extra.query_window(now)
            found = {id(n): n for n in (*self.timeline.window(low, high), *self.timeline.onsets(low, high))}
            notes = tuple(
                n for n in sorted(found.values(), key=lambda n: self._indices[id(n)]) if self._selected(n)
            )
        else:
            notes = tuple(n for n in self.timeline.window(low, high) if self._selected(n))
        active = tuple(n for n in notes if n.start_seconds <= now < n.release_seconds)
        sounding = tuple(n for n in notes if n.start_seconds <= now < n.end_seconds)
        left, top, right, bottom = self.bounds
        scale = self.project.output_quality.vector_scale
        size = ((right - left) * scale, (bottom - top) * scale)
        canvas = Image.new("RGBA", size, self.panel)
        if self.extra is not None:
            self.extra.draw(canvas, now, notes, active, sounding)
        elif self.view == "piano-roll":
            self._piano(canvas, now, notes, active)
        else:
            self._highway(canvas, now, notes, active)
        if scale > 1:
            canvas = canvas.resize((right - left, bottom - top), Image.Resampling.LANCZOS)
        image.alpha_composite(alpha_scaled(canvas, self.opacity), (left, top))
        self.frame_report = {
            "view": self.view,
            "output_seconds": now,
            "visible_notes": len(notes),
            "active_notes": len(active),
            "active_keys": sorted({n.key for n in active}),
            "sounding_keys": sorted({n.key for n in sounding}),
            "source_sha256": self.timeline.evidence["sha256"],
        }
        if self.extra is not None:
            self.frame_report.update(self.extra.frame_report)
        return self.bounds

    def _piano(self, canvas, now, notes, active):
        w, h = canvas.size
        draw = ImageDraw.Draw(canvas)
        pad = max(0, min(round(w * 0.012), (w - 1) // 2, (h - 1) // 2))
        strike = min(h - 1, round(h * (1 - self.keyboard_fraction)))
        area = max(0, strike - pad)

        def edges(key):
            a, b, black = self.geometry[key]
            return pad + a * (w - 2 * pad), pad + b * (w - 2 * pad), black

        for key in range(self.low, self.high + 1):
            x0, x1, black = edges(key)
            if not black:
                draw.line((x0, pad, x0, strike), fill="#23394d", width=max(1, round(w / 640)))
        for note in notes:
            x0, x1, _black = edges(note.key)
            if self.direction == "falling":
                y0 = strike - (note.end_seconds - now) / self.ahead * area
                y1 = strike - (note.start_seconds - now) / self.ahead * area
            else:
                y0 = strike - (now - note.start_seconds) / self.history * area
                y1 = strike - (now - min(now, note.end_seconds)) / self.history * area
            y0, y1 = max(pad, y0), min(strike, y1)
            if y1 > y0:
                inset = min((x1 - x0) * 0.12, max(1.0, w / 480))
                draw.rounded_rectangle(
                    (x0 + inset, y0, x1 - inset, y1),
                    radius=min((x1 - x0) / 5, (y1 - y0) / 3),
                    fill=self._colour(note),
                    outline="#d9ffff" if note in active else None,
                    width=max(1, round(w / 640)),
                )
        held = {n.key: n for n in sorted(active, key=lambda n: n.velocity)}
        for black in (False, True):
            for key, (a, b, is_black) in self.geometry.items():
                if is_black != black:
                    continue
                x0, x1, _ = edges(key)
                fill = "#101b2d" if black else "#ebeff3"
                if key in held:
                    fill = self._colour(held[key])
                end = strike + (h - strike) * (0.63 if black else 1)
                draw.rectangle(
                    (x0, strike, x1, end), fill=fill, outline="#344052", width=max(1, round(w / 640))
                )
        draw.line((pad, strike, w - pad, strike), fill="#c5ffeb", width=max(1, round(h / 140)))
        if self.labels:
            key_width = (w - 2 * pad) / max(1, sum(not v[2] for v in self.geometry.values()))
            font = ImageFont.truetype(
                self.project.font_ref["path"], max(5, round(min(key_width * 0.38, h * 0.032)))
            )
            for key, (a, b, black) in self.geometry.items():
                if not black and key % 12 == 0:
                    draw.text(
                        (pad + (a + b) / 2 * (w - 2 * pad), h - (h - strike) * 0.08),
                        f"C{key // 12 - 1}",
                        font=font,
                        anchor="mb",
                        fill="#142638",
                    )

    def _highway(self, canvas, now, notes, active):
        w, h = canvas.size
        draw = ImageDraw.Draw(canvas)
        horizon, strike = h * 0.06, h * 0.82

        def point(dt, lane):
            u = min(1.0, max(0.0, dt / self.ahead))
            depth = (1 - u) / (1 + 3 * u)
            width = w * (0.24 + 0.68 * depth)
            return (w - width) / 2 + lane / self.lane_count * width, horizon + depth * (strike - horizon)

        draw.polygon(
            [
                point(self.ahead, 0),
                point(self.ahead, self.lane_count),
                point(0, self.lane_count),
                point(0, 0),
            ],
            fill="#182c40",
        )
        for lane in range(self.lane_count + 1):
            draw.line(
                (*point(self.ahead, lane), *point(0, lane)), fill="#496077", width=max(1, round(w / 480))
            )
        for tick in range(math.ceil(now), math.floor(now + self.ahead) + 1):
            draw.line(
                (*point(tick - now, 0), *point(tick - now, self.lane_count)),
                fill="#304b61",
                width=max(1, round(w / 640)),
            )
        # Far notes first so nearer heads and sustained tails retain depth order.
        for note in sorted(notes, key=lambda n: n.start_seconds, reverse=True):
            lane = self.note_map[note.key]
            colour = self.palette[lane % len(self.palette)]
            colour = (*colour[:3], round(colour[3] * (0.4 + 0.6 * note.velocity / 127)))
            head = max(0.0, note.start_seconds - now)
            tail = max(0.0, note.end_seconds - now)
            draw.polygon(
                [
                    point(head, lane + 0.39),
                    point(head, lane + 0.61),
                    point(tail, lane + 0.61),
                    point(tail, lane + 0.39),
                ],
                fill=colour,
            )
            x0, y = point(head, lane + 0.16)
            x1, _ = point(head, lane + 0.84)
            radius = max(1.0, (x1 - x0) * 0.18)
            draw.rounded_rectangle(
                (x0, y - radius, x1, y + radius),
                radius=radius,
                fill=colour,
                outline="#f1ffff" if note in active else "#24364a",
                width=max(1, round(w / 480)),
            )
        draw.line((*point(0, 0), *point(0, self.lane_count)), fill="#c5ffeb", width=max(1, round(h / 100)))
        held_lanes = {self.note_map[n.key] for n in active}
        for lane in range(self.lane_count):
            x0, _ = point(0, lane + 0.2)
            x1, _ = point(0, lane + 0.8)
            y = h * 0.91
            colour = self.palette[lane % len(self.palette)]
            draw.ellipse(
                (x0, y - (x1 - x0) * 0.18, x1, y + (x1 - x0) * 0.18),
                fill=colour if lane in held_lanes else "#16283b",
                outline=colour,
                width=max(1, round(w / 240)),
            )
