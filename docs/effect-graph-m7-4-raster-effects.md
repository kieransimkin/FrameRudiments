# M7.4 — deterministic raster effects

Implementation increment: v29, based on `fc140f505af8d8f61a2de58882f47f9dc55012c7`.
This is a CPU reference milestone, not a package release or a GPU qualification.

## Scope and integration

`raster_effect` is a one-input, same-canvas RGBA node in the existing `effect_graph`.
It implements six effect types: `pixelate`, `pixel_sort`, `dither`, `scanlines`,
`crt` and `glitch`. Nodes may be forked, composited with the established blend
modes, used before or after matte/chroma-key/style/spatial nodes, or supplied as
unary `transition_chain.input_effects`. No second graph executor is introduced.

M7.1–M7.2 continue to use the existing spectral colour selectors and independent
colour-band displacement. The CRT and glitch implementations here are stylised
image effects: no physical phosphor model, temporal persistence, motion estimation,
codec corruption, datamoshing or AI inference is implied.

The node consumes resolved native DanceRudiment controls. It does not infer audio,
copy a motion definition, add an oscillator, read wall-clock time, or accumulate
state between frames. A frame is a pure function of its input image and effective
parameters. Source timing, source audio, lyric timing and the existing beat map
are unchanged. All settings are optional with documented defaults; unknown fields,
nonfinite numbers, booleans in numeric fields and invalid topology fail validation.

## Minimal configuration

```json
{
  "id": "tear",
  "type": "raster_effect",
  "inputs": ["layer:native"],
  "raster_type": "glitch",
  "raster_seed": 17,
  "raster_phase": 0,
  "glitch_density": 0.3,
  "glitch_band_height": 16,
  "glitch_offset": [24, 2],
  "glitch_chromatic": 3,
  "raster_mix": 0.6,
  "opacity": 1
}
```

Use a valid isolated source name from the containing project. The supplied
`examples/effect-graph-raster-effects.json` is a project fragment, not a complete
project: merge its `effect_graph` and `effect_graph_controls` fields into an
existing project. It uses the valid final `frame` source and forks it into sorting
and CRT branches, transitions those branches, then applies optional tearing.

## Parameter contract

`raster_type` selects the effect. The same schema accepts all effect settings so a
categorical binding can switch type without changing the node's one-input topology.
Inactive settings remain validated and can be prepared in advance. Descriptors
identify active settings; absence of an active setting is not a second defaulting
system. Validation also checks joint constraints after all frame-local overrides.

### Shared settings

| Setting | Default | Accepted values / meaning |
| --- | --- | --- |
| `raster_type` | `pixelate` | The six types above |
| `raster_mix` | 1 | 0–1 processed/source mixture in premultiplied RGBA |
| `opacity` | 1 | 0–1 output opacity, applied exactly once by the graph |
| `raster_border` | `transparent` | `transparent`, `clamp`, `repeat`, `mirror`; used for CRT/glitch spatial sampling |
| `raster_seed` | 0 | Integer 0–10000; stable coordinate hash, never global RNG state |
| `raster_phase` | 0 | -10000–10000; **floor(value)** selects the discrete noise/glitch state |

`raster_phase` does not advance automatically and is not seconds or musical beats.
A native binding supplies changes. Values 2.0 through just below 3.0 share a state;
negative values use mathematical floor, so -0.1 selects -1. This is appropriate for
beat-triggered changes, not continuous fluid simulation. `scan_phase`, by contrast,
is a continuous periodic offset measured in cycles.

### Pixelation

| Setting | Default | Range / meaning |
| --- | --- | --- |
| `pixel_size` | [8, 8] | Integer X/Y cell dimensions, each 1–1024 pixels |
| `pixel_origin` | [0, 0] | Integer X/Y cell-grid origin, each -4096–4096 pixels |
| `pixel_alpha` | `preserve` | `preserve` or `average` |

Each cell is the exact area mean of the available pixels inside the canvas. Edge
cells use their actual area, not imagined black padding. RGB is alpha-weighted:
zero-alpha RGB cannot pollute the visible cell colour. `preserve` changes colour
while retaining every source alpha value. `average` also averages coverage and
original-alpha metadata over the cell. Cell grids can move independently of the
source and their origin supports negative coordinates.

A 1×1 cell is an exact byte-preserving pass-through. Arbitrarily small opacity
still contributes proportionally to a cell's colour; source RGB is not discarded
using a hard threshold. Accumulation is in row strips, not a full-frame float
integral image. This avoids an unbounded intermediate as the canvas grows.

### Pixel sorting

| Setting | Default | Range / meaning |
| --- | --- | --- |
| `sort_axis` | `x` | `x` sorts rows; `y` sorts columns |
| `sort_key` | `luminance` | `luminance`, `brightness`, `red`, `green`, `blue`, `alpha` |
| `sort_reverse` | false | Stable descending rather than ascending order |
| `sort_low`, `sort_high` | 0, 1 | Inclusive key interval, each 0–1; low must not exceed high |
| `sort_span` | 256 | Integer 2–4096, maximum independently sorted run chunk |
| `sort_min_run` | 2 | Integer 2–4096, no larger than `sort_span` |
| `sort_alpha_floor` | 1 | Integer 1–255; fully transparent pixels are always excluded |

Eligibility is evaluated on the current input, before any permutation. Every
transparent or ineligible pixel splits a run; the implementation never reaches
across that gap. Eligible runs are split at `sort_span`. Chunks shorter than
`sort_min_run` are unchanged. Stable sorting preserves ties, including descending
order. Entire RGBA pixels and original-alpha lineage use **the same permutation**.
The alpha is not sorted independently and threshold values are never changed by
hidden colour in excluded pixels.

Here `luminance` is the display-referred weighted RGB key
`0.2126 R + 0.7152 G + 0.0722 B`, not an assertion that the entire graph is
scene-linear. `brightness` is maximum RGB. Sorting is bounded but content-dependent;
a worst-case fragmented image need not match the synthetic benchmark throughput.

### Dither and quantisation

| Setting | Default | Range / meaning |
| --- | --- | --- |
| `dither_pattern` | `bayer4` | `none`, `bayer2`, `bayer4`, `bayer8`, `noise` |
| `dither_levels` | 4 | Integer 2–256 levels per channel |
| `dither_strength` | 1 | 0–1 threshold perturbation; zero still quantises |
| `dither_space` | `srgb` | `srgb` or `linear_srgb` |
| `dither_monochrome` | false | Quantise a weighted monochrome value rather than independent channels |
| `dither_origin` | [0, 0] | Integer X/Y phase origin, each -4096–4096 pixels |

Bayer thresholds use the standard recursive 2×2 ordering. `noise` uses deterministic
coordinate hashing, not blue noise, error diffusion or temporally accumulated
noise. `none` means plain nearest-level quantisation. Strength zero is not an
identity; use `raster_mix: 0` to bypass the node exactly. The source alpha is
unchanged. Black and white endpoints remain endpoints.

For `linear_srgb`, sRGB is decoded before monochrome weighting and quantisation,
then re-encoded for the established display-referred graph. This option neither
adds HDR formats nor silently changes the graph's working colour space. The
number of display-encoded levels need not be uniformly spaced in this mode.

### Scanlines

| Setting | Default | Range / meaning |
| --- | --- | --- |
| `scan_axis` | `y` | `y` gives horizontal lines; `x` gives vertical lines |
| `scan_spacing` | 4 | 1–1024 pixels, fractional values accepted |
| `scan_width` | 0.5 | 0–1 dark interval as a fraction of one period |
| `scan_strength` | 0.5 | 0–1 attenuation within the dark interval |
| `scan_phase` | 0 | -10000–10000 cycles, periodic and continuous |

Each pixel receives its analytically integrated overlap with the dark interval,
not an unfiltered binary stripe lookup. This protects fractional spacing and
phase from avoidable sampling flicker. Scanlines darken RGB, never punch holes in
alpha. Width zero or strength zero is an exact identity. Width one attenuates the
whole image; it does not create an undefined periodic function.

### CRT treatment

| Setting | Default | Range / meaning |
| --- | --- | --- |
| `crt_mask` | `rgb` | `none`, `rgb`, `bgr` aperture stripe order |
| `crt_pitch` | 1 | 1–32 pixels per channel stripe |
| `crt_mask_strength` | 0.35 | 0–1 suppression of off-channel stripes |
| `crt_curvature` | 0.08 | 0–1 inverse barrel coordinate displacement |
| `crt_vignette` | 0.3 | 0–1 radial RGB darkening |
| `crt_gain` | 1 | 0–4 RGB gain, clamped to the 8-bit working range |
| `crt_noise` | 0 | 0–1 seeded monochrome noise strength |

CRT also uses the shared scanline settings. Processing order is inverse geometry
and premultiplied sampling, scanline attenuation, integrated aperture mask,
vignette, seeded noise, gain/clamp, then premultiplied source/effect mix. Aperture
masks and shading preserve sampled coverage; geometry alone may move coverage.
There is no phosphor decay or multi-frame memory. With mask `none`, pitch and mask
strength are explicitly inactive in discovery metadata.

### Glitch treatment

| Setting | Default | Range / meaning |
| --- | --- | --- |
| `glitch_band_height` | 16 | Integer 1–1024 pixels per band |
| `glitch_density` | 0.3 | 0–1 band-selection probability |
| `glitch_offset` | [32, 0] | Signed X/Y maximum displacement, each -4096–4096 pixels |
| `glitch_chromatic` | 0 | 0–64 pixels of symmetric red/blue separation in selected bands |

Band identity, selection and signed displacement come from a defined integer
hash of band index, seed and phase state. The same parameter values reproduce
the same pattern regardless of previous frames or row-tile size. Green follows
the base sampled coordinate; red and blue can diverge. Coverage is the maximum
of the sampled channel coverages, so colour separation remains bounded without
repeatedly accumulating opacity. Original-alpha metadata uses the corresponding
maximum of the same footprints. Fully transparent source colours never contribute
visible colour. This is a spatial glitch, not an encoded-video corruption effect.

## Alpha, mixing, lineage and mutability

The public kernel is `render_raster(image, config)`. Graph-aware callers use
`render_raster_with_alpha(..., original_alpha=...)` and receive `(image, alpha)`.
`original_alpha`, when supplied, must be a same-size L-mode image. The RGBA and
alpha outputs are new objects; no source image or authored configuration is mutated.

Spatial sampling, pixel-cell averaging and partial `raster_mix` use premultiplied
colour. Unassociation occurs only at output. Once quantised, zero-alpha processed
pixels are canonicalised to zero RGB. Exact identity shortcuts preserve *all*
original bytes, including any hidden source RGB, because they do not filter it.

Original-alpha transport is effect-specific:

- Pixelate-preserve, dithering and scanlines retain the inherited silhouette.
- Pixelate-average applies the same cell average to lineage.
- Pixel sorting transports lineage with its colour-derived permutation.
- CRT/glitch transport lineage with the visible sampling footprint.

The graph applies overall output `opacity` once after effect processing. Lineage
is not silently reset to the faded result. Subsequent original-alpha styles and
mattes therefore use the transported original silhouette. Separate pre/post matte
and chroma-key nodes remain the explicit processing-order mechanism. No implicit
matte ordering or separate clock is introduced.

## Native controls and discovery

`EffectGraph.control_descriptors(node_id)` supports raster nodes, and
`EffectGraph.raster_manifest()` returns a detached inventory for reachable raster
nodes. Descriptors provide the configured value, factory default, numeric limits,
units, type, interpolation rule and active/inactive status. All controls are
animatable. Integer settings are strictly integral in authored JSON; continuous
native values are mapped to integer controls using nearest, ties toward positive
infinity. Categorical choices use the existing native categorical evaluator.

Examples of addresses:

```text
raster_mix
raster_phase
pixel_size.x
sort_low
sort_key
dither_levels
scan_phase
crt_curvature
glitch_offset.x
glitch_chromatic
opacity
```

Numeric ranges fit within the existing native modulation interval contract.
There is no substitute motion driver or modification to its smoothing, lag,
transfer curves, repeat clock or combination rules. Pair-dependent constraints
(`sort_low <= sort_high`, `sort_min_run <= sort_span`) are checked after all
per-frame values are applied, so dictionary insertion order is not significant.

## Resource and backend contract

No new dependency is introduced. Install the existing `.[dev,effect_graph]`
environment (Pillow, NumPy, released DanceRudiments and the test tools).

The node accepts at most 33,177,600 pixels (8K UHD count), with no dimension over
16,384. Floating-point spatial work is tiled to at most 262,144 pixels. The public
test/diagnostic tile override accepts 1–256 rows and is further limited by pixel
count. Pixel averaging accumulates bounded strips, and sorting operates on one
line and at most 4096 pixels per sorted chunk. Full-size output and uint8 input
copies still consume memory; this is not a promise of a fixed total RAM ceiling.
There is no automatic resolution reduction or canvas expansion.

CPU is the only implementation in this increment. `auto` increments the existing
CPU-fallback counter if a GPU backend is present. Strict `gpu` mode rejects the
node explicitly, including identity configurations; it does not count CPU work
as GPU execution. No GPU allocation or driver is needed to validate configuration.
Real GPU kernels, parity and backend-wide texture residency remain M8 work.

## Testable deliverables and acceptance

Implemented files cover kernel tests, graph controls and forks, alpha-lineage
transport, topology validation, original-image immutability, determinism under
arbitrary call order, tile independence, resource guards, stable sorting ties,
quantisation endpoints, fractional scanlines and CPU/GPU declaration behaviour.
The real-project tests use Project, Compositor and the installed DanceRudiments
runtime on synthetic assets; no replacement driver is provided. Their sample
positions include sub-beat frames to avoid repeatedly selecting identical phases.

The visual generator produces sixteen actual reference renders and a contact
sheet. It records pixel hashes and can be run twice for reproducibility evidence:

```sh
python integration/build_effect_graph_m7_4_visuals.py fresh-qa-directory
python integration/benchmark_effect_graph_raster.py --width 1920 --height 1080 --repeats 3 --output fresh-benchmark.json
```

Outputs must be fresh. A disposable create/read/rename/remove preflight checks
write permissions first. The benchmark measures kernel calls including allocations,
not decoding, composition, file output or encoding; it records runtime versions,
configurations and hashes. It does not measure peak RAM or establish real-time
performance. Optional larger-resolution measurements must be treated separately.

Required project acceptance uses the managed `scripts/Invoke-Tests.ps1` runner:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Invoke-Tests.ps1 -q `
  tests/test_effect_graph_raster.py tests/test_effect_graph_raster_graph.py `
  tests/test_effect_graph_raster_visuals.py tests/test_effect_graph_raster_project.py
```

Then run Ruff and the complete CPU/font/FFmpeg suite with no skipped tests, plus
the existing separate EGL-qualified GPU workflow. A passing reconstructed-module
suite is useful development evidence, not a complete project or Windows acceptance.
No package release, tag, deployment or model download is authorised by this patch.

### Remaining gates

- [x] CPU kernels, discovery, native-binding addresses and existing DAG routing.
- [x] Unit/graph/alpha/sampling/resource and deterministic visual tests.
- [x] Actual synthetic comparison and reproducible benchmark tooling.
- [ ] Eight real Project/Compositor/native-DanceRudiments acceptance cases executed.
- [ ] Full repository Ruff, CPU/font/FFmpeg tests and exact merged-commit CI.
- [ ] Cross-platform benchmarks and optional 4K/8K performance evidence.
- [ ] GPU implementation/parity and texture-resident execution (M8).

Next implementation milestone: M7.5 procedural texture/mask/control-field nodes.
These should feed existing raster/spatial/matte/transition inputs, not create a
second compositor, replace the native clock, or duplicate the existing spectral
selectors. M7 is not closed by this increment alone.
