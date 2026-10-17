# High-quality anti-aliasing and motion blur

FrameRudiments adds **opt-in, deterministic edge-aware anti-aliasing**, **subpixel directional motion blur** and a distinct **encoded-frame temporal shutter** to the existing `visual` / `post_effects` pipeline. Their parameters can be modulated by independent native DanceRudiments, using the source clock and bounded history already documented in [scoped post effects](scoped-post-effects.md). Existing projects remain byte-for-byte unchanged unless the new effects are enabled.

## Three different effects, not interchangeable methods

| Method | Where | What it does | What it does **not** do |
| --- | --- | --- | --- |
| `visual.antialias` | Rudiment renderer `visual`, individual `post_effects.elements`, `all_elements`, or `frame` stages | Detects hard luminance/alpha edges, applies a small Gaussian smooth in premultiplied RGBA and mixes only along those edges | Does not reconstruct vector geometry from true high-resolution rasterisation, MSAA or SSAA. Do not enable it for crisp nearest-neighbour pixel art. |
| `visual.motion_blur` | Same `visual`/stage locations | Integrates bicubic-subpixel translations of an existing RGBA image along a chosen angle, in premultiplied alpha | Does not estimate object velocity or infer missing optical flow. It is a directional *motion-streak approximation* and can blur unmoving objects. |
| `post_effects.temporal` | Whole composed frame, before Butterchurn/projectM | Integrates **actual earlier encoded frames** with a bounded, configurable shutter window in linear-light RGB, stopping at a repeated-source boundary | Does not interpolate subframes, estimate optical flow, or see future frames. A 5-frame shutter is a historical exposure, not 5 samples of one instantaneous pose. |

**Premultiplied alpha** ensures isolated sprites do not pick up dark halos as they move or soften. The temporal compositor blends gamma-correct (linear-light) RGB using deterministic integer LUTs. NumPy is used when installed for an equivalent faster computation, with a pure-Python fallback requiring no additional dependencies. The temporal effect is deliberately bounded to a maximum of eight historical frames and nine sampled frames including the current one; a long shutter can be expensive and visually inappropriate for text or sharp changes.

## Example: local antialiasing and native-driven streaks

```json
{
  "post_effects": {
    "elements": {
      "layer:orbit": [{
        "id": "sprite-quality",
        "padding": 48,
        "visual": {
          "antialias": 0.9,
          "antialias_radius": 0.8,
          "antialias_threshold": 0.06,
          "motion_blur": 0.65,
          "motion_blur_distance": 12,
          "motion_blur_samples": 12,
          "motion_blur_angle": 20,
          "motion_blur_trail": 1
        },
        "modulations": [
          {"id": "angle", "motion": {"kind": "named", "pattern": "circle"},
           "axis": "x", "target": "visual.motion_blur_angle", "range": [-120, 120]},
          {"id": "length", "motion": {"kind": "named", "pattern": "bounce"},
           "axis": "y", "target": "visual.motion_blur_distance", "range": [0, 36]},
          {"id": "edge", "motion": {"kind": "named", "pattern": "circle"},
           "axis": "z", "target": "visual.antialias", "range": [0.3, 1]}
        ]
      }]
    }
  }
}
```

The `layer:orbit` element is affected independently: the background, other sprites, captions and remaining visualisation layers are not treated unless targeted by `all_elements` or `frame`. Use the same `visual` fields directly on `type: "rudiment"` layers to apply quality effects before the later compositing stages.

## Example: true history-based shutter

```json
{
  "post_effects": {
    "temporal": {
      "strength": 0.78,
      "shutter_frames": 3,
      "samples": 4,
      "falloff": 1,
      "modulations": [
        {"id": "exposure", "motion": {"kind": "named", "pattern": "circle"},
         "axis": "x", "target": "temporal.strength", "range": [0.1, 0.9]},
        {"id": "shutter", "motion": {"kind": "named", "pattern": "bounce"},
         "axis": "y", "target": "temporal.shutter_frames", "range": [1, 6]},
        {"id": "samples", "motion": {"kind": "named", "pattern": "circle"},
         "axis": "z", "target": "temporal.samples", "range": [2, 7]}
      ]
    }
  }
}
```

`post_effects.temporal` is only legal as a **final-frame** operation, never inside `elements`, `all_elements` or `frame` stage `visual`. The final frame is recomposed for each required historical sample, including its ordinary post-effects, **before** the optional sequential full-frame MilkDrop engine; historical frames never advance or reuse MilkDrop feedback. This keeps video seeking deterministic and prevents a separate simulated state from diverging after random-access frame renders. The `post_effects.temporal` stage is represented in the encoded frame's `post_effects` report and in project evidence, including source frame indices, native modulation and shutter weights.

Within a repeated excerpt, sampled history is limited to the present repeat. At startup the shutter is shorter rather than generating nonexistent negative frames. Text, artwork and backgrounds will smear with true full-frame temporal blur; apply directional element blur instead if you need sharp lyrics or captions.

## Configuration reference

Numeric fields below all accept native DanceRudiment modulation in the usual range-based `modulations` list of a scoped stage. Stage `visual` numeric values are validated against these bounds. For `post_effects.temporal`, prefix targets with `temporal.` instead.

| Setting | Default | Valid range | Meaning |
| --- | --- | --- | --- |
| `visual.antialias` | 0 | 0–1 | Adaptive AA blend fraction; zero disables |
| `visual.antialias_radius` | 0.75 | 0.25–3 px | Width of local softening along detected edges |
| `visual.antialias_threshold` | 0.08 | 0–0.95 | Edge contrast cutoff |
| `visual.antialias_passes` | 1 | 1–3 | Bounded edge-aware passes (rounded to integer after modulation) |
| `visual.motion_blur` | 0 | 0–1 | Directional streak strength |
| `visual.motion_blur_distance` | 0 | 0–96 px | Total shutter travel across the image |
| `visual.motion_blur_angle` | 0 | −360–360 degrees | Directional blur orientation |
| `visual.motion_blur_samples` | 8 | 2–24 | Subpixel offset samples (rounded to integer) |
| `visual.motion_blur_trail` | 1 | 0–1 | 0 = centred, 1 = backward trail |
| `visual.motion_blur_falloff` | 1 | 0–8 | Weight decay along streak |
| `temporal.strength` | 0.8 | 0–1 | Mix of historical shutter average into current image |
| `temporal.shutter_frames` | 3 | 0–8 frames | Lookback extent in encoded frames |
| `temporal.samples` | 4 | 2–9 | Maximum number of observed samples including present (rounded) |
| `temporal.falloff` | 1 | 0–8 | Weight decay of older frames |

## Example gallery and verification

The CI-generated [anti-aliasing and motion-blur gallery](https://kieransimkin.github.io/FrameRudiments/quality/) includes nine configurations, sampled at four moments, with **four visualiser modes in every panel**: annular spectrum, phosphor oscilloscope, LED spectrum and motion ribbon. The page shows original frames, AA, directional streaks, temporal history, stacked effects and DanceRudiment-modulated quality settings, alongside a machine-readable `catalogue.json` with exact parameters.

No private music, model downloads or false acoustic analysis are required; all gallery spectrum and motion inputs are synthetic test fixtures. CI validates geometry, alpha handling, deterministic seeking, repeated excerpt behaviour, modulation and generated asset counts before Pages deploys. Full-frame rendering and the expensive multi-frame shutter remain explicitly opt-in.
