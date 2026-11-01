# M8.4 — GPU-resident closed graph islands

Increment v34, baseline `cd86d615ab163b7149c7600cd756329f32faa51f`.

A **closed** graph consisting of source, copy, opacity, ordinary two-source dissolve/wipe/radial transitions, and Porter-Duff `over` composites is now eligible for GPU-resident evaluation. It uploads each distinct source once and performs a single output readback. Intermediate nodes stay as texture handles. The CPU reference remains authoritative for unsupported DAGs, custom visual-effects pipelines and complex transitions.

## Semantics

* The existing `EffectGraph.render` resolves native controls first; scheduling uses immutable per-frame configs. Unsupported graphs retain the existing v33 per-node executor.
* A graph path is all-or-nothing: no hidden interpretation of unsupported effects inside a GPU island.
* Source textures are not modified in place. Copy nodes may alias their sources; a branched producer cannot be reused until every consumer is finished.
* Output opacity uses a GPU pass, and transitions use existing GLSL 3.30 shaders. `over` composites use straight RGBA output with premultiplied internal colour math.
* Exact progress endpoints alias their source texture instead of dispatching a transition shader.
* Pools are **per render invocation**, not global. Every allocated texture, framebuffer and temporary readback framebuffer is released in `finally`, even after failure.
* VRAM budget preflight is conservative: `len(reachable_nodes) + 1` RGBA8 textures. No allocation occurs if the budget fails. Resource usage is a logical payload estimate, not physical VRAM measurement.
* Transfer accounting identifies the number of distinct source uploads, output readbacks, resident shader draws and peak live referenced textures.

## Gates

* CPU tests validate eligibility, native-value resolution, immutability, fallback, and unchanged legacy dispatch.
* Dedicated EGL CI **requires** two real-GPU tests to execute without skips, including a shared fork, compositing, deterministic repeated evaluation and rejection before upload when VRAM budget is insufficient.
* Legacy CPU tests keep the no-skips policy. The EGL-only module is excluded from their collection and included in the required GPU workflow.
* Final acceptance needs actual EGL parity and CI, not only source compilation.

## Outstanding M8.4 work

The closed-graph scheduler is a useful first GPU-resident island. M8.4 is **not yet fully closed**: expand the texture-aware kernel API to matte/chroma, spectral, blur/bloom, displacement and mixed CPU/GPU segmented DAGs; replace conservative whole-graph allocation preflight with verified peak-live memory accounting; add persistent pool scheduling, graph partitioning, and measured 4K/8K cross-vendor benchmarks. This milestone does not claim those optimisations.
