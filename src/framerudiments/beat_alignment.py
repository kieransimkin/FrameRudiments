"""Validate a source-bound StemLab clock handoff; no detector or DSP implementation."""

from fractions import Fraction
import math
from pathlib import Path
import re

from .io import read_json


def beat_alignment(path, source_sha256, sample_rate, end_sample):
    path = Path(path)
    if path.stat().st_size > 16384:
        raise ValueError("Beat alignment exceeds 16 KiB")
    reading = read_json(path)
    if (
        reading.get("schema") != "stemlab.beat-alignment.v1"
        or reading.get("pips_per_beat") != 64
        or reading.get("canonical_first_beat_pip") != 0
        or reading.get("source_sha256") != source_sha256
    ):
        raise ValueError("Beat alignment requires the exact audio master and 64-pip contract")
    for key in ("source_sha256", "evidence_sha256", "report_sha256"):
        if not isinstance(reading.get(key), str) or not re.fullmatch("[a-f0-9]{64}", reading[key]):
            raise ValueError("Beat alignment needs source, detector and report SHA-256 identities")
    for key in ("analysis_id", "bpm_basis"):
        if not isinstance(reading.get(key), str) or not re.fullmatch("[a-zA-Z0-9_.-]{1,128}", reading[key]):
            raise ValueError("Beat alignment needs an explicit analysis and BPM basis")
    exact = []
    for key in ("exact_bpm", "first_beat_exact_seconds", "exact_offset_pips"):
        value = reading.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"-?\d{1,80}(?:/[1-9]\d{0,79})?", value):
            raise ValueError("Beat alignment requires bounded exact rational clock values")
        exact.append(Fraction(value))
    tempo, first, offset = exact
    if not 20 <= tempo <= 400 or not 0 <= first <= 86400 or first * tempo * 64 / 60 + offset != 0:
        raise ValueError("Beat alignment must map the first source beat to pip zero")
    for key, value in zip(("bpm", "first_beat_seconds", "offset_pips"), exact):
        actual = reading.get(key)
        if type(actual) not in (int, float) or not math.isfinite(actual) or abs(actual - float(value)) > 1e-7:
            raise ValueError("Exact and numeric beat alignment values disagree")
    audio = reading.get("source_audio") or {}
    if (
        reading.get("source_audio_verified") is not True
        or audio.get("sample_rate") != sample_rate
        or type(audio.get("num_frames")) is not int
        or audio["num_frames"] < end_sample
        or first >= Fraction(audio["num_frames"], sample_rate)
    ):
        raise ValueError("Beat alignment source sample rate/frame bounds disagree with the project")
    if reading.get("bpm_status") not in {"canonical", "estimated"} or reading.get("phase_status") not in {
        "estimated",
        "confirmed",
        "synthetic",
    }:
        raise ValueError("Beat alignment must retain BPM and phase authority separately")
    return reading
