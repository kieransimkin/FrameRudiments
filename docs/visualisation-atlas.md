# Visualisation Atlas: researched display families and implementation map

FrameRudiments offers **161 named built-in Rudiment renderers**: the five
original views, 36 advanced plots, 60 annular modes and 60 additional effects. This catalogue documents techniques from
oscilloscopes, analogue spectrum analysers, classic Winamp-era visualisers,
audio editors, scientific sonograms and modern time-frequency plotting tools.
Run `framerudiments renderers` for the authoritative registry and consult the
[CI-generated gallery](https://kieransimkin.github.io/FrameRudiments/) for
all modes, with a searchable, timeline-scrubbable preview.

**Scientific boundary**: `waveform*`, `phase-*`, `particles`, `bars`, and `path`
show selected **Rudiment motion/recording channels**, **not PCM samples**.
`Spectrum*` and `spectrogram*` work only with calibrated, pinned StemLab Fourier
band recordings: no independent FFT, no duplicate StemLab DSP and no false
pitch, audio true peak, phase or stereo-correlation claim. Audio-spectrum
RMS/levels come from the calibrated native recording, exactly as documented
in [spectrum.md](spectrum.md). This module changes the **rendering of measured
bands**, not the upstream analysis. If StemLab recording's source/audio clock
does not match the programme, the author must correct the source association.

## New: 60 circular modes, gradients and DanceRudiment modulation

See the [Annular and Modulation reference](annular-modulation.md) for the full
60-mode renderer table, 16 built-in palette families, custom RGB/HSV gradients,
spatial and amplitude-based colour mapping, and up to 32 independent native
DanceRudiment modulators per layer. Each modulator can drive renderer geometry
or shared colour/image properties at the precise native source/frame time. The
CI gallery includes 161 renderer panels and 20 additional native-modulated
colour demonstration panels; all spectral inputs remain synthetic test values.

## Further expansion: 60 spectrum/heatmap/oscilloscope visualisations

See the [Expanded Rendering Effects guide](expanded-effects.md) for all 60 new
named modes, optional CRT/dither/grain/chromatic treatments and modulation
examples. Existing annular views remain separately configurable. This expansion
changes presentation only: no new FFT, inferred phase or stereo analysis.

## Supported views: spectrum and spectral history

Each item below is a distinct JSON `renderer` name, not just a colour preset.
All modes use the *same* calibrated measurement contract and native
`RudimentFrame` history.

| Family / historic inspiration | Implemented renderers |
| --- | --- |
| Conventional analyser (bars, graph, dots, stems, steps) | `spectrum` (existing, with holds/labels), `spectrum-line`, `spectrum-area`, `spectrum-dots`, `spectrum-stems`, `spectrum-steps`, `spectrum-peaks` |
| Frequency-axis alternatives | `spectrum-log`, `spectrum-mel`, `spectrum-third-octave` |
| Classic visualiser / polar art | `spectrum-mirror`, `spectrum-radial`, `spectrum-ring`, `spectrum-spiral` |
| CRT persistence, RF-style waterfall, ridgeline / joyplot | `spectrum-horizon`, `spectrum-waterfall`, `spectrum-ridgeline` |
| Sonogram / 2D heatmap | `spectrogram-linear`, `spectrogram-log`, `spectrogram-mel`, `spectrogram-bark`, `spectrogram-erb` |
| Spectrogram variants | `spectrogram-peaks`, `spectrogram-contours`, `spectrogram-mirror`, `spectrogram-transposed`, `spectrogram-polar` |

**Frequency remapping limitations:** `mel`, `bark`, `erb`, `log` and
`third-octave` in this implementation position the **original recorded
Fourier bands** on the named axis; they do not create a mel filter-bank,
third-octave integral, Bark filter-bank, ERB gammatone analysis or CQT. Every
band retains its original low/high Hz provenance and original RMS value;
colour normalisation is visual only. `spectrogram-peaks` selects local
**band** maxima, not reassigned instantaneous frequencies. `spectrogram-contours`
quantises band-level colours; it is not a true continuous iso-energy contour
estimate. Perspective/radial visualisers reinterpret the same measurements,
not additional dimensions of the signal. An upstream FFT's temporal sampling
and bin resolution are never improved by a new axis.

## Supported views: rudiment-domain time traces

| Historic / technical inspiration | Implemented renderers |
| --- | --- |
| Sampled time-domain traces | `waveform` (existing), `waveform-area`, `waveform-dots`, `waveform-steps`, `waveform-oscilloscope` |
| Envelope / mirrored displays | `waveform-mirror`, `waveform-envelope`, `waveform-ribbon` |
| Polar / XY / scope modes | `waveform-polar`, `path` (existing), `phase-portrait`, `phase-points` |
| Motion displays | `bars`, `particles` (existing) |

A `phase-portrait` plots two **selected Rudiment channels** through time. It
resembles an XY oscilloscope or Lissajous display but is **not** an audio
stereo vectorscope unless the underlying channels explicitly represent
separately authorised stereo signal data (and use the appropriate units).
Similarly, `waveform-envelope` is min/max of the *observed frame-addressed
Rudiment samples*; it cannot measure sub-frame PCM peaks.

## Documented but deliberately not misrepresented as implemented

The following established signal analyses require additional upstream data or
true DSP; a presentation-layer remapping is **not** equivalent:

| Research family | Needed input / reason not built here |
| --- | --- |
| True short-time Fourier transform (STFT), window variants, and reassigned spectrogram | Raw PCM and upstream STFT or instantaneous frequency/group delay; current renderer only receives RMS band values |
| Real mel spectrogram, Bark/ERB/gammatone filterbanks, octave-energy aggregation | Upstream filterbank energies or justified FFT-bin weights; an axis change cannot reconstruct them |
| Constant-Q / variable-Q spectrogram, wavelet scalogram | Multiresolution coefficients from an appropriate upstream transform |
| Spectral phase / instantaneous-frequency sonogram | Complex-valued STFT coefficients and phase evolution, not magnitude-only RMS |
| Harmonic/percussive separation, reassigned peaks, sinusoidal ridges | Appropriate transforms, separation models and reliable ridge tracking |
| Pitch, chromagram, HPCP, tonal centroid / Tonnetz, tempogram | Pitch-class and beat-evidence extraction; frequency-band maxima are not necessarily fundamentals |
| Cepstrum, MFCC, spectral flux, spectral centroid/roll-off heatmaps | Derived features computed from calibrated spectrum with explicit spectral semantics |
| True PCM stereo goniometer, Lissajous, correlation / mid-side meters | Synchronous left/right decoded audio; motion channels are not stereo PCM |
| Loudness meter (LUFS), true-peak monitor, RMS programme meter | Full PCM, appropriate loudness/oversampling and channel-weighting standards |
| MilkDrop / Butterchurn / projectM GPU feedback presets | Already handled by the separately pinned **full-frame** `effects` engines; do not duplicate them in Rudiment renderers |

## Configure a new visualisation layer

For a spectrum view use a **real and pinned** StemLab Fourier recording, not
just a named Rudiment pattern:

```json
{
  "id": "sonogram",
  "type": "rudiment",
  "renderer": "spectrogram-log",
  "bounds": [30, 300, 1050, 900],
  "motion": {
    "kind": "recording",
    "file": {"path": "fourier.json", "sha256": "<actual digest>"}
  },
  "history_frames": 256,
  "history_stride": 1,
  "style": {
    "floor_db": -72,
    "ceiling_db": 0,
    "contrast": 1.3,
    "history_limit": 96,
    "background": "#101b2e",
    "colours": ["#76d4cf", "#e5ad6d", "#c9a4ff"]
  }
}
```

The first 36 advanced renderers use common `colours`, `background`, `padding`; spectral
modes additionally accept `floor_db` (-240..0), `ceiling_db` (> floor, up to
60), `line_width` (1..24), `contrast` (.1..4), and `history_limit` (8..256).
`contrast` is a display-only heatmap gamma, not a change to stored levels;
`history_limit` down-samples *observed* frames for raster performance. Motion
modes accept `line_width` (1..24) and `grid` (boolean). Unknown styles fail
closed; existing `spectrum` retains its peak/hold configuration separately.

All renderers remain random-access pure image functions, transparent-bounds
compatible, and registered only in the explicit local registry. A `requires_spectrum`
renderer validates StemLab source identity, FFT size, band metadata, clipping,
normalisation, and order at compositor setup. Its normal streaming
`rudiment-analysis.jsonl` contains spectral peak/band evidence, unaffected by
which view was chosen. Defaults allocate 256 observed history frames to
spectrum/spectrogram modes, and 64 to motion modes; override for longer trails.

## Public CI gallery and verification

`.github/workflows/visualisation-gallery.yml` builds the entire test gallery on
PRs and main. It first runs the full visualisation mode tests and linting, then
renders **four** frames per registered mode and twelve native-modulated style scenes, checks every expected preview and
publishes the complete `catalogue.json` and HTML/PNG site as a portable Actions
artifact. **Only main** (or manual runs on main) can deploy via GitHub Pages;
PRs run verification without publishing unreviewed changes. Configure repository
Settings → Pages → **Source: GitHub Actions** once. The public URL after the
first successful main deployment is:

https://kieransimkin.github.io/FrameRudiments/

The site's 4 generated moments are **synthetic geometry test vectors**. Its
Fourier-band-shaped fixture directly creates `SpectrumBand` metadata and
synthetic signed samples; it is intentionally *not* packaged or claimed as a
StemLab acoustic measurement. Real analysis remains available via the saved
StemLab Fourier demo and [spectrum.md](spectrum.md). Reproduction:

```sh
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_advanced_renderers.py tests/test_visualisation_modulation.py
python scripts/generate_visualisation_gallery.py --output _site
```

## Research references

These public manuals and APIs informed the taxonomy (not copied source code):

- [Sonic Visualiser reference](https://sonicvisualiser.org/doc/reference/5.2.1/en/): spectrogram colour mappings, linear/log/mel axes, peak-bin frequency estimates and spectrum plot styles.
- [Audacity spectrogram settings](https://manual.audacityteam.org/man/spectrograms_preferences.html): waveform/spectrogram UI, multiple windows, reassignment and enhanced-autocorrelation pitch displays.
- [librosa display docs](https://librosa.org/doc/main/api/generated/librosa.display.specshow.html): STFT, mel, log, constant-Q, chromagram and frequency-axis taxonomy.
- [librosa display tutorial](https://librosa.org/doc/0.11.0/auto_examples/plot_display.html): true mel, CQT and chromagram analyses versus graphical coordinate changes.
- [FrameRudiments full-frame visualisers](visualisers.md): existing Butterchurn/projectM MilkDrop-style feedback, intentionally separate from per-Rudiment plots.
