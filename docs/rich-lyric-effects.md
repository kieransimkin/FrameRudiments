# Rich lyric typography and word-following sprite effects

FrameRudiments can use its **existing** per-line `lyrics` and per-word `word_lyrics`
tracks together with opt-in visual effects. Neither track is retimed by corrected
variable BPM: the source-bound beat map still drives only rhythmic procedural motion.
The old plaintext captions, titles, authored lyric artwork and SRT/ASS exports
remain unchanged when these options are absent.

## Styled text blocks

Any existing `captions`, `word_captions`, or `identity[].style` text block may
supply its own **checksum-pinned** TTF/OTF `font` and an `effects` object.
The root `font` remains the default and must still be pinned for the rest of the
project. All font bytes are verified at preparation and render time; no font is
embedded in this feature, fetched from a URL or silently substituted.

The effects are drawn in this order: blurred offset shadow, outer glow,
expanded stroke, text fill (solid or multi-point gradient), progressive/active
word colouring, inner glow, bevel, emboss. All glyph styling is defined by
validated declarative JSON, not arbitrary SVG, shader code or executable scripts.

```json
{
  "font": {"path":"fonts/YourLicensedFont.otf", "sha256":"<64 hex characters>"},
  "captions": {
    "box": [60, 1150, 1020, 1380],
    "font": {"path":"fonts/YourLicensedDisplayFont.ttf", "sha256":"<64 hex characters>"},
    "font_size": 72,
    "min_font_size": 48,
    "word_mode": "progressive",
    "word_colour": "#fff0a0",
    "completed_colour": "#8ae6d6",
    "effects": {
      "gradient": {
        "stops": [
          {"at":0,"colour":"#192a67"},
          {"at":0.32,"colour":"#5db0f4"},
          {"at":0.72,"colour":"#f3eab6"},
          {"at":1,"colour":"#b55cba"}
        ],
        "angle": 80,
        "space": "rgb"
      },
      "stroke": {"width": 3, "colour": "#08152b"},
      "bevel": {"depth": 3, "strength": 0.75, "angle": 125},
      "emboss": {"depth": 2, "strength": 0.35, "angle": 160},
      "inner_glow": {"colour":"#ffffff", "radius":2, "strength":0.6},
      "outer_glow": {"colour":"#6fe8ff", "radius":8, "strength":0.7},
      "drop_shadow": {"dx":5,"dy":7,"blur":8,"opacity":0.6,"colour":"#070c19"}
    }
  }
}
```

Each `gradient.stops` array must contain 2–16 CSS colours or `{at,colour}`
objects. Positions run strictly from 0 to 1; automatic positions are assigned
to simple colours. Use `space: "rgb"` or `"hsv"`, and `angle` from -360 to 360
(degrees). The gradient is projected across the bounding text crop.

| Effect | Validated parameters | Meaning |
| --- | --- | --- |
| Stroke | `width` 0–24, `colour` | Dilated glyph-mask outline |
| Bevel / emboss | `depth` 0–12, `strength` 0–1, `angle` -360..360, `highlight`, `shadow` | Directional relief on glyph boundaries |
| Inner glow | `radius` 0–28, `strength` 0–1, `colour` | Luminous edge *inside* the glyph |
| Outer glow | Same as inner | Soft halo outside the text, bounded at the output frame |
| Drop shadow | `dx/dy` -64..64, `blur` 0–32, `opacity` 0–1, `colour` | Independent blurred, displaced text mask |

These are **raster** treatments, not font hinting instructions or physical 3D
extrusion. They are applied to both normal word-timed captions and the separate
`word_captions` lane. A selected word overlay uses the **same wrapped font
geometry** as the full-line gradient, avoiding baselines shifting while lyrics
are being sung. The original SRT/ASS subtitle files remain plain; the advanced
styling is part of the encoded FrameRudiments image.

## Word-following sprite with a one-dimensional native bounce

Use the unchanged source-clocked `lyrics` and `word_lyrics` inputs, and keep
`captions.word_mode: "highlight"` or `"progressive"` so **the whole line stays
visible**. The `word_indicator` sits over the active matched word, moves from
the preceding word during an optional transition, and adds one scalar native
DanceRudiment axis to the y-coordinate. The cursor may use a `spark`, `orb`,
`star`, `diamond` or `ring` raster sprite, or a checksum-pinned RGBA PNG.

```json
{
  "word_indicator": {
    "glyph": "spark",
    "sprite": {"path":"indicator.png","sha256":"<actual 64 character SHA-256>"},
    "position": "above",
    "size": 26,
    "colour": "#ffe8ac",
    "glow": 5,
    "opacity": 1,
    "travel_frames": 10,
    "offset_x": 0,
    "offset_y": 14,
    "bounce": {
      "motion": {"kind": "named", "pattern": "bounce", "repeat_clock": "continuous"},
      "axis": "x", "amplitude": 26
    },
    "particles": {
      "mode": "hybrid", "kind": "sparks", "rate": 30,
      "burst_count": 14, "lifetime_frames": 40,
      "size": 6, "speed": 2.0, "spread": 210,
      "opacity": 0.9, "colour": "#ffe0a6", "seed": 42,
      "cue_labels": ["*DROP*", "[SECTION: CHORUS*]"],
      "on_word": {
        "burst_count": 24, "colour": "#ffb643",
        "rate_multiplier": 1.6, "size_multiplier": 1.4,
        "glow": 3, "indicator_scale": 1.1, "duration_frames": 14
      },
      "on_cue": {
        "burst_count": 42, "colour": "#ff5c86",
        "indicator_colour": "#ff4b7c", "indicator_scale": 1.5,
        "rate_multiplier": 2.8, "glow": 6, "duration_frames": 28
      }
    }
  }
}
```

The `sprite` field is optional. For custom files, the supplied SHA-256 is
mandatory; changing images requires changing the project reference. The same
built-in transparent glyphs work without external assets. `position` accepts
`above`, `below` or `on`. `travel_frames` (0–120) determines the smoothstep
movement to the next word. Selecting `bounce.axis` projects one native
DanceRudiment dimension only; captured recordings retain their own source
clock, and rhythmic named/table motions use the corrected BPM map if enabled.

## Optional continuous particle effects and event-triggered overrides

`word_indicator.particles.mode` is one of:

| Mode | Behaviour |
| --- | --- |
| `off` | Only the word sprite moves/bounces |
| `continuous` | Constant emissions while the word indicator is active; word/cue triggers can still temporarily change colours, rates and sprite appearance |
| `word` | Only word-start bursts; optional `on_word` colour/size/glow overrides |
| `cue` | Only matching cue bursts; optional `on_cue` colour/size/glow overrides |
| `hybrid` | Constant emissions **and** word and cue bursts (independent event counts) |

`kind` is `sparks`, `smoke`, `embers`, `confetti`, `fireflies`, or `glitter`.
`rate` is 0–120 particles/s; `burst_count` 0–128; `lifetime_frames` 1–240;
`size` 1–30; `speed` 0–12; `spread` 0–360 degrees; `gravity`, `wind` -2..2;
`opacity` 0–1; `glow` 0–24; `trail` 0–30; and `seed` a nonnegative integer.
`on_word` and `on_cue` can independently set `burst_count` (including **zero**),
`rate_multiplier` (0–12), `size_multiplier` (0.1–6), `colour`, `glow`,
`opacity`, `indicator_scale`, `indicator_colour`, and `duration_frames` (1–240).
A trigger can therefore **change colour, pulse, increase particle density or
size without emitting any burst**. Cue values override word values only for
fields explicitly specified by that cue profile.

By default cue events come from the project's checksum-pinned `cues` track;
optional `particles.cue_file` can supply another pinned DanceMoves/StemLab LRC
or versioned JSON cue source. `cue_labels` are case-insensitive glob patterns;
`[]` matches all. Word-start bursts use the already matched per-word ledger,
and source excerpts/repeated audio map words and cues to output frames before
emission. No cue is inferred from tempo or musical analysis.

### Additional independent DanceRudiment modulation

The optional `word_indicator.modulations` list uses the same native, validated
`RudimentModulations` schema as other FrameRudiments visual layers. It can drive
`style.indicator_size`, `style.indicator_glow`, `style.indicator_opacity`,
`style.offset_x`, `style.offset_y`, `style.rotation`,
`style.bounce_amplitude`, `style.particle_rate`, `style.particle_size`,
`style.particle_glow`, `style.particle_opacity` and
`style.particle_burst`. Example:

```json
{
  "motion": {"kind": "named", "pattern": "circle"},
  "axis": "y",
  "target": "style.particle_rate",
  "range": [6, 72],
  "curve": "smoothstep",
  "smoothing_frames": 6
}
```

The emitted particles are derived from seeded birth frames, not from a running
mutable simulation. Seeking to the same output frame reconstructs the same
particle distribution; capped emission and lookback prevent unbounded memory
or render cost. The renderer produces **stylised** smoke and particle stamps,
not a fluid solver or true light scattering. All new overlay pixels can
participate in the existing layer-scoped `post_effects.elements["word-indicator"]`
chain and the final full-frame effects/AA/motion blur.

## Public CI gallery and QA

`scripts/generate_rich_lyric_gallery.py --output _site` generates eight synthetic
scenes with four moments each, and publishes them with a browsable JSON
configuration/evidence sidecar to `/rich-lyrics/` through the existing Pages
workflow. No real songs, font files, or source audio are published. Unit tests
check layout, gradients, stroke/glow masks, invalid input rejection, word/cue
bursts, constant emission, out-of-order frame seeking and native bounce.

Keep unrelated release states distinct: a merged patch is not a passed CI run;
a passed gallery build is not a deployed Pages site; and a technical-QA pass
is not attended listening or creative approval.
