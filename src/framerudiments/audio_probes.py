"""Versioned, sample-clock audio interventions; no artist media or model inference.

These are synthetic measurement signals, not stems or a music classifier. Frequencies
refer to supplied PCM, not a renderer's undocumented internal bass/mid/treble bins.
"""
from __future__ import annotations

from array import array
import hashlib
import math
from pathlib import Path
import random
import sys

PROTOCOL = "framerudiments.audio-interventions/1"
SAMPLE_RATE = 44100
DURATION = 12.0
WARMUP = 2.0
CORE = ("silence", "bass-pulses", "mid-pulses", "treble-pulses", "rhythmic-mix")
EXTENDED = CORE + ("bass-quiet", "bass-fast", "bass-sustain", "broadband-pulses")


def events_for(trial: int, *, fast: bool = False) -> list[dict]:
    if type(trial) is not int or not 0 <= trial < 12:
        raise ValueError("Trial must be an integer in 0..11")
    # Aperiodic and changed between independent trials: autonomous oscillations
    # must not be mistaken for an audio response at one convenient phase.
    rng = random.Random(83191 + trial)
    onsets = [2.5, 4.3, 6.5, 8.9]
    onsets = [t + rng.uniform(-.13, .13) for t in onsets]
    if fast:
        onsets = sorted(onsets + [t + .7 for t in onsets])
    return [{"on": round(t, 6), "off": round(t + .32, 6)} for t in onsets]


def envelope(t: float, events: list[dict]) -> float:
    for event in events:
        if event["on"] <= t < event["off"]:
            x = t - event["on"]
            remaining = event["off"] - t
            # Click-free, equal envelope across the frequency interventions.
            return min(1.0, x / .012, remaining / .025)
    return 0.0


def make_probe(name: str, trial: int, directory: Path) -> dict:
    if name not in EXTENDED:
        raise ValueError("Unknown probe")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    events = events_for(trial, fast=name == "bass-fast")
    frequency = {"bass-pulses": 80, "mid-pulses": 1000, "treble-pulses": 6000,
                 "bass-quiet": 80, "bass-fast": 80, "bass-sustain": 80}.get(name)
    if name == "bass-sustain":
        events = [{"on": events[0]["on"], "off": 10.0}]
    rng = random.Random(97531 + trial)
    mono = array("f")
    for i in range(round(DURATION * SAMPLE_RATE)):
        t = i / SAMPLE_RATE
        gate = envelope(t, events)
        if name == "silence":
            value = 0.0
        elif name == "rhythmic-mix":
            # Deterministic rhythm fixture; never described as the user's music.
            if 2.4 <= t < 10.2:
                phase = (t - 2.4) % .5
                bass = math.sin(math.tau * 80 * t) * math.exp(-phase * 14)
                chord = sum(math.sin(math.tau * f * t) for f in (220, 277.18, 329.63)) / 3
                high = rng.uniform(-1, 1) * math.exp(-((t - 2.4) % .25) * 70)
                value = .65 * bass + .2 * chord + .15 * high
            else:
                value = 0.0
        elif name == "broadband-pulses":
            value = gate * rng.uniform(-1, 1)
        else:
            value = gate * math.sin(math.tau * frequency * t)
        mono.append(value)
    # Match whole analysis-window RMS, not peak amplitude, for the frequency
    # and broadband tests. Quiet is exactly half amplitude of bass-pulses.
    # Fast/sustain are also energy-matched; their instantaneous levels differ.
    lo, hi = int(WARMUP * SAMPLE_RATE), int(10.5 * SAMPLE_RATE)
    rms = math.sqrt(math.fsum(x * x for x in mono[lo:hi]) / (hi - lo))
    target_rms = .06 if name == "bass-quiet" else .12
    factor = target_rms / rms if rms else 0.0
    peak = max((abs(x * factor) for x in mono), default=0)
    if peak >= .98:
        raise ValueError("Protocol signal would clip; change the protocol, not individual probes")
    pcm = array("f")
    for x in mono:
        pcm.extend((x * factor, x * factor))
    if sys.byteorder != "little":
        pcm.byteswap()
    payload = pcm.tobytes()
    path = directory / f"{name}-{trial}.f32"
    if path.exists() and path.read_bytes() != payload:
        raise ValueError("Existing audio stimulus differs; choose a new workspace")
    path.write_bytes(payload)
    return {"id": name, "kind": "silence" if name == "silence" else
            "synthetic-music" if name == "rhythmic-mix" else "synthetic-probe",
            "path": str(path.resolve()), "sha256": hashlib.sha256(payload).hexdigest(),
            "sample_rate": SAMPLE_RATE, "channels": 2, "format": "f32le",
            "samples_per_channel": len(pcm) // 2, "duration_seconds": DURATION,
            "frequency_hz": frequency, "trial": trial,
            "events": [] if name in ("silence", "rhythmic-mix") else events,
            "analysis_window": [WARMUP, 10.5], "warmup_seconds": WARMUP,
            "rms": target_rms if rms else 0.0, "peak": peak,
            "level_convention": "linear PCM RMS over 2..10.5 seconds; not equal perceived loudness",
            "comparison_caveat": "Tempo/envelope changes conserve window energy, not instantaneous peak level"}


def pcm_window(pcm: array, frame: int, fps: int = 60, sample_rate: int = SAMPLE_RATE) -> array:
    """Match EffectAudio.at: 1024 stereo history samples at the exact render clock."""
    if type(frame) is not int or frame < 0 or type(fps) is not int or fps < 1:
        raise ValueError("Invalid render clock")
    if math.ceil(sample_rate / fps) > 1024:
        raise ValueError("Render rate would leave gaps between 1024-sample audio windows")
    end = min(len(pcm) // 2, frame * sample_rate // fps)
    start = max(0, end - 1024)
    return array("f", [0]) * ((1024 - (end - start)) * 2) + pcm[start * 2:end * 2]


def load_pcm(stimulus: dict) -> array:
    data = Path(stimulus["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != stimulus["sha256"] or len(data) % 8:
        raise ValueError("PCM stimulus changed or has invalid stereo length")
    if len(data) != round(DURATION * SAMPLE_RATE) * 8:
        raise ValueError("Unexpected protocol duration")
    pcm = array("f")
    pcm.frombytes(data)
    if sys.byteorder != "little":
        pcm.byteswap()
    return pcm
