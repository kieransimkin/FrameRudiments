"""Native integer-pip motion. Never substitute a Python movement engine."""

import math
from importlib.metadata import version
from functools import lru_cache

from .io import file_ref, read_json, fields, number, integer, sha256
from .stemlab import selected_match


@lru_cache(maxsize=1)
def native_identity():
    import dancerudiments as dr

    return dict(
        distribution="dancerudiments",
        version=version("dancerudiments"),
        path=dr.__file__,
        sha256=sha256(dr.__file__),
        pips_per_beat=dr.PIPS_PER_BEAT,
    )


class Motion:
    def __init__(self, config, base, clock):
        import dancerudiments as dr

        self.dr, self.clock = dr, clock
        fields(
            config,
            {
                "kind",
                "pattern",
                "rate",
                "phase_beats",
                "origin_beat",
                "file",
                "source_index",
                "match_index",
                "source_offset_seconds",
                "axes",
                "outside",
                "allow_outside_match",
                "interpolate",
                "repeat_clock",
                "channels",
            },
            "motion",
        )
        if dr.PIPS_PER_BEAT != 64:
            raise ValueError("Native DanceRudiments must use 64 pips per beat")
        self.config = config
        self.kind = config.get("kind", "named")
        self.rate = number(config.get("rate", 1), "motion rate", minimum=0.00001)
        self.phase = number(config.get("phase_beats", 0), "phase")
        self.origin = number(config.get("origin_beat", 0), "motion origin")
        self.file = file_ref(config["file"], base) if "file" in config else None
        self.evidence = {"native": native_identity(), "config": config, "file": self.file}
        self.files = [self.file] if self.file else []
        self.pattern = config.get("pattern")
        self.recording = None
        if config.get("repeat_clock", "continuous") not in {"source", "continuous"}:
            raise ValueError("repeat_clock must be source or continuous")
        if self.kind == "composite":
            if not 1 <= len(config["channels"]) <= 3:
                raise ValueError("Composite motion needs one to three independent output channels")
            self.channels = []
            for channel in config["channels"]:
                fields(channel, {"motion", "axis"}, "composite channel")
                child = Motion(channel["motion"], base, clock)
                self.channels.append((child, integer(channel.get("axis", 0), "channel axis", maximum=2)))
                self.files.extend(child.files)
            self.evidence["channels"] = [child.evidence for child, axis in self.channels]
            return
        if self.kind == "match":
            if self.file is None:
                raise ValueError("Match needs a pinned StemLab report")
            evidence = selected_match(self.file["path"], config["source_index"], config["match_index"])
            match = evidence["selection"]
            self.evidence["match"] = evidence
            self.pattern = match["pattern"]
            self.phase = match["phase_beats"]
            offset = number(config.get("source_offset_seconds", 0), "match source offset")
            self.origin = clock.beat_at(match["start_seconds"] + offset)
            self.window = (match["start_seconds"] + offset, match["end_seconds"] + offset)
        if self.kind in {"named", "match"}:
            if self.pattern not in {x["name"] for x in dr.catalogue()}:
                raise ValueError(f"Unknown native movement: {self.pattern}")
        elif self.kind == "recording":
            from dancerudiments_authoring.recording import load_recording

            if self.rate != 1 or self.phase != 0 or self.origin != 0 or config.get("interpolate", False):
                raise ValueError(
                    "Recordings use their own source clock; no rate, phase or interpolation override"
                )
            recording = load_recording(self.file["path"])
            self.recording = recording.to_native()
            self.dimensions = list(recording.dimensions)
            self.axes = config.get("axes", self.dimensions[:3])
            if not 1 <= len(self.axes) <= 3 or any(x not in self.dimensions for x in self.axes):
                raise ValueError("Choose one to three existing recording axes")
            if config.get("outside", "error") not in {"error", "neutral"}:
                raise ValueError("Recording outside policy must be error or neutral")
            self.evidence["clock"] = recording.to_dict()["clock"]
            self.evidence["dimensions"] = self.dimensions
            self.evidence["provenance"] = recording.provenance
        elif self.kind == "table":
            table = read_json(self.file["path"])
            if table.get("pipsPerBeat", table.get("pips_per_beat")) != 64:
                raise ValueError("Native export needs 64-pip identity")
            if not table.get("version") or not (table.get("wasmSha256") or table.get("extension_sha256")):
                raise ValueError("Native export needs version and binary hash provenance")
            self.row = table["movements"][self.pattern]
            self.samples = self.row["samples"]
            period = self.row.get("periodPips", self.row.get("period_pips"))
            if integer(period, "table period", minimum=1) != len(self.samples):
                raise ValueError("Native table period does not match its samples")
            for row in self.samples:
                if not 1 <= len(row) <= 3:
                    raise ValueError("Native tables have one to three axes")
                for v in row:
                    number(v, "native table sample", minimum=-1, maximum=1)
        else:
            raise ValueError(f"Unknown motion kind: {self.kind}")

    @lru_cache(maxsize=4096)
    def _named(self, pip):
        result = self.dr.sample(self.pattern, pip)
        return (result.x, result.y, result.z)

    def recording_values(self, source_seconds):
        r = self.recording
        pip = math.floor((source_seconds - r.beat_zero_seconds) * r.bpm / 60 * 64)
        if not r.start_pip <= pip < r.start_pip + r.sample_count:
            if self.config.get("outside", "error") == "neutral":
                return [0.0] * len(self.dimensions)
            raise ValueError(f"Source time {source_seconds} is outside non-looping recording")
        return r.sample(pip)

    @property
    def value_dimensions(self):
        return tuple(self.dimensions) if self.recording else ("x", "y", "z")

    def values(self, source_seconds, *, continuous_beat=None):
        """All native dimensions, without the sprite-specific XYZ projection."""
        if self.recording:
            return tuple(self.recording_values(source_seconds))
        return self.sample(source_seconds, continuous_beat=continuous_beat)

    def sample(self, source_seconds, *, continuous_beat=None):
        if self.kind == "composite":
            values = tuple(
                child.sample(source_seconds, continuous_beat=continuous_beat)[axis]
                for child, axis in self.channels
            )
            return (values + (0.0, 0.0))[:3]
        if self.recording:
            values = self.recording_values(source_seconds)
            projected = tuple(values[self.dimensions.index(axis)] for axis in self.axes)
            return (projected + (0.0, 0.0))[:3]
        if self.kind == "match" and not self.config.get("allow_outside_match", False):
            if not self.window[0] <= source_seconds < self.window[1]:
                raise ValueError(
                    "Motion exceeds matched window; choose another fit or explicitly permit extrapolation"
                )
        beat = self.clock.beat_at(source_seconds)
        if self.kind in {"named", "table"} and self.config.get("repeat_clock", "continuous") == "continuous":
            beat = beat if continuous_beat is None else continuous_beat
        position = ((beat - self.origin) * self.rate + self.phase) * 64
        pip = math.floor(position)

        def sample_at(p):
            values = tuple(self.samples[p % len(self.samples)]) if self.kind == "table" else self._named(p)
            return (values + (0.0, 0.0))[:3]

        a = sample_at(pip)
        if not self.config.get("interpolate", False):
            return a
        # Explicit legacy compositor mode, still only adjacent native integer samples.
        b = sample_at(pip + 1)
        fraction = position - pip
        return tuple(x + (y - x) * fraction for x, y in zip(a, b))
