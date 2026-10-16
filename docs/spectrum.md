# Fourier spectrum effects and peak analysis

The `spectrum` rudiment renderer uses saved **StemLab 1.6+ Fourier recordings**.
It renders calibrated RMS band levels, local frequency-band peaks, dominant
bands and peak-hold markers. FrameRudiments does not run another FFT or copy
StemLab's analysis engine. Rendering an existing pinned recording needs only
the ordinary FrameRudiments dependencies.

```json
{
  "id": "spectrum",
  "type": "rudiment",
  "renderer": "spectrum",
  "bounds": [50, 500, 1030, 1500],
  "motion": {
    "kind": "recording",
    "file": {"path": "fourier.json", "sha256": "<exact recording SHA-256>"}
  },
  "history_frames": 256,
  "style": {
    "floor_db": -72,
    "ceiling_db": 0,
    "hold_seconds": 0.4,
    "decay_db_per_second": 24,
    "peak_count": 3,
    "min_peak_db": -60,
    "min_peak_contrast_db": 3,
    "min_peak_distance": 2,
    "labels": true
  }
}
```

Pin a project font for labels, or set `labels: false`. Select a smaller ascending
set of `axes` when a viewport cannot fit all bands. Source identities remain
explicit: a stem spectrum and the master soundtrack can have different hashes;
the author must verify the intended stem/master association and source offset.

## Obtain real Fourier evidence

Use the current StemLab `record-rudiment` route, reusing the authorised master
or saved stem without running separation again:

```sh
stemlab record-rudiment master.wav --bpm 120 --frequency-bands 64 \
  --fft-size 4096 --normalize global --output frequency-64
```

Replace example BPM with actual authority and retain its confirmation/phase
status. Pin the generated native recording and inspect its source, clock,
frequency range and normalization provenance. StemLab also supports bounded
frequency ranges and log-spaced buckets; inspect its current documentation.

For a reproducible synthetic example, make StemLab 1.6+ available in the same
Python environment, then run:

```sh
framerudiments demo spectrum-demo --spectrum --font /path/to/font.ttf
framerudiments render spectrum-demo/project.json --output spectrum-render
```

This generates an antiphase stereo sweep, asks StemLab for 64 Fourier bands,
and shows spectrum/peak holds with selected waveform traces. The normal
`--rudiments` demo does not import StemLab. No model or private audio is used.

## Calibration and limits

Frequency labels come from the selected recording's exact band metadata.
All selected bands must share source hash, sample rate and FFT size and remain
in ascending, non-overlapping frequency order. The native sample is bipolar;
FrameRudiments reverses its recorded signed gain and per-band normalization
divisor to recover PCM-unity RMS. It handles StemLab's explicitly silent axes
as zero energy, even though their native value is centred at zero. Per-axis
normalization therefore does not falsely equalise the displayed band strengths.

Missing calibration, zero gain on a non-silent axis, clipped exports, generic
motion mislabeled as Fourier, and neutral-outside capture policies are rejected.
Re-export a recoverable bounded recording rather than guessing. RMS dBFS is
`20*log10(RMS)` relative to PCM unity; zero energy has `dbfs: null` in JSON and
draws at the display floor. The floor/ceiling clip the picture, not the recorded
RMS/dB values. These are windowed frequency-band measurements; their ranges do
not establish an exact sinusoidal frequency, pitch, instrument or audio true peak.

Peaks are local maxima across bands. Equal-height plateaus use their middle
band; `min_peak_contrast_db` is contrast to the immediately neighbouring bands,
not scientific full-baseline prominence. Candidates pass the dB threshold,
then strongest-first selection applies the minimum band-index spacing and
count limit. Boundary bands use available neighbours. Dominant-band reporting
is separate and remains available when no local peak passes these controls.

Peak hold is computed from observed encoded-frame history: after the hold time,
each observed value falls by the configured dB/second rate. It uses no hidden
simulation state or future samples. History is bounded, so choose enough frames
for the intended hold/decay span. Default spectrum history is 256 samples at
stride 1 (other renderers default to 64). This is frame-sampled peak hold, not
audio true-peak measurement between frames. Native recording and FFT-window
resolution remain visible in the evidence.

## Style and reusable effect signals

In addition to the common `colours`, `padding` and `background`, spectrum accepts:

| Field | Default and range |
| --- | --- |
| `floor_db`, `ceiling_db` | -72 / 0; finite ordered display limits, floor -240..0, ceiling up to +60 |
| `hold_seconds` | .4, 0..60 |
| `decay_db_per_second` | 24, .01..240 |
| `peak_count` | 3, 1..32 |
| `min_peak_db` | -60, -240..60 |
| `min_peak_contrast_db` | 3, 0..240 |
| `min_peak_distance` | 2, 1..256 band indices |
| `gap` | 1 pixel, nonnegative |
| `peak_colour`, `label_colour` | CSS colours |
| `labels`, `font_size` | true; responsive default font size, explicit range 6..128 pixels |

Every render/preflight streams active spectrum reports into
`rudiment-analysis.jsonl`, binding the sidecar hash in the final manifest.
Each record identifies encoded frame, source/output seconds and layer. It
contains per-band RMS, actual/display/held dBFS, source/FFT identity, dominant
band and selected peaks with exact ranges and adjacent contrast. The stream
uses bounded memory and can drive later glow, displacement, particle or cue
effects without recomputing the FFT. Silent frames explicitly report no dominant
band; inactive layers emit no report.

Trusted Python renderers can set `requires_spectrum = True` to request the same
validated `RudimentFrame.spectrum_bands` and call
`framerudiments.spectrum.analyse_spectrum(frame)` for calibrated effect signals.
An optional `prepare(project)` hook may pin assets or load the pinned font.
An optional JSON-safe `frame_report` is streamed after rendering. These hooks
are trusted Python code, never executable imports supplied by project JSON.
