# M8.7 — GPU-resident matte and chroma-key effects (v37)

**Baseline:** FrameRudiments `main` `5ebd3805c1fa074095e15ed1186d11008bf16567` (M8.6 merged).  **Status:** Implementation, CPU-only tests, mandatory real-EGL parity checks and benchmarks delivered. Full hardware CI/release acceptance requires execution after merge.

## Scope and implementation

The bounded, optional ModernGL closed-DAG executor now accepts `matte` and `chroma_key` nodes in addition to the existing M8.4–M8.6 resident shaders. A fully supported graph still uploads every reachable source exactly once and reads the final graph output once. Forks, aliases and intermediate render passes stay as GPU texture handles. The public graph JSON, native DanceRudiment control paths, clocks and default CPU rendering behaviour are unchanged.

- **Matte:** foreground plus 1–8 independent RGBA mask branches; alpha/luminance channels, multiply/min/max/add/subtract/difference/screen combination, inversion and partial opacity. Morphological signed expand/erode supports fractional radii, followed by bounded Gaussian feather.
- **Chroma:** RGB/OKLab matching with up to eight key colours, tolerance/softness, signed edge expand, edge feather and per-nearest-key despill. Keys use the existing M3.3 GLSL implementation; shader-colour arrays are padded to exactly eight `vec4` entries and rebound for every frame.
- **Original-alpha lineage:** `source` outputs establish an alpha origin; unary `copy`, opacity, filter, chromatic, spectral, matte and key nodes retain the inherited original. `transition` and `composite` reset the alpha origin to their **post-opacity** output, matching CPU `EffectGraph.render`. This is an immutable alias of the original *RGBA8 surface's A channel*, not another image upload; it can remain pinned after the current image branch has advanced. Resident matte/key final shaders sample `original_tex.a`, while the old per-node bridge continues to sample its uploaded `L→RGBA` image's R channel. No existing shader or project is silently reinterpreted.
- **Resource lifetime:** The same dry/actual graph scheduler accounts for lineage pins, 1–8 graph-mask inputs, morphology/blur passes and output targets. It rejects a GPU payload-budget overflow before the first upload, preserving strict `gpu` rejection and non-strict whole-graph CPU fallback for resident-budget failures. Temporary targets are recycled after their last shader read; everything releases in `finally`, even on shader error.
- **No hidden fallback:** The resident predicate uses the *existing* `gpu_requirements` predicate on resolved per-frame parameters. An unsupported matte/key configuration cannot enter a resident island.

## Deliberate bounds and exceptions

- Matte morphology `|expand| ≤ 16` pixels; chroma `|edge_expand| ≤ 16` pixels.
- Matte feather and chroma edge feather `≤ 12` pixels. Larger values stay on the established per-node/CPU path; they are never silently truncated.
- **Historical M8.7 limit:** chroma edge decontamination was not a native GPU operation in 0.4.20. FrameRudiments 0.4.21/M8.8 adds the bounded confidence-weighted neighbouring-colour reconstruction described in [the M8.8 contract](effect-graph-m8-8-gpu-islands.md); feather and despill remain distinct operations.
- There are no new float/HDR device formats, persistent cross-frame pools, arbitrary GPU kernels, CPU/GPU partial-island partitioning or driver-level physical VRAM measurements. Existing limits and explicit diagnostics still apply.
- Output is display-referred, straight RGBA8. Morphology/feather shaders have documented CPU/Pillow differences; test tolerances are explicit and tighter when no blur is present. Hidden RGB under zero alpha is excluded from visible-pixel comparisons.

## Verification

```bash
python -m pytest -q tests/test_effect_graph_gpu_resident_m87.py
python -m pytest -q tests/test_effect_graph_gpu_resident_m87_egl.py  # require actual EGL, no skips
python -m pytest -q tests
python -m ruff check .
python integration/benchmark_effect_graph_m8_7_gpu.py --size hd --cpu --repeats 2 --output m87-resident-matte-key-hd.json
# Extended hardware-only checks, opt-in:
python integration/benchmark_effect_graph_m8_7_gpu.py --size 4k --gpu-budget-mib 1024 --repeats 2 --output m87-resident-matte-key-4k.json
python integration/benchmark_effect_graph_m8_7_gpu.py --size 8k --gpu-budget-mib 1024 --repeats 2 --output m87-resident-matte-key-8k.json
```

The new local tests validate dry-plan count/fork budgets, sampler array binding, source/transition alpha-origin selection after unary operations, uniform packing, budget rejection with **zero uploads**, and guaranteed resource release after shader failures. The required GPU workflow checks real CPU-vs-ModernGL parity for alpha/luminance and 8-input mattes; RGB/OKLab, despill, erosion, morphology, feather, source immutability, native control overrides, original/current alpha and strict/non-strict fallback. All real-EGL test cases must execute without skips, and an HD whole-graph benchmark must produce its JSON artifact.

The benchmark records shader+upload+readback wall times, GPU renderer/vendor, output SHA-256 and reference differences when requested. Logical RGBA8 texture accounting excludes driver allocations, staging buffers, host process RSS and other GPU clients; it **does not claim measured VRAM**.

## Next milestone

M8.8: partition mixed CPU/GPU DAGs into explicit islands with transfer telemetry, add a bounded confidence-weighted GPU edge-decontamination pass if parity/driver evidence supports it, then extend spatial transforms and measured cross-vendor profiling. M8.7 should only be release-accepted once GitHub's exact-head full CI and mandatory real-EGL parity/benchmarks pass.
