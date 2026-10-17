# Per-element and finished-frame post effects

FrameRudiments supports deterministic, independent post-render effects on **each
composited element**, followed by optional passes over **the finished frame**.
These use the same shared `VisualStyle` renderer as the 161 Rudiment views:
gradients, 16 colour palettes, hue/saturation/brightness/contrast, opacity,
blur/glow, geometry, CRT scanlines, dither, grain, halftone, chromatic shift,
solarise, pixelation, sharpening and the other validated options. Every numeric
VisualStyle parameter is addressable by a separate native DanceRudiment source.

No effect stage re-analyses PCM, spectrum bands, or movement. Source/motion
provenance and the native 64-pip clock are preserved. Each frame can be rendered
out of order without hidden smoothing state or future motion samples.

## Example: different effects on individual elements and final frame

Add this object to a `framerudiments.project.v1` project:

```json
{
  "post_effects": {
    "elements": {
      "layer:lead-dancer": [
        {
          "id": "neon-outline",
          "visual": {"glow": 4, "gradient": {"mode": "angular", "stops": "rainbow", "blend": "replace"}},
          "modulations": [
            {"id": "glow-bounce", "motion": {"kind": "named", "pattern": "bounce"},
             "axis": "y", "target": "visual.glow", "range": [1, 12], "smoothing_frames": 6},
            {"id": "palette-turn", "motion": {"kind": "named", "pattern": "circle"},
             "axis": "x", "target": "visual.gradient.stops",
             "choices": ["rainbow", "ice", "fire", "aurora"]}
          ]
        }
      ],
      "layer:spectrum": [
        {"id": "digital-screen", "visual": {"scanlines": 0.6, "grain": 0.08, "pixel_size": 2}}
      ],
      "caption": [
        {"id": "warm-letters", "visual": {"glow": 2, "hue_shift": 20}}
      ]
    },
    "frame": [
      {
        "id": "master-grade",
        "visual": {"vignette": 0.45, "contrast": 1.12},
        "modulations": [
          {"id": "master-pulse", "motion": {"kind": "named", "pattern": "circle"},
           "axis": "z", "target": "visual.vignette", "range": [0.1, 0.65]}
        ]
      }
    ]
  }
}
```

The `motion` object is the existing DanceRudiments route: `named`, `table`,
`match`, `recording`, or `composite`, using the same native clock and pinned
input requirements as other motion uses. Supply actual recording hashes rather
than placeholder SHA-256s. `source`/`continuous` repeat-clock policies remain
in force; smoothing samples earlier *encoded frames* only.

## Element targeting

Use `post_effects.elements` to map exact compositor element IDs to ordered
stage arrays. Entries are validated at project preparation; unknown targets
fail instead of being silently ignored.

| Target ID | Element |
|---|---|
| `background` | The composed background colour/image |
| `scene` | Trusted Python scene hook (when supplied) |
| `layer:ID` | Any sprite, atlas, legacy recording-bars or Rudiment layer with this ID |
| `identity:0`, `identity:1`, etc. | Individual identity title/text blocks, in declared order |
| `caption` | Timed captions/text, when the project has a caption block |
| `lyric-art:0`, `lyric-art:1`, etc. | Individual lyric artwork items in declared order |

`post_effects.all_elements` is a pipeline applied **separately to each
existing element**, before any element-specific stages. It does **not** apply
once to the whole picture. This makes it practical to give every element a
common CRT style without blurring or chromatically separating the *already
composited* frame.

`post_effects.frame` is an ordered pipeline applied **only after** background,
scene, layers, identities and caption/lyric art have been composited. You can
combine both scopes. All Pillow post effects run before the existing top-level
`effects` MilkDrop/projectM GPU feedback engines, which retain their separately
pinned runtime and sequential playback contract.

If a trusted `scene` hook is specifically targeted or `all_elements` is used,
the hook receives a transparent RGBA canvas so the scene can be isolated from
the background. An untargeted scene hook receives the usual background-filled
canvas, preserving the old API. Hooks that sample existing background pixels
should not opt into isolated scene effects without adaptation.

## Stage controls

Every stage has an `id` (unique within its pipeline), optional `visual`,
`modulations`, `coordinate_space` and `padding`. There may be up to 16 ordered
stages per element or frame pipeline, 256 named targets, and 32 native
modulators per stage.

- `visual` accepts exactly the settings supported by `VisualStyle`, including
  all 13 additional post effects in the expanded-effects batch.
- `modulations` uses the same numeric transfer curves, ranges, smoothing,
  lag/stride, `set`/`add`/`multiply`/`min`/`max` combinations and native
  motion identity as a Rudiment layer. Target numeric properties such as
  `visual.glow`, `visual.pixel_size`, `visual.scanlines`, `visual.opacity`,
  `visual.chromatic_shift`, `visual.gradient_mix` and `gradient.phase`.
- Categorical fields use `choices`: `visual.palette`, `visual.gradient`, or
  `visual.gradient.mode`, `.stops`, `.space`, `.wrap`, `.reverse`, `.blend`,
  `.bands`, `.phase`, `.mix`, `.cycles`. Native driver values select the
  nearest alternative, so strings and colour-stop arrays are never assigned
  fractional values. Every choice is validated at project preparation.
- `coordinate_space` can be `element` (default, gradient/rotation centred
  on a padded alpha-bounding rectangle) or `frame` (full output coordinates).
  Frame stages always use full output coordinates.
- `padding` is a fixed 0–256-pixel halo around the element's alpha bounding
  box, default 48. Choose a larger value for wide glows, scaling and offsets
  to avoid cropping the transformed pixels. Both stage-level settings can
  also be selected by DanceRudiment: `stage.padding` accepts a numeric range,
  while `stage.coordinate_space` accepts `choices: ["element", "frame"]`.

Stages operate on independent RGBA images, alpha-composited afterwards in
project order. A stage cannot colour a neighbouring element *unless it itself
explicitly adds ink across that neighbour's pixel area*. The frame stage can
colour everything. Final-frame opacity or transforms that introduce transparent
pixels are flattened against a deterministic black matte before RGB encoding. Output is clipped to the encoded frame. This opt-in
post-processing can extend alpha beyond an individual layer's authored bounds;
the frame manifest records the actual transformed alpha extent.

## Evidence, verification and gallery

Stage configuration and pinned native drivers appear under
`composition.post_effects` in the manifest. Observed frame-specific numeric
and categorical modulation results appear under `post_effects` in the
`rudiment-analysis.jsonl` stream. Absence of `post_effects` retains the previous
per-frame compositing route and its original pixels.

Run:

```sh
python -m pytest -q tests/test_scoped_effects.py
python scripts/generate_visualisation_gallery.py --output _site
python scripts/generate_post_effect_gallery.py --output _site
```

The GitHub Pages workflow includes a linked **Scopes** test page with six
synthetic scenes, four deterministic sample moments each, and the exact
configuration used. These are geometry and pipeline tests, not perceptual
approval or claims about actual song acoustics.
