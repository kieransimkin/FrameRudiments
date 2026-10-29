# M4.2 — GPU spectral colour-band displacement

**Status:** implementation candidate, requiring the new real-GPU CI gate before release.
**Baseline:** M4.1 CPU spectral reference, `effect_graph_backend=cpu|gpu|auto`, ModernGL 5.11+ / OpenGL 3.3, eight-band validated graph controls.
**Implementation:** `src/framerudiments/effect_graph_gpu_spectral.py` and integration in `effect_graph.py` / `effect_graph_gpu.py`.

## Contract

The existing `spectral_displacement` configuration is unchanged. Every band's colour-selection space (`rgb`, `hsv`, `oklab`), tolerance, softness, independent fractional XY displacement, recolouring, band opacity and ordered compositing is evaluated in the GPU backend. `spectral_mode=overlay|replace`, `alpha_mode=source|expanded`, global mix and universal node opacity remain supported. Stable `bands.<id>.*` DanceRudiment bindings resolve *before* GPU execution, as with CPU.

Original inputs are immutable. Both renderers sample unassociated source RGB; displacement accumulates alpha-weighted (premultiplied) colour over four bilinear taps. Border samples are fully transparent. Source pixels with zero alpha must not inject hidden colour. Bands composite in their authored order, independent of fork evaluation order.

## GPU execution and memory

1. Lazy ModernGL import: no GPU dependency on the CPU-only project path.
2. Upload source RGBA8 once. One `replace` base pass computes maximum extracted coverage across up to eight bands; `overlay` copies the original.
3. Alternate between two reusable framebuffers during the independent colour-band passes. Previous output is always sampled from a different texture than the draw target; no undefined read/write feedback.
4. Run a final pass for source-alpha clipping and premultiplied global mix. Read back RGBA once.
5. Retain shader programs/VAOs across frames; release input/output textures and framebuffers reliably on success and failure. Reuse the backend instance across project frames.
6. Preflight the GPU texture size and **four-texture RGBA8 memory estimate** against the existing backend budget. This estimate is conservative about texture attachments but excludes Python/driver staging allocations, so benchmarks must also track host memory.

`gpu` mode rejects unsupported/budget-exceeding configurations explicitly. `auto` falls back to the validated CPU reference on `GPUUnsupported`; shader compilation failures and other genuine runtime faults must propagate. `mix=0` is an exact byte-for-byte bypass without GPU work. The backend is still a CPU↔GPU *node bridge*, not a GPU-resident graph; uploads and readback remain included in timings.

## Numerical parity

The shaders use the M4.1 colour-distance formulae:

- RGB: Euclidean distance with mean-square normalisation.
- HSV: circular hue distance weighted by the smaller saturation, then combined with S/V distances.
- OKLab: sRGB transfer to linear light, then OKLab distance.

The render target is RGBA8 and each compositing pass quantises intermediate results, as Pillow also does. The parity suite compares the alpha channel everywhere and RGB where either output has visible alpha; fully transparent hidden RGB is not a meaningful comparison. Tests include RGB/HSV/OKLab, overlay/replace, alpha clipping, global mix, fractional offsets, multiple bands, 2-source transitions, immutable inputs, deterministic repeats, bounded resources and failure cleanup. **CI thresholds are test acceptance criteria, not a claim that the GPU has already passed.**

## Verification and benchmarks

```bash
pip install -e '.[gpu,dev]'
python -m pytest -q tests/test_effect_graph_gpu_spectral.py tests/test_effect_graph_gpu_spectral_dispatch.py
python integration/benchmark_effect_graph_spectral_gpu.py --size hd --cpu --repeats 3 --output spectral-gpu-benchmark-hd.json
python integration/benchmark_effect_graph_spectral_gpu.py --size 4k --allow-large --budget-mib 1024 --repeats 2
python integration/benchmark_effect_graph_spectral_gpu.py --size 8k --allow-large --budget-mib 1024 --repeats 2
```

The dedicated `.github/workflows/effect-graph-gpu.yml` must create a real Mesa/EGL OpenGL context; it must run the new parity suite and capture the HD benchmark on every relevant commit. The optional workflow-dispatch benchmark covers 4K and 8K. The benchmark includes transfer costs and hashes every output, plus its renderer, GL version, device budget, timings and optional CPU result. Large jobs require opt-in and may fail on insufficient resources; a fail/skip is not performance evidence.

## Existing general-CI lint repair

The previous M4.1 commit (`31f35ad`) failed the generic CI lint job on Ruff `E741`: a lower-case `l` used for an OKLab LMS intermediate in `effect_graph_spectral.py`. This patch renames that local intermediate to `lms_l` (with analogous descriptive names for the other LMS terms), preserving the exact numerical calculation. It must be verified with `ruff check .` in normal CI.

## Gating / limitations

- **CPU reference regression:** all existing effect-graph tests must pass.
- **GLSL compilation:** all four spectral shader programs compile under a real OpenGL 3.3+ implementation.
- **Real ModernGL parity:** required across RGB/HSV/OKLab and every alpha/mix mode. The dedicated CI result, not locally skipped tests, closes this gate.
- **Performance:** record true wall time at HD; collect 4K/8K on a machine with enough VRAM. No unverified performance or speedup claims.
- **Legacy compatibility:** projects without spectral effects and projects on `effect_graph_backend=cpu` remain unchanged.
- **Deferred:** GPU-resident DAG scheduling, 16-bit floating-point HDR, dynamically generated shader effects, colour-space metadata, cross-device comparison and subframe GPU texture reuse.
