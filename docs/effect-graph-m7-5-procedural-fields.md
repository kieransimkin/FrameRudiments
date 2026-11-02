# M7.5 — procedural textures, masks and control fields

Implementation increment **v30**, based on `8d638a143b3e19823d22e9b3287f739e8f94c49b`.
This is a CPU implementation, not a package release, GPU qualification or completed
full-project acceptance. No new runtime dependency is introduced.

## Purpose and integration

`procedural_field` generates a same-canvas RGBA surface in the existing effect DAG.
One mandatory reference input supplies dimensions and optionally alpha coverage.
Its RGB is never used. This keeps the current compositor's source validation and
canvas contract: there is no invented external source provider or separate render
loop. The default `opaque` coverage can generate visible content even when the
reference is transparent. Input references can be any already-composited branch.

The result can be used as an ordinary visible texture, an alpha/luminance matte,
a three-input transition mask, a live pattern-overlay texture, or a two-channel
spatial displacement map. Independent branches share immutable inputs. Unary
transition-chain preprocessing accepts the new node and retains stable generated
IDs, for example `chain::input::0::field`.

M7.3's existing `_noise` function supplies deterministic quintic value noise.
M6.4's existing stop validation, control addresses and premultiplied palette
interpolation supply colour mapping. The established `spatial_warp` displacement,
matte and transition engines consume the result without alternative renderers.
No audio analysis, motion definition, oscillator, wall clock or mutable RNG is
introduced. Animation only comes from resolved native DanceRudiment bindings.
Saved recording axes, including frequency-band lanes, use that same adapter.

```json
{
  "id": "flow",
  "type": "procedural_field",
  "inputs": ["frame"],
  "field_type": "fbm",
  "field_output": "vector",
  "field_vector": "curl",
  "field_scale": [96, 96],
  "field_vector_gain": 32,
  "field_seed": 7
}
```

Connect `flow` as the second input of a `spatial_warp` node with
`warp_type: "displacement"`. Use `warp_map_x: "r"`, `warp_map_y: "g"` and the
existing default `warp_neutral: 128/255`. Changing `field_phase` moves the noise;
changing `warp_amplitude` controls the visible displacement in pixels. Opacity of
the generated vector map scales its influence through the existing warp engine.

## Scalar generators

| `field_type` | Definition |
| --- | --- |
| `constant` | Uniform `field_value`; useful for neutral maps, controls and test masks. |
| `linear` | Ramp along transformed X, offset by one half and the authored phase. |
| `radial` | Distance from the transformed centre, plus phase. |
| `angular` | `atan2(v,u)/(2*pi)+0.5`, plus phase. The centre uses `atan2(0,0)=0`. |
| `stripes` | Periodic centred bands along transformed X, with duty and feather. |
| `rings` | The same band function applied to radial distance. |
| `checker` | Soft exclusive-or combination of periodic X and Y bands. |
| `noise` | Existing seeded quintic-interpolated value noise, mapped into 0..1. Not Perlin/simplex noise. |
| `fbm` | Normalised weighted octave sum of value noise. |
| `ridged` | Normalised weighted sum of `1-abs(noise)` per octave. |
| `cellular` | Normalised nearest-feature distance in a seeded jittered square lattice. |
| `julia` | Smooth normalized Julia escape time. The complex constant can orbit its authored centre on a normalized `2*pi` phase. |

Cellular features are restricted to the central half of each cell. The farthest
point from a feature in its own cell is at most `sqrt(2)*0.75`, less than the
minimum 1.25 distance to a feature beyond the immediate neighbouring cells.
Consequently the bounded 3x3 search is sufficient. Tests compare it with a larger
7x7 reference search. This restriction is intentional and is not an unrestricted
Worley distribution.

Pixel centres are `(x+0.5,y+0.5)`. Subtract the normalised centre and pixel offset,
divide by independent X/Y scales, then rotate by `field_angle`. Because scale is
applied before rotation, radial scalar fields are invariant under angle changes,
including when X/Y scales differ. Positive phase offsets ramp coordinates; for
noise/cellular fields it translates the procedural domain by `(phase,phase*phi)`
where `phi=0.61803398875`. It is neither seconds nor an automatically advancing
beat. Negative phase and offset are supported.

`field_repeat` controls ramp wrapping for linear, radial and angular generators.
It does not promise seamless noise boundaries. Checker/stripe/ring fields have
their own explicit period. Soft band edges are artistic feathering, not a claim of
pixel-integrated antialiasing at arbitrarily high frequencies. The existing render
quality system remains responsible for project-level supersampling.

## Four explicit output contracts

| `field_output` | RGB | Alpha before universal graph opacity |
| --- | --- | --- |
| `grayscale` | Scalar repeated in R/G/B | Selected reference coverage |
| `alpha` | White wherever visible | Scalar times selected coverage |
| `color` | Shared palette interpolation at the scalar | Palette alpha times selected coverage |
| `vector` | Signed vector encoded in R/G; B=128 | Selected reference coverage |

Use `grayscale` with a **luminance** matte or transition. Use `alpha` with an
**alpha** matte. An alpha-mask output is white RGB rather than grayscale RGB, so
consumers that multiply luminance by alpha do not inadvertently square the field.

`field_alpha_source` is `opaque` (default), `current` or `original`. Original alpha
selects the input branch's retained source-alpha metadata, including its prior
spatial transport. Direct API callers that do not provide original metadata fall
back to current alpha, consistent with existing effect functions. Opaque mode
ignores reference alpha entirely. These choices are independently modulatable.

**A generator starts a new alpha lineage.** Its output alpha, after the node's
universal output opacity, becomes the silhouette inherited by downstream original-
alpha styles. It does not retain an unrelated reference silhouette. This is tested
with transparent references, generator output, a subsequent fade and an original-
alpha effect-only colour style.

The generator canonicalises RGB where its quantised alpha becomes zero. Universal
graph opacity follows the existing graph contract and is applied exactly once;
the direct `render_procedural_field` helper does not apply that node-level opacity.
Palette colour interpolation is premultiplied, including in linear-sRGB and OKLab
modes, before conversion to the existing display-referred RGBA output. The graph
as a whole is not switched to linear light or HDR.

## Vector fields

`field_vector` selects:

- `independent`: two scalar evaluations at decorrelated coordinates/seeds, each
  mapped from 0..1 to -1..1.
- `gradient`: screen-X and screen-Y central finite differences of the remapped
  scalar field. The sampling step is in output pixels and accounts for scale and
  rotation in the coordinate transform.
- `curl`: the same derivatives arranged as `(dF/dy,-dF/dx)`.

Multiply by `field_vector_gain`, clamp each component to -1..1, then encode with
`128+127*v` for nonnegative components and `128+128*v` for negative components.
R and G therefore represent exact byte-128 neutrality, with full range 0..255.
The existing warp's asymmetric neutral decoding restores these signed values.
A constant potential has exactly neutral gradient/curl output. Zero vector gain
is exactly neutral irrespective of the chosen generator. B is reserved at 128.

The finite-difference curl resembles a flow field; it is not a fluid simulation.
Clipping, discontinuous scalar patterns and 8-bit encoding mean no exact
continuous divergence-free property is claimed. Large derivatives can saturate;
reduce gain, increase softness or use smoother procedural inputs.

## Parameter and native-control contract

Every creative scalar/vector component, enum and palette component has a stable
control address. Authoring dimensions, stop IDs and stop count are not animated.
All ranges validate both authored configuration and fully resolved per-frame
configuration. Numeric fields reject booleans, NaN and infinity. Authored integer
fields require integers; native continuous overrides round to the nearest integer.

| Parameters | Defaults | Ranges / units |
| --- | --- | --- |
| `field_value` | 0.5 | 0..1 |
| `field_scale.x/y` | 64,64 | 0.25..8192 output pixels per field unit |
| `field_center.x/y` | 0.5,0.5 | Normalised 0..1 canvas coordinates |
| `field_offset.x/y` | 0,0 | -4096..4096 output pixels |
| `field_angle` | 0 | -720..720 degrees |
| `field_phase` | 0 | -10000..10000 procedural domain units |
| `field_seed` | 0 | Integer 0..10000 |
| `field_octaves` | 3 | Integer 1..6 |
| `field_lacunarity` | 2 | 1..4 frequency multiplier per octave |
| `field_persistence` | 0.5 | 0..1 amplitude multiplier per octave |
| `field_duty` | 0.5 | 0..1 band duty |
| `field_softness` | 0.1 | 0..1 band edge feather fraction |
| `field_jitter` | 1 | 0..1 of the bounded cellular jitter |
| `field_contrast`, `field_bias` | 1,0 | 0..8 and -1..1 |
| `field_gamma` | 1 | 0.1..8 exponent |
| `field_invert` | false | Boolean category |
| `field_vector_gain` | 1 | 0..64; 16..32 can be useful for low-frequency derivatives |
| `field_derivative_step` | 1 | 0.25..32 output pixels |
| `field_julia_c.x/y` | -0.8,0.156 | Base complex constant, each -2..2 |
| `field_julia_orbit_radius` | 0 | 0..2 around the base constant |
| `field_julia_phase` | 0 | Normalized turn -1..1; endpoints are the same `2*pi` phase |
| `field_julia_zoom` | 1 | 0.05..32; larger values zoom in |
| `field_julia_rotation` | 0 | Normalized turn -1..1 |
| `field_julia_iterations` | 96 | Integer 1..512 |
| `field_julia_escape_radius` | 4 | 2..64 |
| `field_julia_power` | 2 | Integer 2..8 |
| `opacity` | 1 | Universal graph output opacity, 0..1 |

Scalar remapping order is contrast about 0.5, bias, clamp to 0..1, gamma exponent,
then optional inversion. It precedes palette mapping or vector derivative
calculation. `field_repeat` defaults to `clamp`; other choices are `repeat` and
`mirror`. Default output is `grayscale`, default generator is `noise`, and default
vector mode is `curl`.

`field_palette` reuses the 2..16-stop schema with unique stable lowercase IDs,
strictly increasing positions, opaque CSS RGB colours and independent opacity.
Default stops are black `start` at 0 and white `end` at 1. The interpolation space
is `field_color_space: srgb | linear_srgb | oklab`; interval shape is
`field_interpolation: linear | smoothstep | constant`.

Stable addresses include `field_palette.hot.position`,
`field_palette.hot.color.g` and `field_palette.hot.opacity`. Independent stop
updates are collected first, then jointly validated, so a valid simultaneous
move is not rejected merely because one intermediate update crossed its neighbour.
No automatic stop sorting changes authored identity.

Julia colour uses this same palette path; there is no fractal-specific colour
interpolator. See [Julia fractal configuration, native motion and actual output](julia-fractals.md).

Use `EffectGraph.control_descriptors(node_id)` for detached value/default/range,
units, interpolation and active-setting metadata. Custom stop IDs have no global
factory default (`null`) but retain their configured value. Use
`EffectGraph.field_manifest()` for the reachable generator inventory and resource,
channel and backend contracts. This is a static inventory, not a claim that a
project frame or GPU kernel executed. Unknown fields on other node types are
rejected rather than silently accepted.

## Dependencies, bounds and execution policy

No extra dependency beyond the existing `effect_graph` extra: Pillow and NumPy.
The new module retains lazy NumPy imports. Normal native bindings remain owned by
DanceRudiments; source clocks, recordings, audio and captions are unchanged.

One reference input is mandatory for every variant, so categorical changes never
alter topology. The output is fixed to the reference canvas; it does not expand,
downsample or allocate a new independently controlled resolution. Limits are:
33,177,600 pixels; 16,384 pixels per dimension; 262,144 pixels per float work tile;
1,000,000,000 conservative weighted scalar-sampling work units. Default tiles are
48 rows, additionally reduced by width. The caller may use 1..256 rows for tests;
changing tile size must not change output pixels.

Work estimates account for octave count, cellular neighbourhoods and the two/four
scalar evaluations needed for vector modes. Limits are checked before allocating
the generated output. Some expensive 8K combinations exceed the work limit and
are rejected explicitly. Limits are not measured peak-RAM claims. The source,
output and optional coverage image still require full-canvas 8-bit storage.

All execution is CPU. Strict `gpu` rejects the node, including constant/transparent
variants; `auto` records a CPU fallback. No shader, hardware speedup, GPU-resident
surface or cross-frame caching is claimed. Generated values are stateless pure
functions of canvas/reference coverage and resolved controls. Seed selection does
not touch the Python or NumPy global random generators.

## Examples, evidence and tests

`examples/effect-graph-procedural-fields.json` is a project fragment using the
existing `frame` source, noise mask, curl displacement field and downstream glow.
Merge its graph/control fields into an existing complete project. All referenced
node/control IDs are exercised by a focused test.

Generate actual synthetic evidence (use a fresh output directory):

```sh
python integration/build_effect_graph_m7_5_visuals.py out/field-qa
python integration/benchmark_effect_graph_fields.py --width 1920 --height 1080 --repeats 3 --output out/field-benchmark.json
```

The contact sheet contains actual generated pixels and downstream consumer output,
not illustrative stock images or model-generated art. Synthetic test media only;
no private song, font binary or real-user media is distributed. Perceptual approval
remains a separate attended activity.

Focused tests cover every generator/output pair, tiny canvases, tile independence,
hidden-RGB independence, alpha semantics, analytic gradients, cellular reference
search, shared noise identity, palette transparency, signed vector encoding,
control discovery/immutability, joint stop validation, resource failures and DAG
integration. Consumer tests exercise matte, transition, pattern-overlay, spatial
warp, alpha-lineage reset and transition-chain preprocessing. A repeated visual
build compares every rendered image hash and contact-sheet bytes.

The separate real-project acceptance module uses actual `Project`, `Compositor`,
named native motion and the demo's saved four-dimensional recording. It tests
sub-beat phase variation, frequency-lane control, categorical/palette controls,
random-access rendering, chain preprocessing, temporal supersampling and legacy
identity. It does not substitute a synthetic motion driver.

### Acceptance gates

- [x] CPU generator implementation and existing graph/control integration.
- [x] Focused numerical/consumer regressions and reproducible rendered evidence.
- [x] Incremental patch against complete hash-verified edited baseline files.
- [ ] Execute real-project/native acceptance and full repository Ruff/test matrix.
- [ ] GPU kernels, cross-vendor parity and resource/performance acceptance (M8).

The delivered verification JSON identifies the actual snapshot/test environment
and omissions. Passing snapshot tests is not full-repository or release acceptance.
The next implementation area is **M8.1: backend capability registry and explicit
surface/resource contracts**, followed by GPU execution and bounded surface reuse.
