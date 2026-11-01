# M6.1–M6.2: alpha-derived layer styles

Implementation increment v24, 1 November 2026. Base: `de0b4bb37e3b0b827f3367dac40eae4fe9faa0d7`.
This is the CPU reference and graph integration for the first two M6 stages,
not completion of M6, a GPU implementation, or a published package release.

## Delivered behaviour

A unary `layer_style` node accepts any same-canvas RGBA graph source: sprites,
text, renderer output, transitions, groups already composited into a surface,
or another processing branch. It derives all shape operations from alpha,
never from RGB brightness or the rectangular source image bounds.

Supported `style_type` values are `drop_shadow`, `inner_shadow`, `outer_glow`,
`inner_glow` and `outline`. Outlines have `outer`, `inner` and `center` placement.
Every output can be matted, keyed, transformed by existing nodes, forked or
composited exactly like other graph results. `layer_style` is also permitted
in transition-chain `input_effects`, retaining the normal generated stable IDs.

No project-loader or compositor patch is necessary: the existing source routing,
control evaluator and graph integration already provide the required contracts.
Projects without the new node retain the existing execution path.

## Example: single styled source

This is a **project fragment**, not a standalone project with pinned media:

```json
{
  "effect_graph": {
    "nodes": [
      {"id": "layer:native", "type": "source"},
      {
        "id": "neon",
        "type": "layer_style",
        "inputs": ["layer:native"],
        "style_type": "outer_glow",
        "style_color": "#00d9ff",
        "style_spread": 2,
        "style_blur": 8,
        "style_strength": 0.8,
        "alpha_source": "original"
      }
    ],
    "output": "neon"
  },
  "effect_graph_controls": [
    {
      "id": "beat-glow",
      "node": "neon",
      "property": "style_blur",
      "motion": {"kind": "named", "pattern": "circle"},
      "axis": "x",
      "range": [3, 14]
    }
  ]
}
```

Use actual valid compositor source IDs. A complete fork example is in
`examples/effect-graph-m6-styles.json`. The example reuses existing native
DanceRudiment motion and does not add an oscillator or reanalyse audio.

## Parameters, defaults and control paths

All numerical values must be finite; booleans are not accepted as numbers.
All enum choices are validated before motion loading by the existing adapter.

| Node property | Default | Bounds / choices | Control path |
| --- | --- | --- | --- |
| `style_type` | `drop_shadow` | five types above | `style_type` (categorical) |
| `style_color` | `#000000` | opaque CSS RGB colour; no embedded alpha | `style_color.r`, `.g`, `.b` in 0..255 |
| `style_offset` | `[4,4]` | each -4096..4096 output pixels | `style_offset.x`, `.y` |
| `style_blur` | 0 | 0..128 pixels | `style_blur` |
| `style_spread` | 0 | -64..64 pixels | `style_spread` |
| `style_width` | 3 | 0..64 pixels | `style_width` |
| `outline_position` | `outer` | `outer`, `inner`, `center` | `outline_position` (categorical) |
| `style_strength` | 1 | 0..1, effect intensity only | `style_strength` |
| `fill_opacity` | 1 | 0..1, source artwork only | `fill_opacity` |
| `style_output` | `combined` | `combined`, `effect` | `style_output` (categorical) |
| `alpha_source` | `current` | `current`, `original` | `alpha_source` (categorical) |
| `opacity` | 1 | 0..1, whole node result | `opacity` (existing universal stage) |

Offsets apply to the two shadow styles. Width/placement apply to outlines.
Spread/blur apply to shadows and glows; they do not silently soften an outline.
`fill_opacity` affects combined output, not effect-only output. These fields may
remain in a configuration when animating `style_type`; their applicability is
reported by the descriptors. Defaults are deliberately shared across style
changes: select a visible glow/outline colour explicitly.

`graph.control_descriptors(node_id)` returns the new style node's typed paths,
current base/default values, units, valid bounds/choices and applicable styles.
It supplements the existing control API; it does **not** claim that all older
effect families already implement descriptor discovery. Returned data is copied
and can be edited by a caller without mutating the graph or registry.

Numeric and categorical controls continue through `EffectGraphControls`, including
existing curve, lag, smoothing, weight and combination rules. Per-frame overrides
copy nested vectors and colours. The graph and input image remain unchanged.

## Alpha semantics and compositing

The input is straight, display-referred RGBA, matching the established CPU graph.
This increment does not enable linear-light/HDR compositing or change colour
management for other effects.

Let A be selected reference coverage, D/E fractional dilation/erosion, B blur,
and T a fractional zero-border translation. The reference masks are:

* Drop shadow: `T(B(D(A, spread)), offset)`, behind the source fill.
* Outer glow: `max(B(D(A, spread)) - A, 0)`, behind the source fill.
* Inner shadow: `max(A - T(B(E(A, spread)), offset), 0)`.
* Inner glow: `max(A - B(E(A, spread)), 0)`.
* Outer outline: `D(A, width) - A`; inner outline: `A - E(A, width)`.
  Centre placement uses half the requested width on each side.

Dilation with negative spread is erosion; erosion with negative spread is
dilation. Square structuring elements are explicit: this first implementation
is not an exact circular/disk stroke and has no vector join or miter semantics.
Fractional radii interpolate adjacent integer morphology results. Antialiased
input coverage is retained rather than thresholded for the rendered styles.

Inner effects are shaded within conditional source coverage. A fully filled
layer keeps its original alpha exactly, including semi-transparent edges;
ordinary source-over of a second masked layer would incorrectly increase it.
With fill disabled, only the selected inner edge remains. Outer effects are
composited behind the filled source using ordinary alpha-over. Arbitrary
style-specific blend modes are deferred to M6.4; graph composites still provide
the existing `over` and `add` choices.

`alpha_source=original` selects the existing inherited source-alpha metadata for
**effect generation**. It does not restore the source's RGB or fill alpha. In
combined mode inner styling remains clipped to current source coverage; in
effect-only mode its mask uses the chosen reference alpha. Merges/transitions
retain the established lineage reset, while unary styles inherit lineage.

`style_output=effect` emits only the coloured mask. It is intended for reusable
shadow/glow/outline branches; `opacity` is still applied once by the graph after
style rendering. To apply a key or matte before/after the style, wire the
corresponding nodes before/after it. No implicit stage ordering is introduced.

## Geometry infrastructure and boundaries

`effect_graph_alpha_geometry.py` supplies checked alpha crops, explicit zero
padding, fractional square morphology, inside/outside contours, blur and
bilinear placement. Padding is based on spread, width and conservative blur
support, **not** the potentially large shadow offset. A shifted blurred mask
can move into the frame from beyond the original canvas without being clipped
prematurely. The final project canvas is still the intentional crop boundary.

`style_bounds(alpha, config)` provides conservative un-clipped bounds for
future scheduling. It is not a claim that the compositor now supports arbitrarily
sized render surfaces. Inner styles preserve the source bounds.

The shared `signed_distance` helper computes separable Euclidean distances to
opposite-class **pixel centres**, with negative values inside and a half-pixel
boundary correction. Threshold, saturation limit, empty masks and transparent
exterior are explicit. This is not exact distance to a reconstructed vector
contour. Normal style rendering uses soft morphology, not thresholded distance
fields. The field is a separately callable foundation for the later bevel/rim
work and is checked against an independent brute-force oracle in unit tests.
No SciPy dependency, GPU or native extension is required for it.

Resource gates are explicit: output canvas <= 67,108,864 pixels; the padded
alpha workspace <= 16,777,216 pixels; requested distance map <= 1,048,576 padded
pixels by default. The distance utility permits a caller-supplied test/resource
budget. The style JSON does not expose a way to disable these checks. Inner RGB
processing uses at most 128 rows of float temporaries at a time. A fully covered
8K frame can exceed the alpha-workspace cap; this implementation rejects that
case rather than silently downsampling. Sparse large-canvas sources may fit.

No mutable cross-frame cache or hidden smoothing state is introduced.

## Backend and dependency contract

Required: the existing Python, Pillow and `effect_graph` NumPy dependencies.
There are no new mandatory packages, model downloads, private assets or fonts.
The small installed test font used by full-project tests is referenced locally
and is not redistributed.

`cpu` renders styles directly. With an existing backend in `auto`, the node is
counted as a CPU fallback. Strict `gpu` rejects `layer_style` with `GPUUnsupported`;
it does not count CPU output as GPU execution. Existing GPU matte/key/spectral
nodes are untouched. GPU styles, GPU-resident chaining and cross-vendor parity
are later acceptance gates, not delivered here.

## Acceptance gates and test ownership

| Gate | Evidence / state |
| --- | --- |
| Alpha morphology and distance maths | `test_effect_graph_alpha_geometry.py`; independent all-pairs distance reference |
| Seven visual style/placement variants | `test_effect_graph_layer_styles.py`; fractional alpha, holes, source bounds and fill |
| Registered controls and no mutation | `test_effect_graph_layer_style_graph.py`; each numeric/enum control, limits, original/current alpha |
| Existing graph interoperability | fork, matte/key before/after, transition-chain preprocessing and backend dispatch tests |
| Actual reproducible output | `integration/build_effect_graph_m6_visuals.py` and `test_effect_graph_layer_style_visuals.py` |
| Native Project/Compositor/DanceRudiment | `test_effect_graph_layer_style_project.py`; provided for full repository/CI execution |
| Existing suite, Ruff, package build, supported OS/Python matrix | Must pass on the complete merged repository; not inferred from focused tests |
| GPU-native styles and parity | Not implemented in this increment |

The local development environment contained hash-matched graph modules, not a
complete repository or installed DanceRudiments runtime. Focused graph/image
checks used those actual modules without replacement motion implementations.
The native-project tests and full Ruff/build/CI gates were not run there.
The package's verification report records the exact executed suite, exclusions
and application checks. Do not treat included-but-unexecuted tests as passes.

## Next increments

M6.3 builds bevel/emboss, directional edge lighting and controlled extrusion on
this geometry layer. M6.4 supplies overlays and additional blend modes. Remaining
M6.5 work generalises parameter discovery and bounds-aware scheduling; the new
style parameters already have native bindings. M8 owns GPU-native execution and
texture-resident resource reuse. Avoid claiming all of M6 complete before these
gates and the full integration suite are satisfied.

## Research references

* Pillow ImageFilter: https://pillow.readthedocs.io/en/stable/reference/ImageFilter.html
  (GaussianBlur and rank morphology; the project retains its Pillow backend.)
* W3C compositing and blending: https://www.w3.org/TR/compositing-1/
  (alpha coverage, source-over and the distinction between blending and compositing.)
* SciPy exact EDT contract: https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html
  (independent reference for pixel-centre distance semantics; not a dependency.)

These references inform semantics, not claims of Photoshop equivalence, colour
management, subjective approval or GPU speed.
