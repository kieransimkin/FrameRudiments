"""Read StemLab/DanceMoves beat-timeline.v1 without recomputing music analysis.

Source sample frames and 64-pip beat ordinals define a piecewise-linear phase.
This clock changes procedural rhythmic motion, NEVER audio or lyric/cue time.
"""

from bisect import bisect_right
from fractions import Fraction
import math
from pathlib import Path
import re

from .io import read_json

SCHEMA = "stemlab.beat-timeline.v1"
MAX_BYTES = 32 * 1024 * 1024
MAX_BEATS = 25_000
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_EXACT = re.compile(r"-?\d{1,80}(?:/[1-9]\d{0,79})?\Z")


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in {low}..{high}")
    return value


def _close(value, expected, label, tolerance=1e-7):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or abs(value - float(expected)) > tolerance
    ):
        raise ValueError(f"{label} disagrees with native beat anchors")


def _exact(value, label):
    if not isinstance(value, str) or not _EXACT.fullmatch(value):
        raise ValueError(f"{label} needs a bounded exact rational")
    return Fraction(value)


def phase_pips_at_frame(anchors, origin, source_frame):
    """Exact rational integrated phase; extrapolate only the adjacent outer interval."""
    positions = [row[1] for row in anchors]
    i = max(0, min(len(anchors) - 2, bisect_right(positions, source_frame) - 1))
    beat_a, frame_a = anchors[i]
    beat_b, frame_b = anchors[i + 1]
    return 64 * (
        Fraction(beat_a - origin) + Fraction(source_frame - frame_a, frame_b - frame_a) * (beat_b - beat_a)
    )


class BeatTimelineClock:
    """Clock compatible with BeatClock.beat_at()/pip_at(), with native-frame precision."""

    def __init__(self, document, *, source_sha256, sample_rate, end_sample):
        if (
            not isinstance(document, dict)
            or document.get("schema") != SCHEMA
            or document.get("pips_per_beat") != 64
        ):
            raise ValueError(f"Expected {SCHEMA}, 64 pips/beat")
        audio = document.get("source_audio")
        if (
            not isinstance(audio, dict)
            or audio.get("sha256") != source_sha256
            or not _SHA.fullmatch(source_sha256)
        ):
            raise ValueError("Beat timeline must match the exact audio SHA-256 master")
        rate = _integer(audio.get("sample_rate"), "map sample rate", 1, 768000)
        frames = _integer(audio.get("num_frames"), "map frame count", 2, 10**13)
        _integer(audio.get("num_channels"), "map channel count", 1, 512)
        if rate != sample_rate or frames < end_sample:
            raise ValueError("Beat timeline source sample rate or frame bounds disagree with programme")
        if "duration_seconds" in audio:
            _close(audio["duration_seconds"], Fraction(frames, rate), "master duration")
        if not _SHA.fullmatch(document.get("consensus_sha256", "")):
            raise ValueError("Beat timeline requires original consensus SHA-256 provenance")
        if document.get("review_status") not in {"estimated", "artist_confirmed"}:
            raise ValueError("Beat timeline must retain an explicit review status")
        rows = document.get("beats")
        if not isinstance(rows, list) or not 2 <= len(rows) <= MAX_BEATS:
            raise ValueError("Beat timeline needs between 2 and 25,000 anchors")
        anchors = []
        prior_beat, prior_frame = -1, -1
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Beat anchor must be an object")
            beat = _integer(row.get("beat"), "beat ordinal", 0, 2_147_483_647)
            frame = _integer(row.get("frame"), "beat source frame", 0, frames - 1)
            if beat <= prior_beat or frame <= prior_frame:
                raise ValueError("Beat ordinals and source frames must strictly increase")
            provenance = row.get("source")
            reference = row.get("reference_frame")
            if provenance not in {"consensus", "corrected", "manual"}:
                raise ValueError("Invalid beat provenance")
            if reference is not None:
                _integer(reference, "consensus reference frame", 0, frames - 1)
            if provenance == "consensus" and (reference is None or reference != frame):
                raise ValueError("Uncorrected detector anchors must keep the original reference frame")
            if provenance == "manual" and reference is not None:
                raise ValueError("Manual beats must not claim detector evidence")
            anchors.append((beat, frame))
            prior_beat, prior_frame = beat, frame
        origin = _integer(document.get("origin_beat"), "beat 1 origin", 0, 2_147_483_647)
        if origin not in {beat for beat, _ in anchors}:
            raise ValueError("Beat 1 origin must identify an existing anchor")
        sync = document.get("sync")
        if sync is not None:
            if (
                not isinstance(sync, dict)
                or sync.get("canonical_first_beat_pip") != 0
                or sync.get("pips_per_beat") != 64
            ):
                raise ValueError("Malformed Beat 1 sync metadata")
            origin_frame = next(frame for beat, frame in anchors if beat == origin)
            if sync.get("beat_1_index") != origin or sync.get("beat_1_source_frame") != origin_frame:
                raise ValueError("Beat 1 sync anchor disagrees with beat map")
            _close(sync.get("beat_1_start_seconds"), Fraction(origin_frame, rate), "Beat 1 seconds")
            offset = phase_pips_at_frame(anchors, origin, 0)
            _close(sync.get("sync_phase_offset_pips"), offset, "sync phase")
            _close(sync.get("beat_1_start_pips"), -offset, "Beat 1 start pips")
            if _exact(sync.get("exact_phase_offset_pips"), "exact phase offset") != offset:
                raise ValueError("Exact sync offset disagrees with source-frame beat map")
        timeline = document.get("beat_timeline")
        if timeline is not None:
            if not isinstance(timeline, list) or len(timeline) != len(anchors):
                raise ValueError("Derived beat_timeline must contain all source anchors")
            for derived, row, (beat, frame) in zip(timeline, rows, anchors):
                if (
                    not isinstance(derived, dict)
                    or derived.get("beat") != beat
                    or derived.get("source_frame") != frame
                    or derived.get("pip") != (beat - origin) * 64
                    or derived.get("consensus_reference_frame") != row.get("reference_frame")
                ):
                    raise ValueError("Derived beat_timeline disagrees with actual source anchors")
                _close(derived.get("time_seconds"), Fraction(frame, rate), "derived beat seconds")
        segments = document.get("tempo_segments")
        if segments is not None:
            if not isinstance(segments, list) or len(segments) != len(anchors) - 1:
                raise ValueError("Derived tempo_segments must contain every interval")
            for row, (beat_a, frame_a), (beat_b, frame_b) in zip(segments, anchors, anchors[1:]):
                if (
                    not isinstance(row, dict)
                    or row.get("start_frame") != frame_a
                    or row.get("end_frame") != frame_b
                    or row.get("from_beat") != beat_a
                    or row.get("to_beat") != beat_b
                ):
                    raise ValueError("Derived tempo segment disagrees with source frames")
                bpm = Fraction(60 * rate * (beat_b - beat_a), frame_b - frame_a)
                _close(row.get("bpm"), bpm, "derived interval BPM")
                if "bpm_exact" in row and _exact(row["bpm_exact"], "exact interval BPM") != bpm:
                    raise ValueError("Derived exact interval BPM disagrees with native frames")
        self.anchors = tuple(anchors)
        self.frames = tuple(frame for _, frame in anchors)
        self.origin = origin
        self.sample_rate = rate
        self.num_frames = frames
        self.review_status = document["review_status"]
        self.source_sha256 = source_sha256
        self.consensus_sha256 = document["consensus_sha256"]
        self.bpm = float(self.tempo_at(0))  # Display hint only: beat_at never uses constant BPM.
        self.evidence = dict(
            schema=SCHEMA,
            source_sha256=source_sha256,
            consensus_sha256=self.consensus_sha256,
            origin_beat=origin,
            review_status=self.review_status,
            beat_count=len(anchors),
            sample_rate=rate,
            num_frames=frames,
            phase_rule="piecewise-linear source-frame pips with nearest-interval extrapolation",
        )

    def phase_pips_at_frame(self, source_frame):
        if isinstance(source_frame, bool) or not isinstance(source_frame, (int, Fraction)):
            raise ValueError("Source frame must be integer or rational")
        return phase_pips_at_frame(self.anchors, self.origin, source_frame)

    def _source_frame(self, source_seconds):
        if isinstance(source_seconds, bool) or not isinstance(source_seconds, (int, float, Fraction)):
            raise ValueError("Source seconds must be finite")
        if isinstance(source_seconds, float) and not math.isfinite(source_seconds):
            raise ValueError("Source seconds must be finite")
        value = Fraction(str(source_seconds)) * self.sample_rate
        # Project.source_time() crosses Fraction -> float at the public render
        # boundary. Recover a true integer source frame only within floating
        # representation error; do not quantise authentic subframe positions.
        nearest = math.floor(value + Fraction(1, 2))
        if abs(value - nearest) <= Fraction(1, 1_000_000):
            return Fraction(nearest)
        return value

    def beat_at(self, source_seconds):
        source_frame = self._source_frame(source_seconds)
        return float(self.phase_pips_at_frame(source_frame) / 64)

    def tempo_at(self, source_seconds):
        source_frame = self._source_frame(source_seconds)
        i = max(0, min(len(self.anchors) - 2, bisect_right(self.frames, source_frame) - 1))
        a, b = self.anchors[i : i + 2]
        return float(Fraction(60 * self.sample_rate * (b[0] - a[0]), b[1] - a[1]))

    def pip_at(self, source_seconds, rate=1.0, phase_beats=0.0):
        return math.floor((self.beat_at(source_seconds) * rate + phase_beats) * 64)


def load_beat_timeline(path, *, source_sha256, sample_rate, end_sample):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Beat timeline exceeds 32 MiB")
    return BeatTimelineClock(
        read_json(path), source_sha256=source_sha256, sample_rate=sample_rate, end_sample=end_sample
    )
