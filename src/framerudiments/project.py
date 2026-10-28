"""Validated portable render projects; private media remains outside the package."""

from fractions import Fraction
import math
from pathlib import Path

from .io import read_json, file_ref, fields, integer, number, sha256
from .timeline import BeatClock, parse_lrc, parse_cue, frame_ledger
from .stemlab import beat_grid, selected_loop
from .beat_alignment import beat_alignment
from .beat_timeline import load_beat_timeline
from .word_timing import parse_word_lrc, word_frame_ledger, assign_words_to_lines
from .output_quality import OutputQuality


class Project:
    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        return cls(read_json(path), path.parent, project_path=path)

    def __init__(self, data, base, *, project_path=None):
        self.base, self.path, self.data = Path(base), project_path, data
        fields(
            data,
            {
                "schema",
                "title",
                "artist",
                "purpose",
                "width",
                "height",
                "fps",
                "fps_reason",
                "audio",
                "clock",
                "font",
                "lyrics",
                "cues",
                "background",
                "layers",
                "captions",
                "identity",
                "effects",
                "post_effects",
                "effect_graph",
                "effect_graph_controls",
                "effect_graph_backend",
                "stemlab_loop",
                "notes",
                "lyric_art",
                "canonical_lyrics",
                "word_lyrics",
                "word_captions",
                "word_indicator",
                "render_quality",
                "visualiser_routing",
                "stem_sources",
                "layer_masks",
                "layer_animations",
                "layer_displacement",
                "visualiser_stage_injections",
            },
            "project",
        )
        if data.get("schema") != "framerudiments.project.v1":
            raise ValueError("Expected framerudiments.project.v1")
        if "effect_graph_backend" in data:
            if data["effect_graph_backend"] not in ("cpu", "gpu", "auto"):
                raise ValueError("effect_graph_backend must be cpu, gpu or auto")
            if "effect_graph" not in data:
                raise ValueError("effect_graph_backend requires effect_graph")
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
        # Set by the optional working-cache staging pass. Summaries retain the
        # canonical source identities while render consumers use cached paths.
        self.canonical_inputs = None
        # Named aliases reference ordinary, checksum-pinned StemLab-compatible
        # recording motions. No analysis data or new DSP is introduced here.
        sources = data.get("stem_sources", {})
        if not isinstance(sources, dict) or len(sources) > 32:
            raise ValueError("stem_sources must be an object with at most 32 entries")
        import re

        self.stem_sources = {}
        for name, motion in sources.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
                raise ValueError("Invalid stem_sources name")
            if not isinstance(motion, dict) or motion.get("kind") != "recording":
                raise ValueError("stem_sources entries must be native recording motions")
            self.stem_sources[name] = dict(motion)
        self.output_quality = OutputQuality(data.get("render_quality", {}))
        self.audio = dict(data["audio"])
        fields(
            self.audio,
            {"source", "sample_rate", "start_sample", "end_sample", "repeats", "fade_seconds"},
            "audio",
        )
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
        self.fade = number(
            self.audio.get("fade_seconds", 0.12 if self.purpose == "short" else 0),
            "fade seconds",
            minimum=0,
            maximum=float(self.duration) / 2,
        )
        cfg = data["clock"]
        fields(
            cfg,
            {
                "bpm",
                "origin",
                "authority",
                "status",
                "phase_status",
                "beat_grid",
                "beat_alignment",
                "beat_timeline",
            },
            "clock",
        )
        if not cfg.get("authority") or cfg.get("status") not in {"confirmed", "estimated", "synthetic"}:
            raise ValueError("Clock needs authority and confirmed/estimated/synthetic status")
        self.alignment = None
        self.beat_timeline = None
        if "beat_timeline" in cfg:
            if any(key in cfg for key in ("beat_alignment", "beat_grid", "origin")):
                raise ValueError("Variable beat timeline cannot combine with a second phase/BPM source")
            if cfg["status"] == "synthetic":
                raise ValueError("StemLab beat timeline cannot claim synthetic source authority")
            ref = self.ref(cfg["beat_timeline"])
            self.clock = load_beat_timeline(
                ref["path"],
                source_sha256=self.audio_ref["sha256"],
                sample_rate=self.sample_rate,
                end_sample=self.end_sample,
            )
            if cfg.get("phase_status") not in (None, "estimated", "confirmed"):
                raise ValueError("Beat timeline phase status must be estimated or confirmed")
            if self.clock.review_status == "estimated" and (
                cfg["status"] != "estimated" or cfg.get("phase_status") == "confirmed"
            ):
                raise ValueError("Estimated beat timeline cannot claim confirmed tempo or phase")
            if "bpm" in cfg:
                number(cfg["bpm"], "Nominal BPM", minimum=0.00001)  # display hint only
            self.beat_timeline = dict(self.clock.evidence, file=ref)
        elif "beat_alignment" in cfg:
            if "beat_grid" in cfg or "origin" in cfg:
                raise ValueError("beat_alignment cannot be combined with a separate beat grid or origin")
            ref = self.ref(cfg["beat_alignment"])
            self.alignment = beat_alignment(
                ref["path"], self.audio_ref["sha256"], self.sample_rate, self.end_sample
            )
            if "bpm" in cfg and abs(number(cfg["bpm"], "BPM") - self.alignment["bpm"]) > 1e-8:
                raise ValueError("Explicit BPM conflicts with the beat alignment reading")
            if self.alignment["bpm_status"] == "estimated" and cfg["status"] != "estimated":
                raise ValueError("An estimated alignment BPM cannot be promoted to confirmed or synthetic")
            if "phase_status" in cfg and cfg["phase_status"] != self.alignment["phase_status"]:
                raise ValueError("Project phase authority conflicts with the saved reading")
            self.clock = BeatClock(bpm=self.alignment["bpm"], origin=self.alignment["first_beat_seconds"])
        else:
            grid = beat_grid(self.ref(cfg["beat_grid"])["path"]) if "beat_grid" in cfg else None
            self.clock = BeatClock(bpm=cfg.get("bpm"), origin=cfg.get("origin", 0), beats=grid)
        self.font_ref = self.ref(data["font"]) if "font" in data else None
        if (
            data.get("lyrics")
            or data.get("identity")
            or (data.get("word_lyrics") and (data.get("captions") or data.get("word_captions")))
        ) and not self.font_ref:
            raise ValueError("Pin an explicit font for all authored text")
        self.lyric_ref = self.ref(data["lyrics"]) if "lyrics" in data else None
        self.lyrics = self.ledger(parse_lrc(self.lyric_ref["path"])) if self.lyric_ref else []
        self.word_ref = self.ref(data["word_lyrics"]) if "word_lyrics" in data else None
        if self.word_ref and Path(self.word_ref["path"]).suffix.lower() != ".lrc":
            raise ValueError("word_lyrics must be a pinned .lrc file")
        self.words = (
            word_frame_ledger(
                parse_word_lrc(self.word_ref["path"]),
                start_sample=self.start_sample,
                end_sample=self.end_sample,
                sample_rate=self.sample_rate,
                fps=self.fps,
                repeats=self.repeats,
                frame_count=self.frame_count,
            )
            if self.word_ref
            else []
        )
        if self.words and self.lyrics:
            assign_words_to_lines(self.words, self.lyrics)
        if "word_indicator" in data and (not self.word_ref or not self.lyric_ref or not data.get("captions")):
            raise ValueError("word_indicator requires lyrics, word_lyrics and full-line captions")
        if "word_captions" in data and not self.word_ref:
            raise ValueError("word_captions requires word_lyrics")
        if data.get("captions", {}).get("word_mode", "off") != "off" and not self.word_ref:
            raise ValueError("caption word_mode requires word_lyrics")
        if "canonical_lyrics" in data:
            ref = self.ref(data["canonical_lyrics"])
            canonical = [
                line.strip()
                for line in Path(ref["path"]).read_text(encoding="utf-8-sig").splitlines()
                if line.strip() and not line.strip().startswith("[")
            ]
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
        return frame_ledger(
            events,
            start_sample=self.start_sample,
            end_sample=self.end_sample,
            sample_rate=self.sample_rate,
            fps=self.fps,
            repeats=self.repeats,
        )

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
            self.clock.beat_at(end) - self.clock.beat_at(start)
        )

    def verify_inputs(self):
        references = [*(self.canonical_inputs or []), *self.inputs]
        seen = set()
        for ref in references:
            identity = (ref["path"], ref["sha256"])
            if identity in seen:
                continue
            seen.add(identity)
            if sha256(ref["path"]) != ref["sha256"]:
                raise ValueError(f"Input changed since preparation: {ref['path']}")

    def summary(self):
        return dict(
            schema="framerudiments.plan.v1",
            dimensions=[self.width, self.height],
            fps=self.fps,
            frames=self.frame_count,
            source_start_sample=self.start_sample,
            source_end_sample_exclusive=self.end_sample,
            sample_rate=self.sample_rate,
            repeats=self.repeats,
            duration_seconds=float(self.duration),
            video_duration_seconds=self.frame_count / self.fps,
            frame_rounding="nearest, ties toward positive infinity; stable source ordering",
            clock={
                **self.data["clock"],
                "alignment": self.alignment,
                "beat_timeline": self.beat_timeline,
                "phase_status": self.alignment["phase_status"]
                if self.alignment
                else (
                    "confirmed"
                    if self.beat_timeline and self.beat_timeline["review_status"] == "artist_confirmed"
                    else self.data["clock"].get(
                        "phase_status", "estimated" if self.beat_timeline else "unverified"
                    )
                ),
            },
            endpoint_fade_seconds=self.fade,
            endpoint_fade_curve="qsin",
            lyrics=self.lyrics,
            cues=self.cues,
            stemlab_loop=self.loop,
            effects=self.data.get("effects", []),
            stem_sources=self.stem_sources,
            layer_masks=self.data.get("layer_masks", {}),
            **(
                {"word_timings": {"file": self.word_ref, "event_count": len(self.words)}}
                if self.word_ref
                else {}
            ),
            **({"post_effects": self.data["post_effects"]} if "post_effects" in self.data else {}),
            **({"effect_graph": self.data["effect_graph"]} if "effect_graph" in self.data else {}),
            **(
                {"effect_graph_controls": self.data["effect_graph_controls"]}
                if "effect_graph_controls" in self.data else {}
            ),
            **({"effect_graph_backend": self.data["effect_graph_backend"]}
               if "effect_graph_backend" in self.data else {}),
            render_quality=self.output_quality.evidence,
            **(
                {"visualiser_routing": self.data["visualiser_routing"]}
                if "visualiser_routing" in self.data
                else {}
            ),
            inputs=self.canonical_inputs or self.inputs,
            state="prepared; perceptual review pending",
        )
