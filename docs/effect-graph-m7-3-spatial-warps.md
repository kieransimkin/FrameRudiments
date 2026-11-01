# M7.3 — Reusable spatial warps

**Increment:** v28, 1 November 2026. **Base:** `4d0aa3e3928997e4544ac10a549258826268cd52` (v27).
**Status:** CPU implementation; actual Project/native-DanceRudiments and full CI acceptance remain separate gates.

## Scope and reuse

M7.1–M7.2's colour selection, recolouring and fractional spectral displacement already
exist in the M4 modules. They are reused, not reimplemented. This increment adds
`spatial_warp`, a normal effect-graph node for sustained spatial effects, independent
of transition progress. It works before/after styles, keys and mattes; in forks and
composites; and as unary transition-chain input preprocessing. The source remains a
frame-sized RGBA image. A displacement map is another normal graph output.

The renderer imports the existing geometric-transition transparent RGBA sampler and
rotation helper, and the existing overlay periodic/clamped texture sampler. Their
algorithms and legacy call paths are unchanged. The new code supplies coordinate
fields and the corresponding single-channel alpha-lineage resampling.

No new motion clock, audio analysis, model weights, file loading, shader compiler,
network access or simulation history is introduced. Native DanceRudiment bindings
continue to own source timing, beat maps, curves, lag and smoothing. Phase is an
ordinary controllable parameter, not an internally accumulating time counter.

## Node contract

```json
{
  "id": "wave",
  "type": "spatial_warp",
  "inputs": ["layer:native"],
  "warp_type": "wave",
  "warp_amplitude": [12, 6],
  "warp_scale": [96, 72],
  "warp_phase": 0,
  "warp_border": "transparent",
  "warp_mix": 1,
  "opacity": 1
}
```

The type needs one image input except `displacement`, which needs exactly two:
`[image_to_warp, displacement_map]`. Inputs must have identical dimensions and RGBA
mode. A map may be a video/visualiser surface, gradient overlay, keyed branch or any
other graph output. It is not an external filename and does not bypass source validation.
Unknown node fields, invalid enums, booleans masquerading as numbers, NaN, infinity,
out-of-range values and input-arity mismatches fail explicitly.

### Effects

| `warp_type` | Meaning | Principal controls |
| --- | --- | --- |
| `wave` | Independent sinusoidal inverse X/Y displacements | amplitude, scale, phase |
| `turbulence` | Seeded coherent value-noise vector field, up to six octaves | amplitude, scale, phase, seed, octaves |
| `swirl` | Local polar twist, smoothly falling to zero at its radius | centre, angle, radius |
| `radial` | Pinch/bulge using a radial power mapping inside a radius | centre, radius, amount |
| `mirror` | Mirror the left/top source half about the chosen centre | centre, axis |
| `kaleidoscope` | Repeated reflected angular sectors | centre, angle, segments |
| `polar_unwrap` | Sample circular source content into angle/radius output axes | centre, radius, angle |
| `polar_wrap` | Wrap a rectangular angle/radius texture around a centre | centre, radius, angle |
| `displacement` | Use selectable map channels as signed inverse displacements | amplitude, map channels, neutral |

These are 2D image warps, not fluid simulation, optical flow or mesh deformation.
Turbulence uses integer hashing and quintic lattice interpolation; it does not read
or modify NumPy's global random state. Changing its phase translates/evolves the
sampled field. Changing seed is intentionally discontinuous.

## Coordinates and alpha

Pixel centres run from `(0, 0)` through `(width-1, height-1)`. A normalized centre
`[cx, cy]` becomes `[cx*width-.5, cy*height-.5]`. Wave scales are wavelengths in
physical pixels. Positive displacement samples from a lower source coordinate, so
features move right/down. `warp_angle` is degrees; `warp_phase` is radians.

Polar unwrap puts angle on output X and radius on output Y. The angle parameter
positions the seam. Polar wrap reads source X as angle and source Y as radius.
Wrapping/unwrapping are resampling operations, not byte-exact inverses. General
bilinear sampling is not an antialiasing solution for extreme minification; use
the project's quality controls and inspect the final render.

RGBA interpolation is premultiplied. `warp_mix` blends original and warped RGBA in
premultiplied space; universal node `opacity` is applied once by EffectGraph after
the warp. Hidden colour in fully transparent source pixels cannot contribute to
visible samples. After quantization, zero-alpha outputs also have RGB zeroed.
Zero mix and documented neutral-transform shortcuts return exact copies, including
otherwise hidden source RGB, to preserve the explicit identity contract.

**Original-alpha lineage follows the same inverse coordinate map and mix.** A
source that was dimmed or keyed before the warp retains its original alpha as
separate metadata, but in the new geometry. A downstream original-alpha outline,
matte or glow therefore follows the warped silhouette, not its old position.
Current source alpha is never replaced merely because lineage exists. Forks keep
independent results; the second displacement-map input does not become lineage.

### Displacement-map encoding

Map channels may be `r`, `g`, `b`, `a` or `luminance`. The default uses R for X and G
for Y, with `warp_neutral = 128/255`; byte 128 therefore means exactly zero movement.
For a channel value `c`, neutral `n` and map alpha `a`, normalized displacement is:

```
d = a * ((c-n)/(1-n) if c >= n else (c-n)/n)
sample_x = output_x - warp_amplitude.x * d_x
sample_y = output_y - warp_amplitude.y * d_y
```

Thus channels 0 and 255 span negative/positive full amplitude. Map alpha reduces
displacement, and an invisible map is neutral regardless of its hidden RGB. This
is a normalized control-image encoding, **not** physical flow vectors or scene-linear
colour. Luminance uses the existing coefficients `.2126/.7152/.0722` on the encoded
RGB channels. A and RGB cannot be confused silently by changing the input format.

### Border modes

`transparent` samples zero outside the image. `clamp` repeats edge texels. `repeat`
wraps each bilinear tap to the other side. `mirror` reflects each tap, duplicating
edge texels at the reversal. These are sampler border policies, distinct from the
`mirror` warp's geometry. One-pixel-wide/tall surfaces have defined behaviour.

## Controls and discovery

`EffectGraph.control_descriptors(node_id)` now accepts `spatial_warp` as well as
layer styles. It returns detached configured values and factory defaults, ranges,
units, active flags, interpolation modes and topology-filtered categorical choices.
`EffectGraph.spatial_manifest()` describes reachable spatial nodes, including
expanded transition-chain IDs, with explicit CPU-only capability information.
Existing M6 style reports retain their original scope and schema.

| Control | Range/default | Units |
| --- | --- | --- |
| `warp_mix` | 0..1 / 1 | ratio |
| `warp_amplitude.x`, `.y` | -4096..4096 / 16,16 | pixels |
| `warp_scale.x`, `.y` | 1..8192 / 64,64 | pixels |
| `warp_center.x`, `.y` | 0..1 / .5,.5 | normalized |
| `warp_angle` | -720..720 / 90 | degrees |
| `warp_radius` | .5..8192 / 128 | pixels |
| `warp_amount` | -2..2 / .5 | radial exponent control |
| `warp_phase` | -64π..64π / 0 | radians |
| `warp_octaves` | integer 1..6 / 3 | count |
| `warp_seed` | integer 0..10000 / 0 | seed |
| `warp_segments` | integer 2..32 / 6 | count |
| `warp_neutral` | 1/255..254/255 / 128/255 | channel ratio |
| `warp_type`, `warp_border`, `warp_axis`, `warp_map_x`, `warp_map_y` | enums | discrete |
| `opacity` | 0..1 / 1 | output alpha multiplier |

Integer authored settings must be integers. Numeric modulation rounds integer
controls to the nearest integer after range checking. Other fields use continuous
values. All ranges fit the native driver's existing ±10,000 limit; no new range
adapter or replacement driver is needed. Explicit `choices` bindings must preserve
input count. A unary wave cannot become a two-input displacement node at runtime.
Dormant settings remain valid and discoverable, but are marked inactive.

Example native binding:

```json
{
  "node": "wave", "property": "warp_phase",
  "motion": {"kind": "named", "pattern": "circle"},
  "axis": "x", "range": [0, 6.283185307179586]
}
```

See `examples/effect-graph-m7-spatial.json`. It warps the native layer, forks it into
an original-alpha glow and a sharp branch, then composites the two. Transition
chains use stable targets such as `chain::input::0::wave` without another executor.
A two-input displacement node should be authored explicitly before the chain;
unary preprocessing cannot provide its second input.

## Resources, backend and failure behaviour

Canvas and map dimensions are capped at 33,177,600 pixels (8K UHD), with a 16,384-pixel
maximum on either axis. Floating working arrays are row-tiled, with at most 262,144
output pixels per tile; tile rows are configurable only through the Python test API.
Source/lineage arrays remain uint8 and output images remain full canvas-sized Pillow
images. This is not a promise that the entire process fits in a fixed number of MB:
source, output, graph forks and Pillow/NumPy copies still require memory.

The canvas is never expanded automatically, and content warped beyond a transparent
border can disappear. General output bounds must be measured from the resulting
alpha; no un-clipped analytic bound is claimed for polar/noise fields.

The implementation is CPU-only. `auto` increments the existing CPU-fallback count.
Strict GPU rejects the node even if its configured mix is zero; it never labels CPU
work as GPU execution. There are no new GPU shaders or GPU performance claims.
Existing M3/M4 parity workflows are preserved.

## Tests and acceptance

Focused tests cover analytic integer/fractional displacement, channel routing,
transparent control maps, border footprints, original-alpha transport, arbitrary
seek order, global RNG isolation, seed changes, tile-size equivalence, graph forks,
chain expansion, immutable nested controls, exact zero mix, error paths and budgets.
The actual 10-panel generator also has a pixel-reproducibility test. Ten real
Project/Compositor/DanceRudiments cases are provided separately and are not replaced
by a test oscillator. They sample sub-beat instants and verify resolved control
variation before asserting image variation.

- [x] CPU spatial node and native control addresses.
- [x] Original-alpha geometry follows the warp; current alpha remains independent.
- [x] Documented schema, capability discovery and executable example.
- [x] Focused local Pillow/NumPy and reconstructed graph regression coverage.
- [ ] Full repository, Ruff, real native-project and Windows acceptance.
- [ ] GPU-native warp kernels and CPU/GPU parity (M8).
- [ ] Hardware throughput and cross-platform numerical evidence.

Use `scripts/Invoke-Tests.ps1` for project tests. The supplied v28 installer invokes
that managed runner and refuses to commit if lint, native cases or the full CPU
suite fails or skips acceptance cases. The patch is not a package release or
publication and does not alter existing tags or dependencies.

Next: M7.4 pixel sorting, CRT/glitch/scanline and pixelation/dithering, followed by
M7.5 dedicated procedural texture and control-field nodes. The new map-input
contract provides an integration point for those fields without duplicating the
renderer or clock.
