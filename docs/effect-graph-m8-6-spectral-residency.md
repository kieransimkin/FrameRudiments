# M8.6 — GPU-resident spectral colour bands (v36)

**Baseline:** FrameRudiments `main` `110a4501759ca3b4431b7fb22ebbf2f401f3db31` after M8.5 merge. **Status:** Implementation and tests added; hardware CI and release acceptance pending.

## Scope

This milestone extends the *closed* GPU-resident DAG executor with the existing spectral displacement kernels. A validated 1–8-band `spectral_displacement` node can now participate in a graph containing source, copy, opacity, compositing (`over`), ordinary transitions, chromatic displacement, blur, and bloom. This reuse is intentional: the existing M4.2 bridge remains the numerical reference for shader uniforms and colour selection. The public project format, source clock and DanceRudiment control paths are unchanged.

- RGB, HSV and OKLab selection, tolerance and softness; independent per-band XY movement, opacity and recolour.
- `overlay` and `replace` modes; `expanded` and `source` alpha policy; bounded `mix`. The `mix=0` identity path allocates no shader targets and retains source bytes under the existing alpha conventions.
- Multi-pass GPU execution takes one upload for each distinct reachable source and **one final readback**. Every spectral band reads the immutable original source texture; no intermediate host image transfer is needed.
- GPU target reuse is pass-safe: the original/alias is never recycled during a spectral node. Each band reads the previous output before recycling it; final mixing uses one of the vacated targets. An isolated non-identity spectral node uses one source and **two reused RGBA8 targets**, independently of band count, except for any later opacity or downstream consumers.
- The M8.5 dry-run lifetime planner executes the same pass/recycle sequence as the actual renderer and checks the caller-provided texture payload budget *before* uploads. Strict GPU mode reports a budget rejection; non-strict mode uses the *whole-graph* CPU reference. No partial resident evaluation is presented as success.
- Uniform arrays are padded to eight GLSL vec4 entries, as required by the already-tested M4.2 `replace` shader. Real shader setup is never invoked during dry planning. All invocation-owned textures and framebuffers release on completion or exceptions.

## Validation

```bash
python -m pytest -q tests/test_effect_graph_gpu_resident_m86.py
python -m pytest -q tests/test_effect_graph_gpu_resident_m86_egl.py
python -m pytest -q tests
python -m ruff check src/framerudiments/effect_graph_gpu_resident.py tests/test_effect_graph_gpu_resident_m86*.py
python integration/benchmark_effect_graph_m8_6_gpu.py --size hd --cpu --repeats 2 --output m86-resident-spectral-hd.json
# Opt-in only on memory-qualified EGL hardware:
python integration/benchmark_effect_graph_m8_6_gpu.py --size 4k --gpu-budget-mib 1024 --repeats 2 --output m86-resident-spectral-4k.json
python integration/benchmark_effect_graph_m8_6_gpu.py --size 8k --gpu-budget-mib 1024 --repeats 2 --output m86-resident-spectral-8k.json
```

The tests cover deterministic planning for 1–8 bands, RGB/HSV/OKLab and both base modes, fixed-size uniform packing, native band-parameter resolution, source immutability, memory preflight before uploads, cleanup on per-stage shader failures, visible RGB/alpha CPU parity on real EGL, and single-readback residency. Required GPU CI **must not skip** real EGL parity. The benchmark records full-graph upload/draw/readback time and output hashes, not physical GPU VRAM or display-device throughput. CI parity tolerances are ≤6 alpha levels and ≤13 visible RGB levels in RGBA8; fully transparent RGB is not used as a parity target.

## Outstanding work

- [x] Residency for spectral bands without intermediate host roundtrips.
- [x] Reused pass targets, dry-run allocation accounting and opt-in zero-mix aliasing.
- [x] CPU-independent scheduler/resource tests, real-EGL parity tests and benchmark script wired into CI.
- [ ] **Verify** full CI, actual Mesa/EGL run and benchmark artifacts on the target GitHub commit.
- [ ] GPU-resident matte/chroma processing with immutable original-alpha lineage, edge processing and bounded multi-input arrays.
- [ ] Spatial effects, mixed CPU/GPU graph island partitioning, persistent texture pools if profiling justifies them, device-level VRAM measurement and cross-vendor hardware testing.

This is intentionally **not** a general GPU executor, Vulkan/WebGPU implementation or temporal feedback system. Unsupported node families still follow the existing bridge/CPU policies.
