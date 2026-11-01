# FrameRudiments M8.3 — additional GPU kernels

**Increment:** v33 · **Base:** `5936741dae56b5255b27bf937c94fd31a1725177` (M8.2 merged)
**Status:** implemented CPU references, ModernGL shader paths and tests; real ModernGL execution/full CI must pass after merge.
**Interface:** existing `EffectGraph.render`, `EffectGraph.backend_plan`, `GLMatteBackend`, `effect_graph_controls`.

## Scope

The first M8.3 kernel batch closes three significant per-node GPU gaps and adds a
bounded blur/bloom family. It reuses `GLMatteBackend._input`, `_draw`,
`_readback_rgba`, the M8.2 transport contract and strict `GPUUnsupported` semantics.
Existing matte/key/spectral passes are unchanged. Every GPU call still uploads
RGBA8 inputs, executes bounded shaders, reads back a straight RGBA8 output and
releases its own textures. **No GPU-resident graph or texture reuse across nodes
is claimed (M8.4).**

| Kind | GPU style | Inputs | Draws | RGBA8 textures | Transfers | Notes |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `transition` | dissolve, directional wipe, radial | 2 | 1 | 3 | 2 up, 1 down | Exact endpoints use host identity; matte/spectral/geometric remain CPU-only |
| `chromatic_displacement` | three independent RGB vectors | 1 | 1 | 2 | 1 up, 1 down | Historical channel-wise RGB semantics and retained source alpha |
| `filter` | premultiplied Gaussian blur | 1 | 3 | 4 | 1 up, 1 down | Separable filters, final re-association |
| `filter` | brightpass Gaussian bloom | 1 | 4 | 5 | 1 up, 1 down | Threshold selection, additive luminous contribution |

All blur/bloom intermediates are RGBA8 **premultiplied** channels. They are
intentionally quantized between passes in both implementations; no implicit
linear-light conversion or HDR behaviour. Gaussian radius is a sigma in pixels,
limited to 0–4 with a maximum kernel half-width of 12. Border sampling is
explicitly `clamp` or `transparent`. Filters reject canvases over 16,777,216
pixels and dimensions over 16,384. Alpha-zero RGB is canonicalised after blur or
bloom. Current image alpha can expand under both filters while inherited
original-alpha metadata remains unchanged for downstream style selection.

`chromatic_displacement` preserves **legacy** RGB-channel semantics including
independent RGB sampling and retained alpha; it is not silently changed to the
M4 spectral node's premultiplied colour-selection contract. Zero vectors are
exact identity. For alpha-safe arbitrary band displacement use the existing
`spectral_displacement` node.

## Schema and native controls

```json
{
  "effect_graph": {
    "nodes": [
      {"id": "frame", "type": "source"},
      {"id": "shine", "type": "filter", "inputs": ["frame"],
       "filter_type": "bloom", "filter_radius": 2.0,
       "filter_threshold": 0.72, "filter_strength": 1.4,
       "filter_mix": 0.8, "filter_border": "transparent"}
    ],
    "output": "shine"
  },
  "effect_graph_controls": [
    {"node": "shine", "property": "filter_radius",
     "motion": {"kind": "named", "pattern": "circle"},
     "axis": "x", "range": [0.5, 3.0]}
  ]
}
```

| Property | Range / choices | Units | Notes |
| --- | --- | --- | --- |
| `filter_type` | `blur`, `bloom` | enum | Topology-preserving categorical control |
| `filter_radius` | 0–4 | sigma pixels | Zero blur is exact identity |
| `filter_mix` | 0–1 | fraction | Zero is exact identity |
| `filter_border` | `clamp`, `transparent` | enum | Edge policy for each Gaussian pass |
| `filter_threshold` | 0–0.99 | fraction | Bloom-only, normalised brightpass threshold |
| `filter_strength` | 0–4 | multiplier | Bloom-only; zero is identity |
| `opacity` | 0–1 | fraction | Existing universal graph output opacity |

Every active control is discoverable through `control_descriptors('shine')` and
`filter_manifest()`. The native DanceRudiment evaluator supplies frame-local
values; no DSP/motion definitions, clock or timeline are duplicated. Filter
nodes are valid unary preprocessing in transition chains.

## Validation, parity and resource rules

1. Validate node kind, input arity, fields, enums, ranges and finite numbers
   before rendering; validate resolved categorical and numeric overrides again.
2. Check texture dimensions and the estimated 2/3/4/5-texture working set **before
   uploading**. Both backend planner and executor use the same requirements.
3. The GPU does not claim to process `matte`, `spectral` or geometric transition
   styles. Strict GPU rejects those styles; `auto` accounts a CPU fallback.
4. RGB channel displacement retains the historical CPU reference for byte
   compatibility; the per-channel fractional samples use transparent borders.
5. Test transparent RGB, partial alpha, edge borders, exact endpoints, reversed
   frames, branch reuse, failed allocation and resource cleanup.
6. Compare CPU/GPU outputs at 8-bit tolerances in a real EGL-qualified CI job.
   `test_effect_graph_gpu_kernels.py` must be collected and **not skipped**.
7. HD bridge benchmarks include image uploads, all shaders and readbacks, not
   encoder, decoding, colour-management or a persistent GPU graph. Optional
   large-resolution benchmarks require separately budgeted resources.

## Acceptance gates

- [x] CPU reference + graph/chain/controller integration.
- [x] Bound shared GPU requirements, transfer counts and per-node lifetimes.
- [x] Compile and link five new GLSL programs on Mesa OpenGL 3.3 (local direct EGL probe).
- [x] Existing focused graph regression and new CPU dispatch tests.
- [x] Add real ModernGL parity, budget and combined-branch cases to the
      obligatory EGL workflow; explicitly reject zero/skip-only execution.
- [ ] Pass full native Project/Compositor/DanceRudiment tests and Ruff.
- [ ] Pass actual ModernGL EGL tests and new HD bridge benchmarks on the exact
      merged Git commit; investigate parity/platform differences if any.
- [ ] Inspect perceptual quality and profile CPU/GPU transfer overhead on real
      hardware before recommending a default backend.

**Following milestone M8.4:** GPU-resident DAG scheduling, last-consumer
release, texture pooling, shared fork ownership and quantified transfer savings.
M8.5 then expands cross-vendor parity/performance and CI media evidence.
