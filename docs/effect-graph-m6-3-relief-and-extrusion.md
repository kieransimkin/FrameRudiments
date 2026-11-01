# M6.3 — alpha relief, edge lighting, satin and extrusion

Implementation increment **v25**, 1 November 2026. Base commit:
`1cb7c216029ccd17cf89088fd51f77882c7ba24f` (merged v24).

This extends the existing unary `layer_style` node. It is the CPU implementation
of M6.3, not a GPU implementation or a new package release. The reference
contract remains same-canvas, straight, display-referred RGBA; none of the
existing transition clocks, media sources or GPU algorithms is replaced.

Related: [M6.1–M6.2](effect-graph-m6-alpha-layer-styles.md),
[unified roadmap](unified-effects-transitions-rendering-roadmap.md),
[transition chains](effect-graph-m5-4-transition-chains.md).

## Delivered effect family

| `style_type` | Shape and compositing behaviour |
| --- | --- |
| `bevel` | Distance-derived ramp from the alpha edge to an interior plateau; directional highlights/shadows. |
| `emboss` | Raised or recessed **interior border ridge**, with two opposing slopes. This is not Photoshop compatibility or whole-image convolution emboss. |
| `edge_light` | Light-facing contour illumination; inner, outer or centred placement through `outline_position`. |
| `satin` | Symmetric displaced/blurred alpha interference, clipped to selected coverage. |
| `extrusion` | Fractional 2-D silhouette sweep with near-to-far colour interpolation. |
| `long_shadow` | The same bounded silhouette sweep with a constant colour and distance fade. |

The effects consume arbitrary rendered RGBA sources: sprites, text, visualisers,
mesh renders already converted to surfaces, and intermediate graph compositions.
They are 2.5-D image effects, not actual mesh extrusion, a physically based
material system, specular reflection or a temporal motion echo.

Styles can be forked, used as effect-only branches, composited, matted or keyed
before/after processing, and inserted into `transition_chain.input_effects`.
The existing source-alpha lineage and stable generated chain IDs are retained.

## Example: lit body and separate depth branch

A runnable graph fragment using the synthetic demo's `layer:native` source is
in `examples/effect-graph-m6-3-relief.json`. A minimal node is:

```json
{
  "id": "lit-body",
  "type": "layer_style",
  "inputs": ["layer:native"],
  "style_type": "bevel",
  "style_width": 8,
  "bevel_depth": 10,
  "bevel_softness": 1,
  "bevel_profile": "round",
  "bevel_direction": "up",
  "light_angle": 225,
  "light_elevation": 30,
  "highlight_color": "#ffe9b0",
  "shadow_color": "#15233d",
  "alpha_source": "original"
}
```

This is a project fragment, not a replacement for the project schema or pinned
media references. Use actual compositor source IDs. A native binding can target
`light_angle`, `bevel_depth`, `extrude_length`, a colour component or any of the
other discoverable controls:

```json
{
  "id": "moving-light",
  "node": "lit-body",
  "property": "light_angle",
  "motion": {"kind": "named", "pattern": "circle"},
  "axis": "x",
  "range": [-180, 180]
}
```

No PCM analysis, parallel oscillator or frame-history state is introduced.
Numeric controls use the existing curves, lag, smoothing, weight and combination
rules. Categorical controls use existing `choices` bindings.

## Parameter contract

All numbers must be finite; booleans are not accepted as numeric parameters.
Colour strings must represent opaque CSS RGB; colour alpha is controlled by the
strength/opacity controls instead. Colour components use 0..255 values.

| Parameter | Default | Range / choices | Applicable effects |
| --- | --- | --- | --- |
| `style_width` | 3 | 0..64 pixels | Bevel/emboss width and rim thickness; existing outlines unchanged |
| `bevel_depth` | 4 | 0..64 height pixels | Bevel, emboss |
| `bevel_softness` | 1 | 0..16 pixels | Height smoothing for bevel/emboss; normal-field smoothing for rim |
| `bevel_profile` | `round` | `linear`, `round` | Bevel, emboss |
| `bevel_direction` | `up` | `up`, `down` | Bevel, emboss |
| `relief_threshold` | 0.5 | 1/255..1 relative to peak selected alpha | Bevel, emboss, rim geometry |
| `light_angle` | 225 | -3600..3600 degrees | Bevel, emboss, rim |
| `light_elevation` | 45 | 0..90 degrees | Bevel, emboss, rim |
| `highlight_color` | `#ffffff` | `.r`, `.g`, `.b`: 0..255 | Bevel, emboss, rim |
| `shadow_color` | `#000000` | `.r`, `.g`, `.b`: 0..255 | Bevel, emboss |
| `highlight_strength` | 0.8 | 0..1 | Bevel, emboss, rim |
| `shadow_strength` | 0.8 | 0..1 | Bevel, emboss |
| `outline_position` | `outer` | `inner`, `outer`, `center` | Rim; existing outline semantics unchanged |
| `style_offset.x/y` | 4, 4 | -4096..4096 pixels | Satin's symmetric displacements; existing shadows unchanged |
| `style_blur` | 0 | 0..128 pixels | Satin and existing shadow/glow effects |
| `satin_mode` | `normal` | `normal`, `inverted` | Satin |
| `style_color` | `#000000` | `.r`, `.g`, `.b`: 0..255 | Satin, near extrusion colour, long shadow; existing styles unchanged |
| `extrude_color` | `#000000` | `.r`, `.g`, `.b`: 0..255 | Far extrusion colour |
| `extrude_length` | 16 | 0..256 pixels | Extrusion, long shadow |
| `extrude_angle` | 45 | -3600..3600 degrees | Extrusion, long shadow |
| `extrude_fade` | 0 | 0..1 | Extrusion, long shadow |

`style_strength`, `fill_opacity`, `style_output=combined|effect`,
`alpha_source=current|original`, and universal node `opacity` remain supported.
`style_strength` multiplies the effect only; `fill_opacity` affects artwork;
`opacity` is applied once to the final graph-node result.

`graph.control_descriptors(node_id)` reports current/default values, units,
bounds, choices and applicable styles. Fields may remain in a node while
`style_type` is animated; unused fields have no effect for that selected style.
This is a declared compatibility feature, not silent coercion. Metadata and
frame-local nested colour/vector changes do not mutate authored configuration.

Angles use **image coordinates**: 0 points right and 90 points down. Angles are
reduced modulo 360 for trigonometry, with no hidden rotation accumulator.
Elevation 0 is grazing; 90 is overhead. It is the direction *toward the light*,
whereas `extrude_angle` is the direction *away along the sweep*.

## Relief mathematics and alpha correctness

Geometry uses the existing separable signed-distance helper: negative inside,
positive outside, distances to opposite-class pixel centres with a half-pixel
boundary correction. It is not exact distance to a reconstructed vector contour.
The alpha crop is divided by its maximum for the **geometry threshold only**.
Uniformly fading a silhouette therefore does not erase its bevel. True soft
coverage remains the mask for emitted effects and final shading.

For distance `d` and width `w`, the bevel ramp is `u = clamp(-d/w, 0, 1)`.
A linear profile uses `u`; a rounded profile uses `u*u*(3-2*u)`. The emboss ridge
uses `1-abs(2*u-1)` or `sin(pi*u)`. Depth scales height; `down` negates it.
A bounded, separable Gaussian smooths the height in floating point, without a
SciPy dependency or an 8-bit intermediate height quantisation.

From finite differences `(gx, gy)`, the unit surface normal is proportional to
`(-gx, -gy, 1)`. The signed lighting difference is `dot(N,L) - L.z`.
Positive differences become highlights; negative differences become shadows.
Thus a flat plateau does not acquire an unrelated global exposure change.
This is stylised relative diffuse lighting, not a specular/BRDF shader.

Bevel, emboss and satin use conditional-coverage shading. At full fill opacity,
**current source alpha is preserved byte-for-byte**, including partially opaque
pixels and holes. `alpha_source=original` controls geometry/effect-only masks;
it does not restore source RGB or make the combined interior fill opaque.

Rim lighting uses the outward distance-field normal, positive light facing and
separate inner/outer masks. Inner rim shading preserves coverage. Outer rim
light composites behind the fill and may legitimately add coverage outside or
at the antialiased edge. Effect-only output includes both portions when centred.

Satin is `abs(T(+offset, blur(A))-T(-offset, blur(A)))`, normalised by peak alpha
and clipped by selected coverage; `inverted` complements the interference within
that coverage. It never wraps at image borders. Zero normal-mode offsets produce
no interference. Satin intentionally does not use `style_spread`.

## Extrusion and long-shadow contract

The sweep includes the origin and terminal offset, using `ceil(length)+1`
samples, so consecutive samples are no more than one Euclidean pixel apart.
Subpixel sampling uses the existing transparent-border bilinear mask placement.
There is no user-controlled coarse sampling setting that quietly leaves gaps.

Coverage is the maximum of sample alpha after distance fade, **not repeated
source-over of every stamp**. Nearest sample coverage wins; any newly exposed
coverage receives that sample's interpolated colour. This avoids thickening an
entire translucent sweep into an opaque streak. `extrude_fade=1` reduces the
terminal sample to zero; `0` retains its coverage.

The combined result composites the completed effect surface **once** behind the
source fill, consistent with drop shadows. Consequently an effect behind a
semi-transparent source can increase its combined alpha. That single intended
composite must not be confused with repeated per-sample opacity accumulation.
Effect-only output exposes the full sweep for separate branch compositing.

Length zero returns a transparent effect or the unchanged filled source.
No texture history, earlier frames, motion estimation or actual 3-D mesh data
is required. The near/far colour gradient is display-referred, not spectral or
scene-linear radiometry.

## Boundaries, resources and backend

Output size remains the project canvas. `style_bounds()` reports conservative
un-clipped effect extents; it does not cause the compositor to resize the canvas.
No huge image is allocated merely because a direction points outside the frame.

Lighting uses a cropped, padded alpha workspace. The existing distance-field
cap is **1,048,576 pixels including its padding**. The pre-crop and final EDT
both enforce it. Large fully covered HD/4K surfaces can exceed this cap; they
fail explicitly rather than being silently downsampled. Sparse artwork on a
larger canvas may fit. Tiled/HDR/GPU distance processing is future work.

Sweeps use a visible local rectangle capped at **4,194,304 pixels**, at most
**257 samples**, and at most **134,217,728 pixel-sample visits**. All three
checks occur before sweep float buffers or sample iteration. Full output canvas
limits remain 67,108,864 pixels. Colour compositing uses 128-row float tiles.
These are work/allocation guards, not a measured process-RSS guarantee.

Dependencies: existing Pillow and the NumPy `effect_graph` extra only.
`cpu` executes the reference. `auto` explicitly counts the style as CPU fallback;
strict `gpu` rejects it with `GPUUnsupported`. No GPU-native M6.3 execution,
GPU parity or GPU throughput is claimed. Existing GPU nodes are unchanged.

## Tests, evidence and acceptance

* `test_effect_graph_relief.py`: all six effects, hidden RGB, soft coverage,
  light reversal, ridge direction, fractional sampling, bounds, limits,
  descriptors and independent integer max-union sweep oracle.
* `test_effect_graph_relief_graph.py`: all control paths, frame-local overrides,
  forks/mattes, transition-chain preprocessing, alpha lineage and backend policy.
* `test_effect_graph_relief_project.py`: **real** Project/Compositor/native
  DanceRudiment acceptance. Each sampled animated output is compared to a fresh
  static project with that native value explicitly resolved. No guessed
  phase/pixel-difference assertion and no substitute motion driver are used.
* `test_effect_graph_relief_visuals.py` and
  `integration/build_effect_graph_m6_3_visuals.py`: twelve reproducible synthetic
  images plus a contact sheet/manifest. No private media or redistributed font.

The package verification JSON lists the exact executed subset and exclusions.
Local development uses a reconstructed graph-module snapshot, not a full clone:
the touched existing files are matched to upstream Git blob hashes. Local native
Project/DanceRudiment tests and Ruff cannot run without their unavailable runtime
packages. They remain required installer/CI gates, not counted as local passes.

The installer preflights the patch, checks hashes, uses the repository's
`scripts/Invoke-Tests.ps1`, runs new tests and the full CPU suite, rejects skipped
new/native acceptance tests, and commits only listed files after success. The
existing required GPU CI remains responsible for its actual hardware-qualified
tests. No release, tag rewrite or remote push is performed by this increment.

## Next milestone

M6.4 adds alpha-masked colour/gradient/pattern overlays and additional style blend
modes. GPU-native layer styles, texture residency, expanded bounds scheduling and
cross-vendor parity remain separate M8/production gates.

## Primary technical references

These are implementation references, not interoperability or certification claims.

* W3C Filter Effects / lighting primitives: alpha-as-height and normal/light
  operations provide the general reference. This implementation instead uses
  the project's signed-distance heights and preserves source coverage:
  https://www.w3.org/TR/filter-effects-1/#feDiffuseLightingElement
* Pillow ImageFilter for existing alpha blur/morphology operations:
  https://pillow.readthedocs.io/en/stable/reference/ImageFilter.html
* W3C compositing for premultiplied source-over semantics at graph boundaries:
  https://www.w3.org/TR/compositing-1/
