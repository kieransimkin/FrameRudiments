# Annular visualisations, gradients and native Rudiment modulation

FrameRudiments expands from 41 to **101 named built-in visualisers**: **60
additional circular/annular modes** (29 spectra, eight annular spectrograms,
22 waveforms and one two-axis polar phase portrait). The independent rendering
contract, calibrated Fourier provenance, source-time sampling, strict style
validation and bounded observed history are unchanged.

The full catalogue, including historical source references and technical
non-goals, is in [Visualisation Atlas](visualisation-atlas.md). Every entry
below has its own stable `renderer` name and distinct drawing geometry.
There are also **12 colour/modulation test scenes** in the public gallery,
with 101 × 4 renderer snapshots and 12 × 4 style demonstrations.

## Circular and annular renderer catalogue

| Family | Name | Rendering approach |
| --- | --- | --- |
| spectrum | `spectrum-annular-bars` | Continuous radial frequency bars rooted at the inner ring |
| spectrum | `spectrum-annular-bars-inward` | Radial bars growing inward from the outer perimeter |
| spectrum | `spectrum-annular-bars-bipolar` | Centred, bidirectional radial magnitude bars |
| spectrum | `spectrum-annular-bars-mirror` | Bidirectional ring bars with endpoint markers |
| spectrum | `spectrum-annular-split` | Alternating inward/outward frequency bars |
| spectrum | `spectrum-annular-sunburst` | Alternating-length radial sunburst analyser |
| spectrum | `spectrum-annular-fan` | Filled wedge-frequency fan analyser |
| spectrum | `spectrum-annular-petals` | Thin sector petals sized by frequency-band strength |
| spectrum | `spectrum-annular-led` | Segmented radial LED meter from inner to outer ring |
| spectrum | `spectrum-annular-led-inward` | LED meter counting backwards from the outside |
| spectrum | `spectrum-annular-ticks` | Isolated radial magnitude-position ticks |
| spectrum | `spectrum-annular-arc` | Concentric miniature gauge arcs for each band |
| spectrum | `spectrum-annular-dial` | Graduated frequency dial with dot-end needles |
| spectrum | `spectrum-annular-dots` | Displaced radial frequency-bin dots |
| spectrum | `spectrum-annular-lollipop` | Radial bar stems with circular end markers |
| spectrum | `spectrum-annular-orbit` | Angularly perturbed frequency-bin orbit dots |
| spectrum | `spectrum-annular-caterpillar` | Thick circular bead chain driven by spectral bins |
| spectrum | `spectrum-annular-peaks` | Selected local maximum spectral spokes |
| spectrum | `spectrum-annular-area` | Closed annular frequency response with filled band |
| spectrum | `spectrum-annular-line` | Closed radial spectrum outline |
| spectrum | `spectrum-annular-ribbon` | Nested concentric polygon ribbons |
| spectrum | `spectrum-annular-halo` | Layered low-alpha spectral halo lines |
| spectrum | `spectrum-annular-rose` | Alternating polar rose-style magnitude petals |
| spectrum | `spectrum-annular-concentric` | Multilayered frequency rings |
| spectrum | `spectrum-annular-sectors` | Coloured pie sector bars per frequency bin |
| spectrum | `spectrum-annular-layers` | Interleaved bands partitioned over concentric rings |
| spectrum | `spectrum-annular-gauge` | Single annular swept arc for mean visual band intensity |
| spectrum | `spectrum-annular-spiral` | Frequency bins plotted on an amplitude-perturbed spiral |
| spectrum | `spectrum-annular-helix` | Frequency bins along a multiple-turn polar trace |
| spectrogram | `spectrogram-annular-time` | Annular spectrogram with time as radius and frequency as angle |
| spectrogram | `spectrogram-annular-rings` | Discrete concentric time-history rings coloured by band intensity |
| spectrogram | `spectrogram-annular-heatmap` | Polar annulus time-frequency heatmap |
| spectrogram | `spectrogram-annular-stripes` | Alternating tinted strips across the polar heatmap |
| spectrogram | `spectrogram-annular-contour` | Quantised spectral power annulus, band-level contour regions |
| spectrogram | `spectrogram-annular-frequency` | Time mapped to angle, frequency to radius |
| spectrogram | `spectrogram-annular-sweep` | Radar-style time sweep with old measurements faded |
| spectrogram | `spectrogram-annular-spiral` | Dominant-band spectral history along a time spiral |
| motion | `waveform-annular-line` | Signed native Rudiment history as a complete annular outline |
| motion | `waveform-annular-fill` | Signed annular waveform with fill to the centre ring |
| motion | `waveform-annular-area` | Filled area between motion trace and inner ring |
| motion | `waveform-annular-envelope` | Unsigned Rudiment channel amplitude around a circular baseline |
| motion | `waveform-annular-halo` | Translucent circular waveform aura |
| motion | `waveform-annular-rose` | Alternating-petal annular waveform |
| motion | `waveform-annular-pulse` | Per-point thickness pulse around a middle ring |
| motion | `waveform-annular-mirror` | Inside/outside mirrored radial waveform envelopes |
| motion | `waveform-annular-ribbon` | Annular ribbon between two native Rudiment axes |
| motion | `waveform-annular-rings` | Concentric independent motion channels |
| motion | `waveform-annular-bands` | Two-axis banded annular trace |
| motion | `waveform-annular-bars` | Bipolar motion-history spokes rooted at a centred circle |
| motion | `waveform-annular-spokes` | Circular discrete-time stem oscilloscope |
| motion | `waveform-annular-led` | Radial LED trail driven by absolute Rudiment channel value |
| motion | `waveform-annular-petals` | Sampled waveform sectors as radial petals |
| motion | `waveform-annular-sectors` | Sector length encodes absolute Rudiment motion magnitude |
| motion | `waveform-annular-arc` | Time-addressed arcs with per-sample radial position |
| motion | `waveform-annular-dots` | Circular sampler dots mapped to signed amplitude |
| motion | `waveform-annular-radar` | Radar-like radial motion traces with dot endpoints |
| motion | `waveform-annular-trails` | Chronologically fading dots along the annular waveform |
| motion | `waveform-annular-spiral` | Rudiment signal warped around a multi-turn time spiral |
| motion | `waveform-annular-helix` | Waveform on a spiralling annular helix |
| motion | `phase-annular-portrait` | Two native Rudiment dimensions mapped into annular angle and radius |


These are **polar projections of data**, not 60 different signal-analysis
algorithms. The annular spectrum and spectrogram views require the same pinned,
recoverable StemLab Fourier band recording as `spectrum`; the annular waveform
and `phase-annular-*` modes use selected **DanceRudiment motion values** and
observed frame history. Neither view produces PCM waveform measurements, true
stereo phase, new FFT bins, a pitch estimate, or a real mel filterbank. Smoothing
is visual interpolation over observations; it does not increase native sampling
resolution. `spectrogram-annular-*` can remap available recorded-band samples
into polar time/frequency geometry but cannot derive missing complex STFT data.

### Geometry parameters (annular renderer `style`)

All 60 circular views share validated numeric controls:

| Parameter | Default | Range / meaning |
| --- | --- | --- |
| `inner_radius`, `outer_radius` | 0.27 / 0.96 | 0..0.95 and 0.05..1 respectively, inner strictly less than outer. Units: fraction of usable viewport radius. |
| `start_angle` | 0 | -36000..36000 degrees; 0 maps to 12 o'clock. |
| `sweep_angle` | 360 | 5..360 degrees. Enables semicircles, arcs and full rings. |
| `clockwise`, `mirror` | true / false | Native angle direction and reflection preference (where mode uses it). |
| `line_width` | 3 | 1..64 pixels. |
| `bar_gap` | 0.16 | 0..0.9, fraction of each angular slot. |
| `bar_width` | 1 | 0.1..1, extra angular width multiplier. |
| `cap_radius` | 2.5 | 0.5..20 pixel endpoint markers. |
| `gain` | 1 | 0..8, rendering sensitivity only (never stored RMS). |
| `floor_db`, `ceiling_db` | -72 / 0 | -240..0 floor, ceiling above floor and ≤60, presentation only. |
| `fill_alpha` | 170 | 0..255. |
| `led_segments` | 16 | 2..64 radial LED segments. |
| `rings` | 4 | 1..12 concentric radial tracks. |
| `history_limit` | 40 | 4..256 sampled past frames for polar history geometry. |
| `axis` | 0 | Index of selected source Rudiment value dimension for waveform modes. |
| `twists` | 2 | 0.2..12 turns for spirals and helixes. |
| `frequency_scale` | `equal` | `equal`, `linear`, `log`, `mel`, `bark`, `erb`; **only remaps recorded frequency-band positions**. |
| `peak_threshold` | 0.18 | 0..1 display-level local maxima threshold. |
| `peak_count` | 16 | 1..64 selected band peaks. |
| `decay` | 0.65 | 0..1, reserved for history decay presentation. |
| `grid` | false | Optional three-circle guide overlay. |
| `colours`, `background`, `padding` | Core defaults | Supported by all views as before. |

A field can be accepted but only affect modes where its geometry is relevant.
For instance `led_segments` is active for LED variants, and `peak_threshold`
for the annular peak-selector.

## Shared colour pipeline: available to **all 101** built-ins

Add `visual` directly to *any* `type: "rudiment"` layer. This sits after the
renderer and before the layer's existing opacity and compositor, so it works
with native bars, particles, scopes, spectrograms, new annular views, or trusted
custom RGBA renderers. Existing projects without `visual` remain pixel-identical.

### Named and custom palettes

Built-in palette presets (usable as `visual.palette` and gradient `stops`):

- `aurora` — #062b45, #15c4b0, #f1dc84, #ec73e8.
- `neon` — #160b39, #00e5ff, #ff27bd, #fff2aa.
- `fire` — #180520, #a32740, #ff6827, #ffe9a2.
- `ice` — #09213c, #326bb6, #7adce9, #f4ffff.
- `sunset` — #261348, #c0427a, #f6a263, #fff4c5.
- `ocean` — #08192f, #145b88, #1cbeb0, #caf9de.
- `synthwave` — #101038, #993ce6, #fc468e, #f9ba6b.
- `copper` — #241919, #855039, #cb895f, #f0cc9a.
- `forest` — #101f2a, #187456, #70b16c, #e3f5b9.
- `mono` — #14202e, #556b80, #b1c9d1, #ffffff.
- `rainbow` — #ff4466, #ffbe45, #5be07d, #50aefb, #bc68f8, #ff4466.
- `heat` — #050414, #622090, #ec3b50, #ffa844, #fff7bc.
- `viridis-inspired` — #440154, #31688e, #35b779, #fde725.
- `magma-inspired` — #000004, #51127c, #b73779, #fc8961, #fcfdbf.
- `diverging` — #2171b5, #eeeeee, #d73027.
- `pastel` — #a8c8ff, #bfc0ef, #f6c7dc, #f9deb1.

Custom `palette` can be a list of 1..32 CSS colour strings (for RGB or RGBA).
`visual.palette` also overrides the renderer's existing discrete `colours` list,
providing one uniform mechanism for both older/newer renderers.

### Gradients

`visual.gradient` accepts `mode`: `horizontal`, `vertical`, `diagonal`,
`anti-diagonal`, `radial`, `angular`, `diamond`, `wave`, `checker`, `level`,
`frequency` (vertical layout alias), `time` (horizontal layout alias).
Use `radial` to colour every circular bar from its inside toward its endpoint,
`angular` to colour by position around a ring, `vertical` for traditional
columns, and `level` to map existing rendered brightness to a palette. This is
a **spatial recolour pass**, not a new audio or signal measurement.

- `stops`: named palette, list of 2..32 colour strings, or strictly ascending
  `{ "at": 0..1, "colour": "#rrggbb" }` objects covering positions 0 and 1.
- `space`: `rgb` or shortest-hue `hsv` interpolation.
- `blend`: `replace`, `tint` (brightness-preserving), `multiply`, `screen`,
  or `mix`. `mix` and `blend=replace` can intentionally override source hues.
- `wrap`: `clamp`, `repeat`, `mirror`; `reverse`: boolean.
- `cycles`: 0.01..32; `phase`: -100..100; `mix`: 0..1; `bands`: 0..256 to
  quantise into discrete LED/CRT-era colour steps.

### Universal numeric visual parameters

Every entry is eligible for another DanceRudiment's numeric modulator:

| Visual property | Minimum | Maximum | Default |
| --- | ---: | ---: | ---: |
| `visual.hue_shift` | -3600 | 3600 | 0 |
| `visual.saturation` | 0 | 4 | 1 |
| `visual.brightness` | 0 | 4 | 1 |
| `visual.contrast` | 0 | 4 | 1 |
| `visual.opacity` | 0 | 1 | 1 |
| `visual.gamma` | 0.1 | 5 | 1 |
| `visual.threshold` | 0 | 1 | 0 |
| `visual.posterize` | 0 | 16 | 0 |
| `visual.glow` | 0 | 24 | 0 |
| `visual.blur` | 0 | 12 | 0 |
| `visual.rotation` | -36000 | 36000 | 0 |
| `visual.scale` | 0.1 | 3 | 1 |
| `visual.scale_x` | 0.1 | 3 | 1 |
| `visual.scale_y` | 0.1 | 3 | 1 |
| `visual.offset_x` | -1 | 1 | 0 |
| `visual.offset_y` | -1 | 1 | 0 |
| `visual.gradient_phase` | -100 | 100 | 0 |
| `visual.gradient_mix` | 0 | 1 | 1 |
| `visual.gradient_cycles` | 0.01 | 32 | 1 |


`rotation`, `scale`, `scale_x`, `scale_y`, `offset_x` and `offset_y` transform
the viewport image without moving the layer bounds. `glow`/`blur` are bounded
Pillow image effects. `hue_shift`, `saturation`, `brightness`, `contrast`,
`gamma`, `opacity`, `threshold`, and `posterize` act on rendered ink (transparent
background stays transparent). `gradient_phase`, `gradient_mix` and
`gradient_cycles` control the gradient and have aliases `gradient.phase`,
`gradient.mix`, `gradient.cycles` in modulation rules. Perceptual interpretability
is best with monotonically ordered palettes; cyclic/rainbow hue is creative,
not an unambiguous scalar energy ruler.

## Modulation from an independent DanceRudiment

Add a **`modulations` array** (maximum 32) to a rudiment layer. Each rule has a
separate real `motion` route from DanceRudiments (named/table, pinned recording,
selected StemLab match, or existing composite). The driver shares the layer's
**exact encoded-frame timeline**. Recorded drivers retain their native source
clock; procedural drivers honour `repeat_clock` and continuous beat as they do
for sprites. No extra oscillator, resynthesised audio, future reads, or stateful
integration is inserted. Pin all referenced files with SHA-256, as usual.

```json
{
  "id": "radial-audio-and-native-motion",
  "type": "rudiment",
  "renderer": "spectrum-annular-bars",
  "bounds": [0, 0, 1080, 1080],
  "motion": {"kind": "recording", "file": {"path": "fourier.json", "sha256": "<exact SHA-256>"}},
  "history_frames": 256,
  "style": {
    "background": "#0b1323", "inner_radius": 0.32,
    "outer_radius": 0.94, "bar_gap": 0.2, "gain": 1.25,
    "start_angle": -90, "sweep_angle": 320
  },
  "visual": {
    "palette": "aurora",
    "gradient": {
      "mode": "radial", "stops": "neon", "space": "hsv",
      "wrap": "repeat", "blend": "replace", "cycles": 1.0
    },
    "glow": 3.0
  },
  "modulations": [
    {
      "id": "turn-the-ring", "motion": {"kind": "named", "pattern": "circle"},
      "axis": "x", "target": "style.start_angle", "range": [-120, 120],
      "curve": "smoothstep", "smoothing_frames": 4
    },
    {
      "id": "breathe-radius", "motion": {"kind": "named", "pattern": "bounce"},
      "axis": "y", "target": "style.inner_radius", "range": [0.18, 0.42],
      "curve": "sine"
    },
    {
      "id": "spin-colours", "motion": {"kind": "named", "pattern": "circle"},
      "axis": "z", "target": "gradient.phase", "range": [-0.4, 0.4]
    },
    {
      "id": "light-bloom", "motion": {"kind": "named", "pattern": "bounce"},
      "axis": "x", "target": "visual.glow", "range": [1, 10],
      "curve": "absolute", "lag_frames": 2
    }
  ]
}
```

**Each modulation rule accepts:**

| Field | Behaviour |
| --- | --- |
| `id` | Optional short unique identifier for the per-frame evidence. |
| `motion` | Full existing DanceRudiments motion config; may be a pinned file route. Required. |
| `axis` | Original dimension name (`x`, `y`, `z` or recorded name) or zero-based index (default 0). |
| `target` | `visual.<numeric>` or `style.<numeric>` (renderer parameter), with gradient aliases above. |
| `range` | Finite increasing `[minimum, maximum]` (mapped from native -1..+1 driver). |
| `curve` | `linear`, `smoothstep`, `ease-in`, `ease-out`, `sine`, `square`, `cubic`, `absolute`, `steps`, `threshold`, `triangle`, `bell`. |
| `steps` | 0 (continuous) or 2..128 quantisation steps. |
| `power` | 0.01..20 output curve exponent. |
| `deadzone` | 0..0.99 central signal deadband. |
| `invert` | Reverse modulation response. |
| `combine` | `set` (blend into parameter), `add`, `multiply`, `min`, or `max`. |
| `weight` | 0..1 strength of combination. |
| `lag_frames` | 0..600 encoded frames of delay. |
| `smoothing_frames` | 0..120 **past-only** frames included in a recency-weighted FIR average. |
| `sample_stride` | 1..60 frame interval across the observed FIR window. |

Relative `style.*` modulations (`add`, `multiply`, `min`, `max`) require an
explicit numeric base in `style` to avoid silently inferring renderer-specific
defaults. `set` can override any validated numeric renderer style; that
renderer then checks its own constraints. Integer renderer fields are rounded
(`line_width`, `rings`, `led_segments`, `history_limit`, etc.). When multiple
modulators write a target, they compose **in the order listed**. Bounds are
still enforced by the destination renderer and shared colour pipeline.

Any numeric renderer parameter can be modulated, including `count`, `radius`,
`amplitude`, `line_width`, `gap`, `contrast`, `history_limit`, `inner_radius`,
`outer_radius`, `bar_gap`, `start_angle`, `sweep_angle`, `gain`, `twists`,
`fill_alpha`, `led_segments`, `rings`, `peak_threshold` and `floor_db`.
Boolean, palette-list, and string enum parameters are *not* interpolated by
numeric modulation; set those in ordinary style instead. Modulating `floor_db`
and `ceiling_db` is a **visual scale movement**, not a new gain-calibration
measurement.

`RudimentLayer` collects modulation provenance and driver file hashes in its
manifest, and emits per-frame modulation values into `rudiment-analysis.jsonl`
(when the layer is active), alongside any true spectrum analysis report.
All smoothing references past encoded frames only. A random-access render of
frame 95 must match frame 95 after rendering frames 0–94 in order.

## Research and design choices

Historic and modern visualization systems inspired the renderer **geometry**,
not copied code or DSP:

- [audioMotion-analyzer](https://audiomotion.dev/): radial/inverted frequency
  bars, segmented LED and alpha/luminance meters, custom gradients, multiple
  frequency-band distributions, peak and mirror/reflection options.
- [MilkDrop custom waves/shapes](https://milkdrop.org/resources/preset-authoring):
  per-frame/per-point waveform and spectrum color/geometry equations, shape
  parameters; unlike GPU presets, these modes are deterministic Pillow plots.
- [Sonic Visualiser reference](https://sonicvisualiser.org/doc/reference/5.2.1/en/):
  spectrogram color maps and banded/continuous scales, frequency-axis mappings,
  true waveform measurement and peak-frequency estimation distinctions.
- [librosa specshow](https://librosa.org/doc/main/api/generated/librosa.display.specshow.html):
  time/frequency, pitch, mel, logarithmic and multiresolution display taxonomy;
  different plotted axes do not imply newly computed transform coefficients.
- [Matplotlib colour map design](https://matplotlib.org/stable/users/explain/colors/colormaps.html):
  sequential/diverging/cyclic colour semantics and perceptual luminance ordering.
- [Matplotlib colormap normalization](https://matplotlib.org/stable/tutorials/colors/colormapnorms.html):
  linear/log/power data-to-colour mapping. Here postprocessing is a display
  change, never an adjustment of persisted or reported RMS values.
- [Pipecat CircularWaveform](https://github.com/pipecat-ai/voice-ui-kit/blob/main/docs/content/docs/visualizers/circular-waveform.mdx):
  ring-ordered frequency bars, dynamic rotation, color gradients, varying bar
  counts and responsive annular composition.

## Test and publication

```sh
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_advanced_renderers.py tests/test_visualisation_modulation.py
python scripts/generate_visualisation_gallery.py --output _site
```

CI also checks Windows/Linux deterministic rendering through the standard
suite. The gallery is built from reproducible **synthetic** spectrum/motion
fixtures, with the independent *native* DanceRudiments patterns `circle` and
`bounce` acting as actual style modulation drivers. Its colour lab reports
numerical driver outputs in `catalogue.json`. The main-branch workflow is the
only one allowed to publish the gallery to GitHub Pages, after the site's
repository Pages settings permit deployment. It does **not** publish private
song masters or claim technical/perceptual approval.
