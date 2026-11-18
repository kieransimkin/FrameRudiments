# M11.3 — Frame-addressed motion echoes, directional blur, slit scan and time remapping

This milestone adds **native, explicitly bounded, stateless temporal effects** to
FrameRudiments, between M11.1's composed native RGB8 frames and M11.2's optional
delayed-feedback recurrence. It does not modify the source clock, audio,
DanceRudiment motion engine, same-frame graph, or opaque Butterchurn/projectM
state. The existing **spatial** `visual.motion_blur` continues to work; the
M11.3 `directional_blur` samples *real completed native frames* and applies an
optional authored translation to those observed pixels. No optical flow,
artificial frames, motion estimation or temporal model is implied.

This is an **unreleased implementation candidate**. Full repository Linux and
Windows CI, physical-device/driver tests, browser painting and perceptual
approval must be recorded separately from isolated kernel qualification.

## Project configuration

Declare one or more effects in `post_effects.temporal_fx`. They execute in
fixed order: time remap (source frame selection), motion echoes, temporal
directional blur, slit scan, then optional M11.2 `post_effects.feedback` and
final antialiasing. The legacy `post_effects.temporal` historical shutter is
part of each native sampled source and therefore runs *before* the M11.3 stage.

```json
{
  "post_effects": {
    "temporal_fx": {
      "boundary": "repeat",
      "time_remap": {
        "output_start": 12,
        "output_end": 48,
        "source_start": 18,
        "rate": [1, 2],
        "interpolation": "linear"
      },
      "motion_echoes": {
        "count": 3,
        "delay_frames": 2,
        "decay": 0.65,
        "strength": 0.55,
        "shift_x": 3,
        "shift_y": -1
      },
      "directional_blur": {
        "samples": 5,
        "history_frames": 4,
        "angle_degrees": 15,
        "distance_px": 12,
        "falloff": 1,
        "strength": 0.45
      },
      "slit_scan": {
        "axis": "x",
        "strips": 12,
        "history_frames": 8,
        "reverse": false,
        "strength": 0.85
      }
    }
  }
}
```

All four effects are optional individually; at least one must be specified.
There are no implicit extra media inputs. `boundary` is `repeat` (default) or
`programme`. `repeat` prevents historical taps and remapped samples from
accessing an earlier or later exact source cycle. For a fractional 49/10-frame
cycle, the first valid source indices are 0, 5, 10, 15 and 20; a missing past
tap is omitted, **not silently replaced by the preceding repeat**. `programme`
allows explicit sampling across those boundaries.

### Exact parameters

| Effect | Parameters, limits and semantics |
| --- | --- |
| `time_remap` | Required `output_end` (exclusive) and optional `output_start` (default 0), `source_start` (default output start), signed `rate: [numerator, denominator]` (−32…32 over 1…32) and `interpolation: linear\|nearest`. Source position = source start + rate × (output frame − output start). Outside the authored output interval it is an identity. Zero rate holds; negative rate reverses. Half-speed uses two real source frames in linear-light RGB when fractional. Boundary policy always clips sampled coordinates rather than wrapping or reading missing inputs. |
| `motion_echoes` | `count` 1–12, `delay_frames` 1–32, total past window ≤32 frames; `decay` 0–1, `strength` 0–1, `shift_x/y` each −96…96 px. The original native source is blended with decayed real historical echoes; shifts are pixel translations of observed images, not movement synthesis. |
| `directional_blur` | `samples` 2–12, `history_frames` 1–32, `angle_degrees` −360…360, `distance_px` 0–96, `falloff` 0–8, `strength` 0–1. Real sampled historical frames form a directional exposure, mixed with the current output in linear light. No guessed future frames. |
| `slit_scan` | `axis` x/y, `strips` 2–24, `history_frames` 1–32, `reverse` boolean and `strength` 0–1. Each spatial strip copies the corresponding region from an observed source frame at a declared lookback. `strength` linearly blends the scanned mosaic with the preceding stage. |

### Native DanceRudiment controls

Up to three independent source-clocked numeric drivers can modulate
`temporal_fx.motion_echoes.strength`,
`temporal_fx.directional_blur.strength` and
`temporal_fx.slit_scan.strength`, but only if that effect is configured. The
existing native `motion`, `axis`, `range`, `combine`, `weight` and input-pin
contracts apply; the effective value is clamped to 0–1. Native motion does not
change the tap schedule, so the resource bound is stable across modulations.

```json
"modulations": [{
  "target": "temporal_fx.motion_echoes.strength",
  "motion": {"kind": "named", "pattern": "circle", "repeat_clock": "source"},
  "axis": "x", "range": [0, 0.8], "combine": "set"
}]
```

This is an excerpt from inside the `temporal_fx` object, not a new top-level
modulator registry. Unknown fields, nonfinite values, negative resource
limits, invalid interpolation, duplicate drivers and booleans supplied as
integers are rejected at `Compositor` construction.

## Source read and output order

Each output frame N receives a pure `TemporalFXPlan` documenting all resolved
roles, exact rational interpolation weights, deduplicated integer source
indices and allocated RGB budget. Each source index is independently evaluated
through the existing M11.1 `render_scheduled_frame`—including configured
subframes and historical shutter—**without invoking M11.2 feedback or this
M11.3 stage recursively**. The target frame is evaluated last to restore its
original extents, captions, scene reports and isolated route surfaces. Its
source/beat/audio context is authoritative even when visible pixels are
remapped. Final AA runs once, after the optional feedback recurrence.

- No mutable temporal effect history or cross-frame cache is kept. Out-of-order
  rendering is equivalent to sequential rendering for identical pinned inputs
  and dependencies.
- Source-only feedback state is owned by M11.2; restoring its validated
  checkpoint reconstructs the **post-M11.3** input for each replayed frame.
  Any change to M11.3 settings invalidates feedback checkpoint identity.
- Preflight checks every nested native schedule before raster evaluation.
  At most **48 unique native frame taps** and **256 MiB** estimated retained RGB
  source/output memory are admitted per M11.3 output. The estimate excludes
  M11.1's separately budgeted subframes, renderer caches and GPU driver memory.
  A feedback seek permits at most **4,096** preflighted M11.3 frame callbacks.
- `temporal_read_bounds` includes the maximum declared lookback and remap
  displacement for S5/S6 cache invalidation. Remap windows require an explicit
  exclusive end so that the source reach is known before rendering.
- Interrupted or failed requests roll back the native compositor's metadata,
  discard detached input images and close any failed virtual sampler. Input
  identity guards remain owned by S6/M11.2. Cancellation is cooperative between
  native callbacks; it does not preempt a native raster call mid-flight.

## Inspection and programmatic access

`Compositor.temporal_fx_contract()` reports enabled families, resource limits
and conservative read bounds. `Compositor.temporal_fx_plan(frame)` returns a
content-free JSON plan; it does not open GPU contexts or render pixels. The
existing `temporal_plan(frame)` still describes each individual M11.1 native
sampling task. Read-only `framerudiments inspect-temporal` emits both plans,
plus feedback reconstruction if configured.

The report contains actual tap indices, stage names and native modulation
receipts but no private original pixel data. `Compositor.evidence()` adds
`temporal_fx.last_execution` for the completed output, including number of
actual M11.1 frame callbacks; the original native-scheduling receipt still
refers to the final requested source frame.

## Qualification and limits

Local deterministic tests:

```bash
python -m pytest -q tests/test_temporal_fx_m11.py tests/test_compositor_temporal_fx.py
python -m pytest -q tests
ruff check .
```

The M11.3 CI qualification should generate independent native source-frame
references, run sequential and shuffled queries, compare every decoded pixel
(or distinguish lossy codec tolerances), and publish side-by-side images,
manifest, source/clock hashes, CLI commands and environment identity. Native
EGL/physical-device and external Butterchurn/projectM acceptance remain
separate. M11.4, not M11.3, owns general-purpose rational-timebase video I/O.

**Known scope:** All four effects consume the already-composited RGB8 output;
per-element velocity/depth and scene-linear HDR aren't available. Retime
changes *visible content* but not soundtrack or cues. An explicitly authored
pixel shift can smear edges; it cannot reconstruct missing object motion.
Large samples at 4K/8K may fail the declared memory gate rather than silently
reduce fidelity. Time remapping is not currently an editor-side drag gesture;
author it in JSON or use the native parameter surface and inspect its plan.
