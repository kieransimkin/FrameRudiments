# M3.3 — ModernGL matte and chroma-key acceleration

**Status:** implemented CPU↔GPU node bridge; real GPU acceptance pending dedicated CI.
**Baseline:** v15 alpha-lineage effect graph (v14 is insufficient).
**Dependencies:** existing `pip install -e ".[gpu,dev]"` (ModernGL 5.11+, NumPy 1.26+), OpenGL 3.3 context (headless EGL on Linux). No new required base dependencies.

## Project API

`framerudiments.project.v1` gains an optional `effect_graph_backend` field only when `effect_graph` is present:

- `cpu` (default): existing Pillow/NumPy reference; byte-identical legacy behaviour.
- `gpu`: require an OpenGL 3.3 context, offscreen GPU processing for matte/chroma nodes, **raise** on unsupported GPU capabilities/settings. Other graph nodes remain CPU until full GPU graph scheduling is implemented.
- `auto`: attempt an OpenGL 3.3 context; use CPU if absent, and only fall back to CPU for **declared unsupported operations**. Shader errors and other unexpected runtime errors are not swallowed.

The compositor owns and releases the optional GPU context. Requests, selected backend, and last-frame GPU-node/CPU-fallback counts are exposed through render evidence. Controls are resolved by the existing native DanceRudiment evaluator **before** GPU dispatch; backend execution does not have its own clock.

```json
{
  "effect_graph_backend": "auto",
  "effect_graph": {
    "nodes": [
      {"id":"layer:dancer","type":"source"},
      {"id":"layer:matte","type":"source"},
      {"id":"cutout","type":"matte","inputs":["layer:dancer","layer:matte"],
       "matte_channel":"alpha","expand":2,"feather":3,
       "mask_combine":"multiply","alpha_source":"original"}
    ],
    "output":"cutout"
  }
}
```

## GPU execution model

`src/framerudiments/effect_graph_gpu.py` implements ModernGL fragment passes for:

- Up to eight combined alpha/luminance track mattes; multiply, min/max, add, subtract, difference and screen.
- Signed erosion/dilation in two separable passes with fractional-radius interpolation.
- Separable Gaussian feather, inverted mattes, partial matte opacity and inherited original-vs-current alpha.
- Up to eight chroma-key colours with RGB and OKLab selection, tolerance, softness, edge growth/shrinkage, edge feather and nearest-key despill.
- RGBA output with straight/unassociated colour matching the existing CPU graph contract. The node's universal opacity still runs after the GPU effect in `EffectGraph.render`.

The initial bridge uploads each input image, runs offscreen shader passes, and reads an RGBA texture back into Pillow. It deliberately **does not claim** GPU-resident DAG execution or asynchronous transfers. Forks and transitions still execute in the existing CPU graph; removing transfer overhead is a later graph-backend milestone.

### Deliberate limits / fallback

- Morphology radius `abs(expand)`/`abs(edge_expand)` ≤ 16 pixels on the GPU.
- Gaussian radius `feather`/`edge_feather` ≤ 12 pixels on the GPU.
- `edge_decontaminate > 0` remains CPU-only (requires separate confidence-weighted neighbourhood reconstruction).
- Upload, intermediates and output are estimated against a configurable 768 MiB default GPU working-set cap, plus the device's max texture dimension; no implicit resampling or silent radius clamp.
- CPU implementation remains authoritative for wider radii, edge decontamination and visual reference data.

`gpu` mode raises `GPUUnsupported` for these configurations; `auto` uses the CPU reference for that node. Missing ModernGL/OpenGL raises in `gpu` mode and uses CPU in `auto` mode. `cpu` never imports or instantiates ModernGL.

### Image semantics and reference tolerances

Inputs and outputs remain 8-bit, straight RGBA. When applying matte feathering, the GPU uses a Gaussian kernel instead of Pillow's implementation, so small image differences are expected. Reference tests use strict bounds for basic alpha operations and documented, looser bounds for blurred masks. These bounds are **acceptance proposals until run on real hardware**. For all tests, source textures must remain immutable, render order must be reproducible, and transparent pixels must retain their intended RGB policy.

Shader key matching uses the same published OKLab matrices and sRGB transfer function as the CPU version. Gaussian, quantisation and morphology may differ by a few 8-bit units across GPUs and drivers. Do not suppress differences by silently skipping tests.

## Verification matrix

| Gate | How to execute | Required evidence |
| --- | --- | --- |
| CPU dispatch/unsupported/fallback | `python -m pytest -q tests/test_effect_graph_gpu_dispatch.py tests/test_effect_graph_gpu_integration.py tests/test_effect_graph_gpu_resources.py` | All pass on CPU-only systems |
| Real GPU parity | `python -m pytest -q tests/test_effect_graph_gpu_parity.py` | Must run with ModernGL + EGL; no skip-only success |
| Runtime/driver | `.github/workflows/effect-graph-gpu.yml` | OpenGL version, renderer, test and benchmark logs |
| HD performance | `python integration/benchmark_effect_graph_gpu.py --backend egl --size hd --cpu --require-gpu` | Measured CPU+GPU end-to-end median milliseconds |
| 4K and 8K performance | `python integration/benchmark_effect_graph_gpu.py --backend egl --all --gpu-budget-mib 1024 --require-gpu` | Memory-capability/skip diagnosis or measured GPU frame times |
| Full project integration | `python -m pytest -q tests/test_effect_graph_project_integration.py` | No legacy-project regression, native modulated graph controls and valid output |
| Full repository | `python -m pytest -q tests` | No regressions; optional tests skip explicitly where prerequisites missing |

The dedicated GPU workflow installs Mesa/EGL, verifies an actual context **before** running the shader tests, and uploads machine-readable benchmark results. A context-free or skip-only run is not accepted as GPU verification. Benchmarks should report GPU upload + compute + readback (not shader-only); the latter requires a texture-resident scheduler.

## Next milestone dependency gates

1. **M3.3 GPU acceptance:** get passing real OpenGL shader builds, parity tests and HD/4K/8K benchmark results from CI and at least one non-Mesa platform. Correct any parity regressions before enabling GPU automatically for released projects.
2. **M3.4:** optional GPU decontamination, better blur parity, source texture pooling and transfer batching; benchmark these against the existing bridge.
3. **M4:** arbitrary spectral colour-band extraction, recolouring and independent X/Y transforms. It should reuse this new backend capability/CI infrastructure, not hardcode a second GPU implementation.
