"""Calibrated display analysis of saved StemLab Fourier bands, without FFT DSP."""
from dataclasses import dataclass
import json
import math

from .io import integer, number


@dataclass(frozen=True)
class SpectrumBand:
    name: str
    low_hz: float
    high_hz: float
    divisor: float
    gain: float
    silent: bool
    source_sha256: str
    sample_rate: int
    fft_size: int

    def rms(self, value):
        if self.silent:
            return 0.0
        return number(max(0.0, (value / self.gain + 1) / 2 * self.divisor), "recovered Fourier RMS", minimum=0)


def spectrum_bands(motion, axes):
    if not motion.recording or motion.config.get("outside", "error") != "error":
        raise ValueError("Spectrum needs a bounded Fourier recording with outside: error")
    try:
        raw = motion.evidence["provenance"]
        meta = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(meta, dict) or meta.get("producer") != "StemLab":
            raise ValueError("Spectrum requires StemLab Fourier calibration provenance")
        rows = meta["axes"]
        if len({row["name"] for row in rows}) != len(rows):
            raise ValueError("Duplicate Fourier axis metadata")
        by_name = {row["name"]: row for row in rows}
        bands = []
        for index in axes:
            row = by_name[motion.value_dimensions[index]]
            if row["mode"] != "fourier" or row["output_scale"] != "bipolar":
                raise ValueError("Spectrum dimensions must be calibrated bipolar Fourier bands")
            if integer(row["clipped_windows"], "clipped Fourier windows") != 0:
                raise ValueError("Clipped Fourier axes cannot recover calibrated RMS; re-export without clipping")
            silent = row["silent_axis"]
            if not isinstance(silent, bool):
                raise ValueError("Fourier silent_axis must be boolean")
            divisor = number(row["normalization_divisor"], "Fourier divisor", minimum=0)
            gain = number(row["gain"], "Fourier gain")
            if not silent and (divisor <= 0 or gain == 0):
                raise ValueError("Non-silent Fourier axes need a positive divisor and nonzero gain")
            low = number(row["frequency_low_hz"], "frequency low", minimum=0)
            high = number(row["frequency_high_hz"], "frequency high", minimum=low)
            rate = integer(row["source_sample_rate"], "Fourier source rate", minimum=1)
            fft = integer(row["fourier"]["fft_size"], "Fourier FFT size", minimum=16, maximum=65536)
            digest = row["source_sha256"]
            if high <= low or high > rate / 2 or fft & (fft - 1):
                raise ValueError("Invalid Fourier frequency bounds or FFT size")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                raise ValueError("Fourier source SHA-256 is required")
            bands.append(SpectrumBand(row["name"], low, high, divisor, gain, silent, digest, rate, fft))
        if len({(b.source_sha256, b.sample_rate, b.fft_size) for b in bands}) != 1:
            raise ValueError("Spectrum bands must share source, sample rate and FFT size")
        if any(b.low_hz < a.high_hz for a, b in zip(bands, bands[1:])):
            raise ValueError("Spectrum axes must be in ascending, non-overlapping frequency order")
        return tuple(bands)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Missing or malformed StemLab Fourier calibration provenance") from exc


def peak_indices(levels, *, floor, threshold, contrast, distance, count):
    """Plateau-aware local maxima, adjacent-band contrast and greedy separation."""
    candidates = []
    start = 0
    while start < len(levels):
        end = start
        while end + 1 < len(levels) and abs(levels[end + 1] - levels[start]) <= 1e-9:
            end += 1
        neighbours = ([levels[start - 1]] if start else []) + ([levels[end + 1]] if end + 1 < len(levels) else [])
        surrounding = max(neighbours, default=floor)
        height = levels[start]
        if height >= threshold and height > surrounding and height - surrounding >= contrast:
            candidates.append(((start + end) // 2, start, end, height - surrounding))
        start = end + 1
    selected = []
    for row in sorted(candidates, key=lambda x: (-levels[x[0]], x[0])):
        if all(abs(row[0] - previous[0]) >= distance for previous in selected):
            selected.append(row)
            if len(selected) == count:
                break
    return selected


def analyse_spectrum(frame, *, floor=-72, ceiling=0, hold_seconds=.4, decay_db_per_second=24,
                     peak_count=3, min_peak_db=-60, min_peak_contrast_db=3, min_peak_distance=2):
    if not frame.spectrum_bands:
        raise ValueError("Spectrum frame has no calibrated Fourier bands")
    bands = frame.spectrum_bands
    rms = tuple(band.rms(value) for band, value in zip(bands, frame.values))
    actual_db = tuple(20 * math.log10(value) if value > 0 else None for value in rms)
    levels = tuple(max(floor, value) if value is not None else floor for value in actual_db)
    held = list(levels)
    for sample in frame.history:
        age = frame.context.output_seconds - sample.context.output_seconds
        for j, (band, value) in enumerate(zip(bands, sample.values)):
            amplitude = band.rms(value)
            db = 20 * math.log10(amplitude) if amplitude > 0 else floor
            held[j] = max(held[j], db - max(0, age - hold_seconds) * decay_db_per_second)
    peaks = peak_indices(levels, floor=floor, threshold=min_peak_db, contrast=min_peak_contrast_db,
                         distance=min_peak_distance, count=peak_count)
    def entry(index):
        band = bands[index]
        return dict(axis=band.name, index=index, low_hz=band.low_hz, high_hz=band.high_hz,
                    rms=rms[index], dbfs=actual_db[index], display_dbfs=min(ceiling, levels[index]),
                    held_dbfs=held[index])
    dominant = max(range(len(rms)), key=lambda index: rms[index])
    return dict(kind="stemlab-fourier-band-spectrum", units="PCM-unity RMS dBFS",
                source_sha256=bands[0].source_sha256, fft_size=bands[0].fft_size,
                fft_bin_resolution_hz=bands[0].sample_rate / bands[0].fft_size,
                bands=[entry(j) for j in range(len(bands))],
                peaks=[dict(entry(index), plateau_low_hz=bands[start].low_hz,
                            plateau_high_hz=bands[end].high_hz, adjacent_contrast_db=contrast)
                       for index, start, end, contrast in peaks],
                dominant=entry(dominant) if rms[dominant] > 0 else None,
                peak_method="local band maxima; plateaus centred; adjacent contrast; strongest first",
                hold_clock="observed encoded frames within configured history; no audio true-peak claim")
