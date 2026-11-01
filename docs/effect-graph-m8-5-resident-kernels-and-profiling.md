# M8.5 — GPU-resident chromatic/filter kernels and bounded graph profiling

**Increment v35.** Target baseline: FrameRudiments `main` at `75b42a5ccba445d60ef7236e0e685411d32a8f7e` (M8.4 merged). M8.5 is an incremental engineering delivery; hardware CI must pass before release acceptance.

## What this increment implements

- The optional GPU-resident *closed DAG* now accepts **chromatic channel displacement, Gaussian blur and selective bloom** alongside source, copy, opacity, dissolve/wipe/radial transition and `over` composite nodes. The same existing M8.3 GLSL kernels are used for byte semantics and filter radius bounds.
- One upload per declared source and **one final readback** per accepted graph evaluation; intermediate filter and composite targets stay on device. Source and aliased fork textures are immutable; temporary filter passes can be recycled after their final consumer.
- A **device-free dry execution** computes the actual high-water **allocated RGBA8 texture count** using the identical lifetime and pass scheduler. `_check_budget` runs before any uploads/shader execution. It includes source uploads, multi-pass filter intermediates, composite chains and per-node opacity, rather than treating `len(nodes)+1` as an allocation bound.
- Framebuffer/texture resources are invocation-local, released in reverse order in `finally`; no cross-frame mutable pool, stale surface or hidden state is introduced.
- Resident telemetry adds `resident_peak_allocated_surfaces`, `resident_peak_live_surfaces`, `resident_peak_pool_surfaces` and `resident_estimated_texture_bytes`, plus draw and transfer counters. **This is RGBA8 texture payload accounting, not measured VRAM**: driver metadata, shader resources, staging buffers, host images and process memory are excluded.
- When **resident preflight rejects a GPU budget**, strict GPU mode raises `GPUUnsupported` with zero uploads. Non-strict GPU/auto mode returns the CPU reference rendering as a **whole graph** rather than continuing with partially accelerated node execution that could exceed the same budget.

## Eligibility and limitations

Supported shaders and config semantics remain GLSL 3.30 and straight-alpha RGBA8, display-referred. The CPU implementation is the source of truth. A closed graph is opted into resident mode only when all reachable nodes are supported. The following remain outside resident islands and use their current CPU/bridge dispatch: matte/chroma original-alpha lineage and edge refinement, arbitrary spectral bands, spatial warp, raster/procedural effects, layer styles, extended transition families and composite modes other than `over`. **There is no mixed-island partitioner yet.** This patch does not change the public graph/project JSON format or introduce a new dependency.

The filter reference limits remain `16,777,216` pixels, radius `0–4` and max dimension `16,384`, so an **8K resident benchmark intentionally omits filter nodes**. VRAM preflight uses a caller-set budget and is not a GPU-wide allocation monitor; other clients may still exhaust the driver.

## Tests and benchmarks

```bash
python -m pytest -q tests/test_effect_graph_gpu_resident.py tests/test_effect_graph_gpu_resident_m85.py
python -m pytest -q tests/test_effect_graph_gpu_resident_egl.py  # real ModernGL/EGL required
python -m pytest -q tests
python -m ruff check src/framerudiments/effect_graph_gpu_resident.py tests/test_effect_graph_gpu_resident_m85.py
python integration/benchmark_effect_graph_m8_5_gpu.py --size hd --cpu --repeats 2 --output m85-resident-gpu-hd.json
# Larger runs are opt-in and require an adequately provisioned driver/runner:
python integration/benchmark_effect_graph_m8_5_gpu.py --size 4k --repeats 2 --output m85-resident-gpu-4k.json
python integration/benchmark_effect_graph_m8_5_gpu.py --size 8k --gpu-budget-mib 1024 --repeats 2 --output m85-resident-gpu-8k.json
```

The benchmark JSON stores renderer/vendor, surface plan, per-run wall time including upload/readback, CPU parity deltas when requested, input size and output SHA-256 repeatability. No claims of speedup are made until actual hardware/Mesa measurements and cross-vendor results are recorded. Headless EGL CI must run and not skip its real shader tests. CI artifacts retain HD measurements, while manually dispatched extended runs are for 4K/8K qualification.

## Remaining M8.5 acceptance gates

- [x] Resident chromatic, blur and bloom kernels without intermediate host transfers.
- [x] Deterministic all-pass texture-lifetime preflight, pooled temporary reuse and error cleanup.
- [x] Explicit strict rejection and non-strict whole-graph CPU fallback on resident budget failure.
- [x] Device-independent scheduler tests and hardware-dependent parity/transfer tests added.
- [x] HD and opt-in 4K/8K benchmark harness and CI artifact paths added.
- [ ] **Verify** actual Mesa EGL parity and full repository CI; gather HD/4K/8K result artifacts.
- [ ] Expand resident islands to matte/keying with original-alpha provenance, spectral displacement and spatial processing; add mixed GPU/CPU graph partitioning.
- [ ] Record measured memory under real drivers, persistent cross-frame texture pools if justified, ROI/fusion, and cross-vendor GPU profiles.

See [M8.4 residency](effect-graph-m8-4-residency.md) and the [unified roadmap](unified-effects-transitions-rendering-roadmap.md).
