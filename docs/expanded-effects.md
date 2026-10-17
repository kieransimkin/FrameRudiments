# Expanded spectrum, spectrogram and Rudiment rendering effects

FrameRudiments now contains **161 named renderers**: the original 5, the first
36 modern/historical views, the 60 annular/polar displays, and **60 new effects**
for instrument-style spectrum plots, synthetic data-art spectrograms and
source-clocked Rudiment oscilloscopes. Every mode is a distinct explicit JSON
`renderer` identifier, not executable plugin code. The 60 new modes are
implemented in `effect_renderers.py` and enumerated in `effect_catalogue.py`.

## Complete new display index (60)

### Spectrum band visualisations (24)

| Renderer | Presentation |
| --- | --- |
| `spectrum-cityscape` | Magnitude-controlled skyline/rooflines with window strips |
| `spectrum-led-matrix` | Stacked LED bin columns with unlit cells |
| `spectrum-capsules` | Rounded-end frequency level columns |
| `spectrum-outline-blocks` | Hollow amplitude-outline bars |
| `spectrum-peak-ladder` | Thin horizontal level markers per frequency bin |
| `spectrum-staircase` | Connected stepped sample/hold levels |
| `spectrum-eq-curve` | Display-only interpolation of existing calibrated bins |
| `spectrum-ribbon-stack` | Faded observed historical magnitude traces |
| `spectrum-peak-trails` | Retained past-frame level markers |
| `spectrum-gel-bars` | Layered translucent height bars with glossy edge |
| `spectrum-lollipop-grid` | Stems ending in magnitude beads |
| `spectrum-mountain-range` | Overlaid filled ridges from sampled history |
| `spectrum-band-delta` | Signed adjacent-observation band-level difference |
| `spectrum-band-variance` | Observed history variance, display-only |
| `spectrum-energy-tiles` | Full-height energy-luminance tiles |
| `spectrum-semi-circle` | Half-circle spectral spoke analyser |
| `spectrum-fan-meter` | Filled half-annulus magnitude wedges |
| `spectrum-braided-lines` | Alternating-colour interwoven sampled histories |
| `spectrum-rain-bars` | Short falling magnitude bars across time |
| `spectrum-bead-stacks` | Dot stacks quantised into energy cells |
| `spectrum-dash-grid` | Segmented multi-row analyser strips |
| `spectrum-thermometer` | Level-to-opacity full-height bars |
| `spectrum-wave-fence` | Perspective history fence |
| `spectrum-partition` | Frequency column area partition based on band level |

All spectrum modes require exactly the existing `spectrum_bands()` validated,
pinned, un-clipped StemLab Fourier recording metadata, including shared source
hash, sample rate, FFT resolution and ascending band intervals. They render
windowed RMS dBFS from `analyse_spectrum`; no new FFT is performed. Display
interpolation, history differences and variance must **not** be interpreted as
true spectral centroid, flux, RMS true-peak, exact pitch or sub-frame samples.

### Time-frequency history and creative sonograms (18)

| Renderer | Presentation |
| --- | --- |
| `spectrogram-pixel-mosaic` | Hard rectangular sample tiles |
| `spectrogram-isometric-terrain` | Slanted ridges resembling an isometric relief |
| `spectrogram-voxel-city` | Height-coded perspective parallelogram cells |
| `spectrogram-frequency-stripes` | Alternating frequency-column strips |
| `spectrogram-dot-cloud` | Scaled circular level markers |
| `spectrogram-isobands` | Quantised discrete level bands |
| `spectrogram-negative` | Reversed display luminance |
| `spectrogram-tape` | Horizontal tape-style band lanes |
| `spectrogram-slit-scan` | Vertical sampled slit marks |
| `spectrogram-trails` | Slanted time-frequency traces |
| `spectrogram-hex-cells` | Hexagonal power-density mosaic |
| `spectrogram-bead-grid` | Brightness-coded bead matrix |
| `spectrogram-tunnel` | Nested historical rectangle outlines |
| `spectrogram-record-grooves` | Concentric arc grooves of sampled bands |
| `spectrogram-quilt` | Alternating colour blocks |
| `spectrogram-crosshatch` | Level-thresholded crossed line cells |
| `spectrogram-triangle-tiles` | Triangular tesselation |
| `spectrogram-pixel-rain` | Brightness-coded pixel streaks |

These visualisations map only the **observed, sampled StemLab band RMS
recording** onto different shapes. Isobands quantise band measurements; they
do not infer continuous spectral contours. Hex cells and perspective views
do not add resolution or new scientific data.

### Rudiment motion/oscilloscope displays (18)

| Renderer | Presentation |
| --- | --- |
| `waveform-phosphor` | Sample-age-weighted simulated CRT persistence |
| `waveform-echo` | Layered offset Rudiment history echoes |
| `waveform-sample-beads` | Discrete sample dots |
| `waveform-channel-braid` | Filled ribbon woven from two Rudiment dimensions |
| `waveform-pulse-train` | Vertical signed sample impulses |
| `waveform-quantised` | Discrete amplitude rungs |
| `waveform-triggered` | Shift to first observed rising zero crossing |
| `waveform-spiral-trace` | Past-valued amplitude around several spiral turns |
| `waveform-butterfly` | Mirrored dual-axis waveforms |
| `waveform-horizon` | Four offset translucent scope traces |
| `waveform-heat-ribbon` | Intensity-modulated, filled time history |
| `waveform-dotted-envelope` | Two symmetric sets of absolute-value dots |
| `waveform-vertical-scope` | Time progresses down, signal amplitude across |
| `waveform-phase-trails` | XY Rudiment scatter with past-sample decay |
| `waveform-polar-radar` | Radial motion history and spokes |
| `waveform-dna-helix` | Paired sign-mirrored traces linked by transverse rungs |
| `waveform-block-meter` | Filled signed sample-value columns |
| `waveform-signal-staircase` | Sampled zero-order-hold Rudiment trace |

These plot **native source-clocked Rudiment values**, not decoded PCM or a
stereo audio vectorscope. They do not compute physical phase, true audio
periods or signal-edge jitter. `waveform-triggered` aligns only to an
**already-observed** zero crossing; no future-audio-trigger prediction occurs.

## Controls shared by all 60 new modes

All retain existing `background`, `padding` and `colours`, and accept the
following strictly validated `style` controls. They are all opt-in; a JSON
project retains the same raster output until it selects another renderer.

| Property | Range / default | Effect |
| --- | --- | --- |
| `line_width` | integer 1..32 / 2 | Strokes, plot outlines |
| `floor_db`, `ceiling_db` | -240..0 / -72, floor+0.01..60 / 0 | Display clipping of calibrated bands only |
| `gain` | 0..8 / 1 | Visual amplification (no changes to evidence) |
| `history_limit` | 4..256 / 48 | Maximum observed frames included in history plot |
| `axis` | 0..255 / 0 | Selected Rudiment motion axis |
| `segments` | 2..64 / 14 | LEDs, colour bands and segmented displays |
| `quantize` | integer 0..64 / 0 | Optional visual amplitude discretisation |
| `gap` | 0..0.95 / .14 | Rectangular/LED cell gaps |
| `fill_alpha` | 0..255 / 175 | Drawn fill opacity |
| `decay` | 0..1 / .72 | Observed-history persistence fade |
| `bead_radius` | .5..20 / 3.5 | Size of cap/dot plots |
| `perspective` | 0..1 / .46 | Depth offsets in perspective views |
| `smoothing` | integer 0..16 / 0 | Display-only neighbouring-band spatial mean |
| `threshold` | 0..1 / .06 | Omit low-intensity display marks |
| `stride` | integer 1..16 / 1 | Subsample observed history |
| `amplitude_curve` | `linear`, `sqrt`, `square`, `log`, `ease` | Visual map, not energy calibration |
| `colour_map` | `axis`, `level`, `time`, `alternating`, `mono`, `rainbow` | Map existing CSS colours to marks |
| `mirror`, `reverse`, `grid`, `baseline` | booleans / false | Plot cosmetics |

Motion plot channels use selected `axes`, with `axis` referring to that
selection. The view validates a referenced axis at rendering time. Spectral
views additionally validate all `SpectrumBand` metadata through the native
`requires_spectrum` preflight path. Unknown style keys are rejected.

## Universal post effects: modulation-compatible for all 161 views

Every Rudiment layer already accepts a `visual` object, shared across old
and new renderers. This patch adds **13 numeric post-effect controls**, all
usable via `visual.*` DanceRudiment modulation targets. They are applied
*after* primary renderer geometry and palette gradient, leaving the underlying
StemLab band evidence unchanged.

| `visual` control | Range / default | Treatment |
| --- | --- | --- |
| `pixel_size` | 1..48 / 1 | Pixelated nearest-neighbour rendering |
| `scanlines` | 0..1 / 0 | CRT-like dark horizontal intervals |
| `scanline_spacing` | 2..24 / 4 | Scanline period in pixels |
| `grain` | 0..1 / 0 | Deterministic seeded film noise |
| `grain_seed` | 0..65535 / 0 | Stable grain seed; no hidden RNG state |
| `chromatic_shift` | 0..12 / 0 | Separate red and blue display channels |
| `vignette` | 0..1 / 0 | Radial edge darkening |
| `edge_mix` | 0..1 / 0 | Bright edges derived from the alpha mask |
| `dither` | 0..1 / 0 | Bayer-style ordered luminance dither |
| `halftone` | 0..1 / 0 | Printed-dot screen intensity modulation |
| `solarize` | 0..1 / 0 | Thresholded colour inversion mix |
| `glitch` | 0..1 / 0 | Stable horizontal channel displacements |
| `sharpen` | 0..4 / 0 | Unsharp-mask emphasis |

The default is pixel-identical to the original visual pipeline. Effects
preserve the original viewport dimensions and transparent alpha, and avoid
unbounded GPU/shader work. Modulate these parameters using the existing
`RudimentModulations` with per-source validation, weighted FIR smoothing over
**past** frame samples, declared ranges and deterministic random access.

### Example: independent native Rudiment controllers

```json
{
  "id": "coloured-cityscape",
  "type": "rudiment",
  "renderer": "spectrum-cityscape",
  "motion": {
    "kind": "recording",
    "file": {"path": "fourier.json", "sha256": "<exact recording sha256>"}
  },
  "bounds": [40, 220, 1030, 860],
  "history_frames": 240,
  "style": {
    "segments": 24,
    "amplitude_curve": "sqrt",
    "colour_map": "level",
    "gain": 1.4,
    "line_width": 3
  },
  "visual": {
    "palette": "aurora",
    "gradient": {"mode": "angular", "stops": "rainbow", "wrap": "repeat"},
    "scanlines": 0.2,
    "chromatic_shift": 2,
    "vignette": 0.3
  },
  "modulations": [
    {
      "id": "hue-from-circle",
      "motion": {"kind": "named", "pattern": "circle"},
      "axis": "x", "target": "visual.hue_shift", "range": [-120, 120]
    },
    {
      "id": "scanline-from-bounce",
      "motion": {"kind": "named", "pattern": "bounce"},
      "axis": "y", "target": "visual.scanlines", "range": [0.08, 0.85],
      "curve": "smoothstep"
    },
    {
      "id": "segment-count-from-circle",
      "motion": {"kind": "named", "pattern": "circle"},
      "axis": "z", "target": "style.segments", "range": [8, 30],
      "curve": "steps", "steps": 12
    }
  ]
}
```

The recording must use real pinned StemLab Fourier evidence; the public
preview page uses **invented synthetic signal values** and explicitly flags
that no real music analysis has been performed. Do not use the demo as
measurement evidence. Named motions preserve the native 64-pip DanceRudiments
sampling rate, the project's repeating/excerpt clock and source identity.

## Gallery, tests and deployment

The existing GitHub Pages workflow builds every registered view and four
sample times, then verifies their exact catalogue membership. The expansion
adds 8 post-effect/modulation examples, bringing the style lab to 20 examples.
On both `push` to main and PRs, the build checks a duplicate-free registry,
source identity, expected catalogue counts, deterministic pixels, style
validation, alpha preservation, and presence of every PNG in the gallery.
PRs never deploy; successful main builds publish after Pages is enabled.

```sh
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_advanced_renderers.py \
  tests/test_visualisation_modulation.py tests/test_effect_batch.py
python scripts/generate_visualisation_gallery.py --output _site
```

## References and scope

Taxonomy and user-facing effects draw from public manuals rather than copied
shader or analyser implementations:

- [Sonic Visualiser reference](https://www.sonicvisualiser.org/doc/reference/5.2.1/en/): lines, steps, frequency-bin blocks, magnitude colour maps, thresholding, banding and transparent sonograms.
- [audioMotion-analyzer](https://audiomotion.dev/): bar/LED outlines, radial/reflection variants, luminance response and gradients.
- [Friture features](https://friture.org/features.html): scrolling waterfall, spectrum and level displays.
- [Oscilloscope persistence displays](https://institutionofelectronics.ac.uk/oscilloscope-persistence-displays/): phosphor persistence, intensity coding and XY history plots.

**Not implemented as new DSP**: spectral phase, true stereo vectorscope,
constant-Q/wavelets, true-peak metering, LUFS, pitch, FFT bin reassignment,
true mel/ERB/gammatone filterbanks. These require appropriate upstream
analysis and/or PCM not available from Rudiment magnitude or motion samples.
