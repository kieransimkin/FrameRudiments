"""Validated portable render projects; private media remains outside the package."""
from fractions import Fraction
import math
from pathlib import Path

from .io import read_json, file_ref, fields, integer, number, sha256
from .timeline import BeatClock, parse_lrc, parse_cue, frame_ledger
from .stemlab import beat_grid, selected_loop
from .beat_alignment import beat_alignment


class Project:
    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        return cls(read_json(path), path.parent, project_path=path)

    def __init__(self, data, base, *, project_path=None):
        self.base, self.path, self.data = Path(base), project_path, data
        fields(data, {"schema", "title", "artist", "purpose", "width", "height", "fps", "fps_reason",
                      "audio", "clock", "font", "lyrics", "cues", "background", "layers", "captions",
                      "identity", "effects", "post_effects", "stemlab_loop", "notes", "lyric_art", "canonical_lyrics"}, "project")
        if data.get("schema") != "framerudiments.project.v1":
            raise ValueError("Expected framerudiments.project.v1")
        self.width = integer(data.get("width", 1080), "width", minimum=16, maximum=7680)
        self.height = integer(data.get("height", 1920), "height", minimum=16, maximum=7680)
        if self.width % 2 or self.height % 2:
            raise ValueError("H.264 yuv420p needs even dimensions")
        self.fps = integer(data.get("fps", 60), "fps", minimum=1, maximum=120)
        if self.fps != 60 and not data.get("fps_reason"):
            raise ValueError("Non-60-fps output needs a documented reason")
        self.purpose = data.get("purpose", "short")
        if self.purpose not in {"short", "visualiser", "canvas"}:
            raise ValueError("purpose must be short, visualiser or canvas")
        self.inputs = []
        self.audio = dict(data["audio"])
        fields(self.audio, {"source", "sample_rate", "start_sample", "end_sample", "repeats", "fade_seconds"}, "audio")
        self.audio_ref = self.ref(self.audio["source"])
        self.sample_rate = integer(self.audio["sample_rate"], "sample rate", minimum=1, maximum=768000)
        self.loop = None
        if "stemlab_loop" in data:
            cfg = data["stemlab_loop"]
            fields(cfg, {"file", "id"}, "StemLab loop")
            ref = self.ref(cfg["file"])
            self.loop = selected_loop(ref["path"], cfg["id"], self.audio_ref["sha256"])
            for key in ("start_sample", "end_sample", "sample_rate"):
                if key in self.audio and self.audio[key] != self.loop[key]:
                    raise ValueError(f"Explicit {key} conflicts with selected StemLab loop")
                self.audio[key] = self.loop[key]
        self.start_sample = integer(self.audio.get("start_sample", 0), "start sample")
        self.end_sample = integer(self.audio["end_sample"], "end sample", minimum=self.start_sample + 1)
        self.sample_rate = self.audio["sample_rate"]
        self.repeats = integer(self.audio.get("repeats", 1), "repeats", minimum=1, maximum=1000)
        self.slice_duration = Fraction(self.end_sample - self.start_sample, self.sample_rate)
        self.duration = self.slice_duration * self.repeats
        self.frame_count = math.ceil(self.duration * self.fps)
        if self.frame_count > 10_000_000:
            raise ValueError("Render exceeds ten million frames")
        self.fade = number(self.audio.get("fade_seconds", .12 if self.purpose == "short" else 0),
                           "fade seconds", minimum=0, maximum=float(self.duration) / 2)
        cfg = data["clock"]
        fields(cfg, {"bpm", "origin", "authority", "status", "phase_status", "beat_grid", "beat_alignment"}, "clock")
        if not cfg.get("authority") or cfg.get("status") not in {"confirmed", "estimated", "synthetic"}:
            raise ValueError("Clock needs authority and confirmed/estimated/synthetic status")
        self.alignment = None
        if "beat_alignment" in cfg:
            if "beat_grid" in cfg or "origin" in cfg:
                raise ValueError('beat_alignment cannot be combined with a separate beat grid or origin')
            ref = self.ref(cfg['beat_alignment'])
            self.alignment = beat_alignment(ref['path'], self.audio_ref['sha256'], self.sample_rate, self.end_sample)
            if 'bpm' in cfg and abs(number(cfg['bpm'], 'BPM')-self.alignment['bpm']) > 1e-8:
                raise ValueError('Explicit BPM conflicts with the beat alignment reading')
            if self.alignment['bpm_status'] == 'estimated' and cfg['status'] != 'estimated':
                raise ValueError('An estimated alignment BPM cannot be promoted to confirmed or synthetic')
            if 'phase_status' in cfg and cfg['phase_status'] != self.alignment['phase_status']:
                raise ValueError('Project phase authority conflicts with the saved reading')
            self.clock = BeatClock(bpm=self.alignment['bpm'], origin=self.alignment['first_beat_seconds'])
        else:
            grid = beat_grid(self.ref(cfg["beat_grid"])["path"]) if "beat_grid" in cfg else None
            self.clock = BeatClock(bpm=cfg.get("bpm"), origin=cfg.get("origin", 0), beats=grid)
        self.font_ref = self.ref(data["font"]) if "font" in data else None
        if (data.get("lyrics") or data.get("identity")) and not self.font_ref:
            raise ValueError("Pin an explicit font for all authored text")
        self.lyric_ref = self.ref(data["lyrics"]) if "lyrics" in data else None
        self.lyrics = self.ledger(parse_lrc(self.lyric_ref["path"])) if self.lyric_ref else []
        if "canonical_lyrics" in data:
            ref = self.ref(data["canonical_lyrics"])
            canonical = [line.strip() for line in Path(ref["path"]).read_text(encoding="utf-8-sig").splitlines()
                         if line.strip() and not line.strip().startswith("[")]
            if not self.lyric_ref:
                raise ValueError("canonical_lyrics requires a timed lyrics file")
            timed = [e.text for e in parse_lrc(self.lyric_ref["path"]) if e.text]
            if timed != canonical:
                raise ValueError("Timed lyrics do not match the pinned canonical text, including punctuation")
        self.cues = []
        if "cues" in data:
            ref = self.ref(data["cues"])
            parser = parse_cue if Path(ref["path"]).suffix.lower() == ".cue" else parse_lrc
            events = parser(ref["path"]) if parser is parse_cue else parser(ref["path"], lyrics=False)
            self.cues = self.ledger(events)

    def ref(self, data):
        result = file_ref(data, self.base)
        self.inputs.append(result)
        return result

    def ledger(self, events):
        return frame_ledger(events, start_sample=self.start_sample, end_sample=self.end_sample,
                            sample_rate=self.sample_rate, fps=self.fps, repeats=self.repeats)

    def source_time(self, frame):
        output = Fraction(frame, self.fps)
        # Only the fractional last video frame can exceed exact audio; clamp into last source sample.
        output = min(output, self.duration - Fraction(1, self.sample_rate))
        return float(Fraction(self.start_sample, self.sample_rate) + output % self.slice_duration)

    def continuous_beat(self, frame):
        output = min(Fraction(frame, self.fps), self.duration - Fraction(1, self.sample_rate))
        cycle = int(output // self.slice_duration)
        start = float(Fraction(self.start_sample, self.sample_rate))
        end = float(Fraction(self.end_sample, self.sample_rate))
        return self.clock.beat_at(self.source_time(frame)) + cycle * (
            self.clock.beat_at(end) - self.clock.beat_at(start))

    def verify_inputs(self):
        for ref in self.inputs:
            if sha256(ref["path"]) != ref["sha256"]:
                raise ValueError(f"Input changed since preparation: {ref['path']}")

    def summary(self):
        return dict(schema="framerudiments.plan.v1", dimensions=[self.width, self.height], fps=self.fps,
                    frames=self.frame_count, source_start_sample=self.start_sample,
                    source_end_sample_exclusive=self.end_sample, sample_rate=self.sample_rate,
                    repeats=self.repeats, duration_seconds=float(self.duration),
                    video_duration_seconds=self.frame_count / self.fps,
                    frame_rounding="nearest, ties toward positive infinity; stable source ordering",
                    clock={**self.data['clock'], 'alignment': self.alignment,
                           'phase_status': self.alignment['phase_status'] if self.alignment else self.data['clock'].get('phase_status', 'unverified')},
                    endpoint_fade_seconds=self.fade, endpoint_fade_curve="qsin",
                    lyrics=self.lyrics, cues=self.cues, stemlab_loop=self.loop, effects=self.data.get("effects", []),
                    **({"post_effects": self.data["post_effects"]} if "post_effects" in self.data else {}), inputs=self.inputs,
                    state="prepared; perceptual review pending")
