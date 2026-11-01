# M6.4 — Alpha-masked overlays and colour blending

Implementation increment v26, 1 November 2026. Exact source baseline:
`9c3da8e0b19511e93cb284eab6403bc74b1878f9` (merged v25).
This is the CPU reference, graph integration and parameter-discovery increment.
It is not a package release, a GPU implementation, or full-repository acceptance.

## Delivered scope

The existing `layer_style` node now supports `color_overlay`, `gradient_overlay`
and `pattern_overlay`. It retains the existing `style_output`, `style_strength`,
`fill_opacity`, `alpha_source` and graph-level `opacity` controls. No additional
clock, audio analysis, mutable frame accumulator or external asset loader is added.

A paint field may cover the whole image internally, but visible output is masked
by the selected silhouette. Text, sprites, renderer output, a transition result,
and a composited group all use the same RGBA input contract. Processing branches
remain independent. A matte/chroma-key node can be placed before or after the
style. Unary overlays also work in transition-chain input preprocessing.

The shared colour-blend kernel supports 16 functions: `normal`, `multiply`,
`screen`, `overlay`, `darken`, `lighten`, `color_dodge`, `color_burn`, `hard_light`,
`soft_light`, `difference`, `exclusion`, `hue`, `saturation`, `color` and
`luminosity`. They are available through overlay `style_blend` and graph
`composite.mode`. Existing `over` and `add` implementations are unchanged;
`normal` is an additional spelling of Pillow's normal source-over. Legacy `add`
is the established bounded premultiplied sum with union alpha, not W3C plus-lighter.

## Examples

### Controlled gradient overlay

```json
{
  "id": "paint",
  "type": "layer_style",
  "inputs": ["layer:character"],
  "style_type": "gradient_overlay",
  "style_blend": "soft_light",
  "overlay_space": "source",
  "gradient_kind": "linear",
  "gradient_angle": 35,
  "gradient_space": "oklab",
  "gradient_stops": [
    {"id": "cold", "position": 0, "color": "#18d7ff", "opacity": 1},
    {"id": "hot", "position": 1, "color": "#ff458f", "opacity": 1}
  ]
}
```

Native bindings continue in the project's `effect_graph_controls` list:

```json
[
  {
    "id": "gradient-turn",
    "node": "paint",
    "property": "gradient_angle",
    "motion": {"kind": "named", "pattern": "circle"},
    "axis": "x",
    "range": [-90, 90]
  },
  {
    "id": "hot-green",
    "node": "paint",
    "property": "gradient_stops.hot.color.g",
    "motion": {"kind": "named", "pattern": "circle"},
    "axis": "y",
    "range": [32, 224]
  }
]
```

Stop controls use stable IDs, not list positions. Reordering or renaming a stop
requires a deliberate project edit. IDs must be unique, lowercase and 1–32
characters. Invalid paths are rejected before creating native motion drivers.

### Texture pattern supplied by another graph branch

```json
{
  "id": "live-pattern",
  "type": "layer_style",
  "inputs": ["layer:character", "processed-visualiser"],
  "style_type": "pattern_overlay",
  "pattern_kind": "texture",
  "pattern_scale": [64, 64],
  "pattern_offset": [8.5, -3.25],
  "pattern_angle": 20,
  "pattern_wrap": "mirror",
  "style_strength": 0.75
}
```

The first input supplies coverage and fill; the second supplies a live RGBA tile.
They need not have the same dimensions. A tile can come from a pinned image
sprite, a procedurally rendered layer, or an independently filtered branch.
Texture addressing never reads an arbitrary filesystem path. Original input
ownership and source-file provenance stay with the existing project renderer.

Procedural patterns require one input. Texture patterns require exactly two.
Categorical controls cannot change input arity. Switching a two-input texture
node to a unary style is rejected; use prebuilt branches and a transition/mix
instead. Texture-pattern preprocessing should be authored as an explicit graph
node before a transition chain; its unary shorthand does not provide a second
texture connection.

A fork/composite/control fragment is supplied at
`examples/effect-graph-m6-4-overlays.json`; it is an example graph fragment, not a
complete standalone media project.

## Alpha contract

Input and output are straight, display-referred RGBA8, matching the established
Pillow compositor. Generated images retain the input canvas dimensions.

For **combined output** at `fill_opacity=1`, current source alpha is preserved
byte-for-byte. Half-transparent ink does not become opaque just because a
second paint colour is applied. Transparent holes remain transparent. Original
alpha restricts coverage that earlier operations grew; it does not resurrect
pixels removed by keying in combined mode.

Let `A` be current alpha, `R` selected alpha, `P` paint alpha, `s` style strength,
`f` fill opacity and `B(Cb,Cs)` the chosen colour-blend function. Define
`q = P*s*clamp(R/A,0,1)`, with zero influence where `A=0`. Conditional coverage
is `d = q + f*(1-q)`. Then:

- `Aout = A*d`;
- `Cout = [Cb*f*(1-q) + B(Cb,Cs)*q] / d` when `d>0`.

This is masked painting within a layer, not source-over of two copies of the
same antialiased silhouette. Source fill and output opacity remain independent.
At zero style strength, the filled input is returned byte-exact when fill is 1,
including stored hidden RGB. Nonzero generated effects canonicalise fully
transparent output colour to zero.

For **effect-only output**, `Aout=R*P*s` and colour is the generated paint itself.
`style_blend` is intentionally a combined-output operation: it is not baked into
an isolated effect-only branch. Select a `composite.mode` when compositing that
branch later. With `alpha_source=original`, effect-only output can intentionally
recover the original silhouette, matching earlier layer-style alpha-lineage use.

Gradient-stop colour is premultiplied by its stop opacity before interpolation.
Texture bilinear filtering interpolates premultiplied RGBA and then safely
unassociates. Hidden red RGB in a transparent texel or gradient stop must not
contaminate a neighbouring visible blue pixel. Tests exercise that invariant.

## Gradient controls

| Field | Values / units | Default |
| --- | --- | --- |
| `gradient_kind` | `linear`, `radial`, `angular`, `reflected`, `diamond` | `linear` |
| `gradient_stops` | 2–16 objects with stable `id`, increasing `position`, opaque CSS `color`, optional `opacity` | black `start` at 0, white `end` at 1 |
| `gradient_angle` | degrees, -36,000…36,000 | 0 |
| `gradient_center` | normalized XY, each -4…5 | `[0.5,0.5]` |
| `gradient_scale` | dimensionless 0.01…100 | 1 |
| `gradient_phase` | cycles, -1024…1024 | 0 |
| `gradient_repeat` | `clamp`, `repeat`, `reflect` | `clamp` |
| `gradient_direction` | `forward`, `reverse` | `forward` |
| `gradient_space` | `srgb`, `linear_srgb`, `oklab` | `srgb` |
| `gradient_interpolation` | `linear`, `smoothstep`, `constant` | `linear` |
| `overlay_space` | `source`, `canvas` | `source` |

Stop positions and opacities are in [0,1]; colour component controls are [0,255].
Stops must remain strictly increasing after all controls are resolved. Simultaneous
position updates are validated as one table, not rejected midway through an
otherwise valid pair of movements. Duplicate positions are rejected; `constant`
interpolation supplies hard colour steps. Beyond the first/last authored stop,
the nearest endpoint colour/opacity is extended.

A linear gradient spans the projected bounds along its angle. Reflected gradients
fold that coordinate around the centre. Radial and diamond scales use half the
smaller bound dimension, so a radial field is circular in pixel coordinates.
Angular gradients use an angular turn, then scale. Direction reversal precedes
phase, followed by repeat/clamp. `source` bounds are the selected alpha bounding
box and can move with the subject; `canvas` bounds are fixed to the output frame.

`gradient_space` changes **gradient interpolation only**. It does not convert the
whole compositor to scene-linear HDR or change the working space of blend modes.
OKLab-to-sRGB out-of-gamut values are clipped for this SDR reference; full colour
management/gamut mapping is a later production-colour milestone.

## Pattern controls

| Field | Values / units | Default |
| --- | --- | --- |
| `pattern_kind` | `checker`, `stripes`, `dots`, `grid`, `texture` | `checker` |
| `pattern_scale` | XY cell size in pixels, each 1…4096 | `[32,32]` |
| `pattern_offset` | XY translation in pixels, each -65,536…65,536 | `[0,0]` |
| `pattern_angle` | degrees, -36,000…36,000 | 0 |
| `pattern_duty` | 0…1 stripe/grid width or dot diameter fraction | 0.5 |
| `pattern_softness` | 0…1 cell-relative edge softness | 0 |
| `style_color` | procedural foreground colour | black |
| `pattern_background` | procedural background colour | white |
| `pattern_foreground_opacity` | 0…1 | 1 |
| `pattern_background_opacity` | 0…1 | 1 |
| `pattern_wrap` | texture `repeat`, `mirror`, `clamp` | `repeat` |

Procedural fields use a pixel-footprint soft edge plus authored softness.
Checker has equal-size cells and does not use duty; texture mode uses its own
RGBA and does not use procedural colour/duty/softness controls. Non-square
scales intentionally stretch cells. Translation is in canvas/source pixel axes
before rotation; rotation is about the chosen overlay bounds' centre. A texture's
full extent fills one pattern cell. Repeat/mirror address across seams; clamp
extends edge texels. All texture modes use fractional bilinear sampling.

## Blending branches

`composite.mode` applies each later input over the preceding result in list order.
For new colour-blend modes, colour mixing is weighted by overlap alpha; source-only
areas keep their original colour. In premultiplied form:

`Co = Cs*As*(1-Ab) + Cb*Ab*(1-As) + B(Cb,Cs)*Ab*As`

`Ao = As + Ab*(1-As)`

This avoids multiply turning an isolated translucent image black over an empty
backdrop. Nonseparable hue/saturation/colour/luminosity modes use the W3C auxiliary
luminance/saturation and gamut-clipping definitions. They are not HSV substitutions.
Inputs with no alpha contribution do not influence visible colours.

The W3C equations are mathematical references, not a claim that this package is
a browser engine or implements all CSS isolation, colour-space and group semantics.

## Controls, validation and compatibility

`control_descriptors(node_id)` exposes static controls plus the current gradient
stop IDs, ranges, units, defaults and applicability. Static style controls can be
preconfigured while a different style is selected, preserving the earlier M6
convention; metadata identifies which styles use each field. Nested gradient
stop controls require a gradient-overlay node. Enum bindings use native `choices`;
numeric bindings reuse existing curves, source clocks, lag, smoothing and combination.

Examples: `gradient_center.x`, `pattern_scale.y`, `pattern_offset.x`,
`pattern_background.b`, `gradient_stops.hot.position`,
`gradient_stops.hot.color.g`, `gradient_stops.hot.opacity`, `style_blend`.

Every resolved style is revalidated before drawing, including stop order and
texture port arity. Temporary overrides copy nested data and do not mutate the
authored graph, its stops, input images or module defaults. No-graph paths and
existing `over`/`add` algorithms are untouched. Existing v24/v25 styles retain
their rendering algorithms and defaults.

## Resource and backend limits

The output ceiling is 67,108,864 pixels; a texture input is limited to 16,777,216
pixels. Tile-backed float work uses at most 64 output rows at a time; texture
storage remains RGBA8 rather than a full float conversion. These are explicit
algorithm limits, not a promise that every allowed image fits every host's RAM.
Images, final outputs and retained graph branches still occupy memory.

This patch does not implement GPU-native paint, blend shaders or texture-resident
graph scheduling. Existing `layer_style` policy is unchanged: CPU works directly,
`auto` records a CPU fallback, and strict `gpu` rejects the style. Composite colour
blends execute in the established CPU compositing stage; they are not counted as
accelerated GPU nodes. Do not interpret an enabled GPU backend as a wholly GPU-resident graph.

## Files and test gates

New runtime modules: `effect_graph_blend.py`, `effect_graph_layer_overlays.py`.
Updated: `effect_graph.py`, `effect_graph_layer_styles.py`.

Focused tests:

```text
tests/test_effect_graph_blend_modes.py
tests/test_effect_graph_overlays.py
tests/test_effect_graph_overlay_graph.py
tests/test_effect_graph_overlay_visuals.py
```

Native acceptance: `tests/test_effect_graph_overlay_project.py` uses actual
Project, Compositor and DanceRudiment APIs with generated pinned media and an
installed font. No substitute driver is installed. CI must supply its existing
font and FFmpeg environment; a skipped native test is not acceptance.

Visual QA generator:

```sh
python integration/build_effect_graph_m6_4_visuals.py qa/m6-4
```

This produces 16 real RGBA outputs, a contact sheet and pixel hashes. The imagery
is synthetic and is evidence of implementation behaviour, not perceptual approval
of a user's creative assets.

Required acceptance: focused tests, native controls/project tests, complete CPU
suite without skipped tests, Ruff, exact patch application, and existing GPU CI.
M6.5 still covers consolidated style capability/bounds reporting and cross-renderer
acceptance. GPU-native styles and production HDR remain separate later gates.

## Primary technical references

Consulted 1 November 2026:

- W3C, Compositing and Blending Level 1: https://www.w3.org/TR/compositing-1/
  (source-over general formula, separable and nonseparable blend functions).
- W3C, CSS Color Module Level 4: https://www.w3.org/TR/css-color-4/
  (colour-space interpolation, premultiplication and OKLab concepts).

No third-party shader library or new runtime dependency is introduced. The
implementation uses the existing Pillow and optional NumPy effect-graph extra.
