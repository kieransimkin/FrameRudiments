# Project format v1

The schema identifier is `framerudiments.project.v1`. Unknown fields are errors.
All file references are `{ "path": "relative-or-absolute", "sha256": "64 hex digits" }`.
Hash each final input, including the font; a changed file fails preparation and
the inputs are checked again before and after a render.

| Field | Meaning |
| --- | --- |
| `title`, `artist`, `notes` | Private project description; text appears only through `identity`. |
| `purpose` | `short` (120 ms endpoint fades by default), `visualiser`, or `canvas` (no fades by default). |
| `width`, `height`, `fps`, `fps_reason` | Even dimensions, fps defaults to 60; other rates need a reason. No platform compliance is inferred. |
| `audio` | `source` file reference, verified `sample_rate`, start-inclusive `start_sample`, end-exclusive `end_sample`, optional positive integer `repeats` and `fade_seconds`. |
| `clock` | `bpm`, `beat_grid` or source-bound `beat_alignment` file reference; `authority`, `status` (`confirmed`, `estimated`, `synthetic`), optional `origin` in source seconds and `phase_status`. A reading owns its BPM/origin and cannot be combined with `beat_grid` or `origin`. |
| `font` | Pinned default TTF/OTF for authored text; text blocks may override via their own pinned `font`. |
| `lyrics`, `cues` | Separate LRC references; `.cue` files are also supported for structural cues. |
| `word_lyrics`, `word_captions`, `word_indicator` | Word LRC source, optional word lane, and word-following sprite/effects. |
| `render_quality` | High-quality default: 4× source-vector/font raster, 8 distinct source-time samples per encoded frame and 3-pass edge-aware finishing; explicit `enabled`, `spatial_scale` (1–4), `temporal_samples` (1–8), and AA controls lower the quality for chosen projects. |
| `canonical_lyrics` | Optional pinned plain text whose performed nonempty lines must exactly match the LRC. Bracketed structural lines in this comparison file are excluded. |
| `background` | `colour`, optional pinned `image` and `fit` (`cover` or `contain`). |
| `captions` | `box` [left,top,right,bottom], `font_size`, optional `min_font_size`, `colour`, `panel`, `stroke`, `line_spacing`, `mode` (`current` or `neighbours`). |
| `identity` | List of `{text, style}`; each style uses the same text-block fields as captions. |
| `layers` | Ordered sprite, legacy bar and modular rudiment layers described below. |
| Native radial petals | Use `type: "rudiment", renderer: "rangoli"` with independent scale/spin/lift axes; see [rangoli configuration and actual demo](rangoli.md). |
| `visualiser_routing` | Optional per-element GPU input/overlay policies. Feed continuously, only for first N native pips after visibility onset, or never; see [routing](visualiser-routing.md). |
| `effects` | Ordered Butterchurn/projectM stages. Butterchurn may set a denser `mesh_width`/`mesh_height`; any stage may set `input_effects`, retained-loop `feedback_effects`, `output_effects`, a multi-stop `output_palette_grade`, and a stage-level `composite` containing chroma key, named alpha/luminance matte and opacity controls. projectM stages may use `preset_timeline` for ordered changes at canonical source seconds through the native soft transition on one retained feedback instance. See [visualisers](visualisers.md). |
| `lyric_art` | `{items: [{text, asset}], placement: <sprite fields>, fallback: "text" or "error"}`. Exact text lookup preserves canonical lyric association. `error` requires every visible lyric to have an asset. |
| `stemlab_loop` | `{file: <pinned loops report>, id: <accepted loop id>}`; imports native bounds/rate, verifies source SHA, rejects conflicting explicit audio values. Does not approve a seam. |

Sprites use `id`, `asset`, `center` [x,y], `width` and optional `bounds`
[left,top,right,bottom]. Optional `crop_alpha`, `opacity`, `motion`, `mapping`,
`atlas`, `trail`, source-time `start_seconds`/`end_seconds` and `cue_labels`
control their appearance. `mapping` amplitudes are `dx`, `dy`, `rotation`
(degrees), `scale` (magnitude less than one); `x_axis`, `y_axis`, `rotation_axis`
and `scale_axis` select index 0,1,2 of the motion projection. Defaults are
0,1,2,2 respectively. Set `y_axis: 0` to map a one-dimensional bounce vertically.

![Actual encoded original/aligned clock example: captions and recorded bands retain source time](media/beat-alignment.png)

[Watch the synthetic clock example](media/beat-alignment.mp4) and follow the
[source-bound alignment setup](beat-alignment.md). Its first click at 0.125
seconds maps to pip zero; the screenshot is encoded frame 75 at source 1.25
seconds. [Verification and media hashes](media/beat-alignment-evidence.json).

Atlas configuration is `{columns, rows, count, cycle_ticks, phase_beats?}`.
The atlas must divide into exact cells. One beat is 16 ticks; cycle ticks are
explicitly authored rather than silently quantised. Cycles use a continuous
beat clock across repeated source excerpts. `trail` accepts `offset` [x,y],
`opacity` and `radius`, with the expanded result included in bounds checks.

Motion kinds:

* `composite`: `channels` is a list of one-to-three `{motion, axis}` entries;
  each output axis samples its own native motion route. Use this for independent
  sway, bounce and tilt rather than conflating their periods or phases.
* `named`: native `pattern`, optional `rate`, `phase_beats`, `origin_beat`.
* `table`: pinned `file` containing an existing native sample export and
  `pattern`. Requires 64-pip, version, binary-hash and exact period identity.
* `match`: pinned StemLab report `file`, explicit zero-based `source_index`
  and `match_index`, optional `source_offset_seconds` for a documented excerpt
  report. Uses the selected pattern/phase and source-window origin. Extrapolation
  requires `allow_outside_match: true`; it never implies approval.
* `recording`: pinned native recording `file`, optional one-to-three named
  `axes`. Its BPM, beat zero and negative start pip stay authoritative. Outside
  capture `outside` is `error` by default, or explicitly `neutral`. Rate,
  phase and interpolation overrides are rejected.

`interpolate: true` on named/table/match routes explicitly blends adjacent
integer native samples for old compositor parity. Default sampling is native
integer-pip. Named/table `repeat_clock` is `continuous` by default; `source`
replays the source phase each cycle. Recorded and matched motion always use
absolute source time.

A layer with `type: "bars"`, `bounds` and a recording motion displays every
dimension on a signed -1..+1 scale, including Fourier buckets. It does not
extract frequencies or generate envelopes: StemLab supplies that evidence.

The Python `scene(image, FrameContext)` hook supports release-owned custom
flight physics, fog, backgrounds and choreography. Pure/random-access hooks
are easiest to test; a stateful simulation must advance by encoded frame in
a deterministic order and own its collision/occlusion QA. Custom drawing is
not automatically covered by sprite bounds. All external assets must be pinned.


A modular layer uses `type: "rudiment"`, `renderer` (`particles`, `bars`,
`waveform`, `path`, `spectrum` or an explicitly registered trusted Python renderer),
`motion`, integer-pixel `bounds`, optional original-dimension `axes`, `style`,
`history_frames`, `history_stride`, `opacity` and the existing source-time/cue
visibility gates. Every rudiment layer additionally accepts optional `visual`
(palettes, gradients, colour grading and image transforms) and `modulations`
(up to 32 native DanceRudiments driving numeric renderer `style.*` or shared
`visual.*` properties). See [rudiment-renderers.md](rudiment-renderers.md)
and [annular-modulation.md](annular-modulation.md) for validated controls.
All rudiment layers share the same native sample and
source/frame clock as sprites; waveform views trace the supplied rudiment
values and do not compute PCM audio or frequency evidence.


## Scoped compositor post effects

The optional top-level `post_effects` object assigns ordered, deterministic
`VisualStyle` effect stages to individual components (`background`, `scene`,
`layer:ID`, `identity:N`, `caption`, `lyric-art:N`) using `elements`, to all
individual elements using `all_elements`, or to the completed composited frame
using `frame`. Every numeric effect setting accepts a source-clocked independent
DanceRudiment modulator; palettes and gradient settings use native-driven
categorical `choices`. See [scoped-post-effects.md](scoped-post-effects.md).
Stages run before the optional full-frame Butterchurn/projectM `effects` chain.
The original compositor pixel path remains unchanged when no post effects are
configured.

## Bitmap and sprite-backed Rudiment renderers

All existing renderer names still select vectors. The 52 new `sprite-*` names
render transparent PNG atlas tiles rather than Pillow vector primitives, with
LCD segment options, custom `style.sprite_asset` SHA-pinned files, on/off segment
states, radial layouts, deterministic animation-cell selection and native
DanceRudiment modulation of numeric style properties. See
[sprite visualisations](sprite-visualisations.md).

## Particle and smoke emitters

`layers` may include `{ "type": "emitter", "id": "haze", "kind": "smoke", ... }`.
Emission modes are `continuous`, `cue` and `hybrid`. Cue `.lrc` files use
DanceMoves/StemLab source-time labels; `.json` accepts DanceMoves cue objects
or StemLab cue-snap/cue-generate reports. Optional custom particle PNGs and cue
files must use ordinary `{path, sha256}` file references. Independent native
DanceRudiment modulators can target validated numeric `style.*` or `visual.*`
settings. See [particle and smoke](particle-smoke.md) for the full schema,
selection patterns, repeat/excerpt behaviour and safety limits.

## Variable tempo and dual LRC subtitle sources

`clock.beat_timeline` is a pinned `stemlab.beat-timeline.v1` file: beat/sample
anchors control procedural phase with local interval BPM and fractional pips,
but must not be combined with `beat_alignment`, `beat_grid` or `origin`.
A supplied `bpm` is a display hint, not the effective rhythm.
`lyrics` remains the per-line LRC and `word_lyrics` is the optional,
independently hashed per-word LRC. `captions.word_mode` may be `off`,
`highlight`, `progressive` or `current`; the default with both files
is `highlight`. `word_captions` is an independent text-block configuration
for a second, simultaneously rendered word-only subtitle lane.
See [variable BPM/word LRC guide](variable-bpm-word-subtitles.md).

## Rich text and word indicator

Set `effects` and optional checksum-pinned `font` on any `captions`, `word_captions`, or
`identity[].style` text block. Rich styles include multi-point gradient fill, stroke, bevel,
emboss, inner/outer glow and drop shadow. `word_indicator` needs both line and word LRC,
a full-line captions block and a selected native one-axis Rudiment bounce driver. Its
particle options support constant emission, timed word/cue bursts and trigger-driven
appearance changes. `travel_mode: "between_words"` can use the pinned next-word
timestamp to follow a smooth pre-hit arc and land exactly on the word frame;
see [full schema and examples](rich-lyric-effects.md).

## Optional full-frame effects

The top-level `effects` list is additive to v1. See [visualisers.md](visualisers.md)
for the `id`, `engine`, `preset`, `input_mix`, `seed`, `trusted_preset` and
stage-level `composite` fields,
runtime setup and sequential feedback contract. Both engines process all existing
layers, including native moving sprites/atlases, captions and lyric art, before
encoding. Existing projects without `effects` keep the original composition path.

![Actual synthetic frame before and after both visualisers](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.1.4/docs/media/full-frame-comparison.png)

Actual FrameRudiments 0.1.4 candidate output: timed captions and native moving
sprites/atlas before composition effects (left) and after both real engines
(right). Synthetic oscillator audio and example words; this is a software demo.
[Play the four-second demonstration](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.1.4/docs/media/full-frame-demo.mp4)
and [read its transcript](https://github.com/kieransimkin/FrameRudiments/blob/v0.1.4/docs/media/demo-transcript.md).
Playback is user-controlled; no automatic media playback is required.

![Generated processing-order illustration; not a screenshot](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.1.4/docs/media/effects-pipeline.svg)

Generated vector illustration of the processing order. The comparison above
and linked video are captured renderer output; the diagram is explanatory.

## High-quality anti-aliasing and motion blur

The new `visual.antialias` and `visual.motion_blur` controls can be applied to individual elements or the finished frame and modulated with native DanceRudiments. `post_effects.temporal` separately integrates observed past encoded frames with a bounded historical shutter; see [quality effects](quality-effects.md) for safe settings, constraints, and the CI gallery.
