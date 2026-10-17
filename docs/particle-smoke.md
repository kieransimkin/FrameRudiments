# Deterministic particle and smoke emitters

FrameRudiments supports an opt-in `type: "emitter"` composited layer. It uses
frame-addressed, seeded particles, so seeking/reordering frames is reproducible;
there is no mutable particle simulation state or a duplicated movement engine.
The renderer depicts **stylised** smoke and particles, not fluid dynamics,
acoustic evidence, collision physics or a volumetric simulation.

The layer works with the existing independent-element `post_effects` pipelines
(`layer:<id>`), quality effects, sprite/canvas composition and the optional
full-frame Butterchurn/projectM chain. Emitter frame reports enter the normal
`rudiment-analysis.jsonl` stream. All source files, including optional custom
transparent PNG particles and cue files, must be SHA-256 pinned. No external
images are downloaded at render time.

## Continuous smoke

```json
{
  "type": "emitter",
  "id": "stage-haze",
  "kind": "smoke",
  "bounds": [30, 200, 1050, 1800],
  "emission": {"mode": "continuous"},
  "style": {
    "seed": 12, "rate": 25, "lifetime": 3.2,
    "size": 18, "size_growth": 0.9, "speed": 0.11,
    "direction": -90, "spread": 50, "turbulence": 0.16,
    "colour": "#9bc6d2", "secondary_colour": "#d5deea", "opacity": 0.45
  },
  "modulations": [
    {"id": "wind", "motion": {"kind": "named", "pattern": "circle"},
     "axis": "x", "target": "style.wind", "range": [-0.25, 0.25]},
    {"id": "puffs", "motion": {"kind": "named", "pattern": "bounce"},
     "axis": "y", "target": "style.rate", "range": [6, 38]},
    {"id": "colour", "motion": {"kind": "named", "pattern": "circle"},
     "axis": "z", "target": "style.hue", "range": [-70, 70]}
  ]
}
```

`kind` can be `particles`, `smoke`, `sparks`, `embers`, `confetti`, `snow`,
`rain`, `dust` or `fireflies`. Each has a sensible shape/motion preset that
can be overridden with `style`. `shape` supports `soft`, `disc`, `square`,
`star` and `streak`. `blend` supports `alpha` or additive `add`. `particle_sprite`
can be a pinned RGBA PNG object; it is rendered into the tile shape and may be
combined with native post effects. An unpinned file path fails validation.

## Cue-triggered smoke or sparks

The `cue_file` may be one of:

* **DanceMoves section LRC**, e.g. `[01:44.01][SECTION: DROP 1]` or
  `[00:30.00][DROP: CHORUS]`. StemLab `generate-cues` / `snap-cues` output
  `.lrc` files use the same time-label convention.
* **DanceMoves cue timeline JSON**, with `cues` or `events` entries containing
  `time` (source seconds), optional `id`, `name` and `type`, such as
  `{"cues": [{"id": "burst-1", "type": "DROP", "time": 4.2}]}`.
* **StemLab `stemlab.cue-snap.v1` and `stemlab.cue-generate.v1` JSON reports**,
  taking the adjusted `output_seconds_exact` when available, otherwise
  `output_seconds`. This does not silently use the original, unsnapped timing.
* **CD `.cue` sheet**, with `TRACK`, `TITLE` and `INDEX 01 mm:ss:ff` at 75 frames
  per second.

Example excerpt:

```text
[00:01.25][SECTION: BUILD]
[00:02.50][DROP: FIRST]
[00:03.75][DROP: FINAL]
```

Example layer, assuming the cue file's SHA-256 has been calculated:

```json
{
  "type": "emitter",
  "id": "drop-bursts",
  "kind": "sparks",
  "bounds": [0, 0, 1080, 1920],
  "emission": {
    "mode": "cue",
    "cue_file": {"path": "sections.lrc", "sha256": "<actual SHA-256>"},
    "labels": ["DROP*"],
    "burst_count": 100
  },
  "style": {
    "seed": 7, "shape": "streak", "colour": "#ffe3a6",
    "emitter_x": 0.5, "emitter_y": 0.8,
    "direction": -90, "spread": 220,
    "speed": 0.5, "gravity": 0.24, "lifetime": 1.5,
    "trail": 2, "opacity": 0.9
  }
}
```

Use `"mode": "hybrid"` to combine continuous particles with filtered cue
bursts. Labels are case-insensitive glob patterns, matching the original
bracketed label, the inner name, or `TYPE: NAME` (e.g. `DROP*`, `SECTION: DROP*`,
`[DROP: FINAL]`). Empty `labels` means all cues. Duplicate timed labels are
independent cue occurrences. A cue file alone **does not** establish that its
source is acoustically confirmed or matches the selected master: select and
pin the correct file, and review the association. If the StemLab JSON provides
a source-audio SHA-256 it must match the project's pinned master or loading
fails.

### Timing and seeking

Cue timestamps are **source-audio seconds**, not sequential output time.
Only events inside the exact source excerpt `[start_sample, end_sample)` are
scheduled. The encoder's nearest-frame, half-up policy is used for the output
trigger frame, and events are scheduled again within each `repeats` cycle.
The birth time and each seed are tied to that output frame, so render order,
seek and processing threads cannot invent or duplicate an event. Continuous
particles use the native per-frame source beat to sample their modulators;
`repeat_clock: "source"` and `"continuous"` retain their established meanings.
`start_seconds` and `end_seconds` filter births by source seconds; particles
born before the gate closes may continue fading afterwards. There is no
backwards integration across an unrelated audio source boundary.

## Numeric parameters and DanceRudiment modulation

Every numeric `style.*` parameter below supports the existing native
`modulations` array (up to 32 independent modulators). Existing `visual.*`
modulation supports gradient/colour grading and other post effects after
particle rasterisation. Values are verified against their permitted ranges
on every frame; unknown targets and non-finite/unsafe values fail closed.

| Parameter | Valid range | Interpretation |
| --- | --- | --- |
| `rate` | 0–1200 | Continuous births per second |
| `burst_count` | 0–512 | Births per selected cue |
| `lifetime` | 0.05–12 | Seconds before expiration |
| `size`, `size_growth`, `stretch` | 0.5–96, -3–8, 0.2–8 | Particle size, expansion and aspect |
| `speed`, `speed_variance` | 0–4, 0–1 | Initial speed and random variation |
| `direction`, `spread` | -360–360°, 0–360° | Velocity angle and emission cone |
| `gravity`, `wind`, `drag` | -3–3, -3–3, 0–12 | Ballistic forces and damping |
| `turbulence`, `turbulence_frequency` | 0–2, 0.1–16 | Bounded procedural curl |
| `emitter_x`, `emitter_y` | 0–1 | Viewport-relative origin |
| `emitter_width`, `emitter_height` | 0–1 | Random spatial spread around origin |
| `opacity`, `fade_in`, `fade_out` | 0–1 | Alpha and lifetime ramps |
| `hue` | -360–360° | RGB-to-HSV hue rotation |
| `saturation`, `brightness` | 0–3 | Colour balance and luminance |
| `rotation`, `spin` | -360–360°, -1440–1440°/s | Sprite orientation/rotation rate |
| `softness`, `trail` | 0–1, 0–5 | Shape falloff and trailing copies |
| `pulse_frequency`, `pulse_amount` | 0–24 Hz, 0–1 | Particle-level brightness pulse |

Static, nonnumeric settings are `shape`, `colour`, `secondary_colour`, `blend`
and `seed`. They are validated on load but not treated as numeric modulation
targets. For a modulated hue or palette-like variation, use `style.hue`,
`style.saturation`, `style.brightness` or a surrounding scoped `post_effects`
stage with its existing categorical-choice support. Do not modulate random
seeds per frame when repeatability matters.

## Safety, resource use and provenance

The engine reconstructs only particles born within a bounded lifetime window.
Particle lifetimes are at most 12 seconds, with at most 128 continuous births
per encoded frame and at most 2,000 active particles drawn per layer. Excess
survivors are counted as dropped in frame evidence. Cue files are limited to
2 MiB and 20,000 timestamp occurrences. Use reasonable `rate`/`burst_count`
values for high-resolution renders; the engine deliberately does not download
models, execute cue-file scripts or perform an audio transform.

The `rudiment-analysis.jsonl` evidence identifies cue bursts, active particles,
caps/dropped survivors and the applied native modulation for the frame.
Prepared manifests also identify the pinned cue/source sprite and the
scheduled event count. Use small bounded clips in CI before rendering a
full song. The static CI gallery at `/particles/` shows 12 synthetic modes
with four seekable moments, including native DanceRudiment-driven wind and
cue-triggered smoke/sparks. The gallery is synthetic preview geometry, not a
claim of real smoke dynamics or music-analysis accuracy.
