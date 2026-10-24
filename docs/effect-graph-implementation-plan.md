# FrameRudiments — Forkable Dance-Modulated Effects Graph: Granular Implementation Plan

**Version:** 1.0 · **24 October 2026** · **Status:** proposed, not implemented.
**Companion:** `FrameRudiments_Effect_Graph_Design_Specification.md`.

## Baseline and verified dependencies

Current repository files include `src/framerudiments/{compositor,post_effects,scoped_effects,effects,effect_catalogue,effect_renderers,modulation,layer_masks,layer_displacement,layer_animation,gpu_spatial_backend,project,render}.py`. Existing documentation `docs/scoped-post-effects.md` already specifies per-element and finished-frame post effects with DanceRudiment modulation; `docs/phase3-compositing.md` specifies animated layer transforms, displacement and GPU stage injection. Extend these routes rather than create incompatible replacements. `docs/project-format.md` describes strict `framerudiments.project.v1` validation and hashed asset references. The `authoring/` application uses React/TypeScript/Vite.

| Dependency | Status | Needed for | Gate |
|---|---|---|---|
| Python >=3.11 | Required, existing | all engine milestones | test across supported interpreters |
| Pillow >=10 | Required, existing | CPU reference rendering | alpha golden tests |
| `dancerudiments>=0.3.0,<0.4` | Required, existing | native motion controls | pinned clock and source provenance tests |
| pytest >=8, ruff >=0.9 | Dev, existing | all milestones | test and lint jobs |
| NumPy >=1.26 | Optional existing | accelerated CPU kernels | Pillow-only fallback tests |
| ModernGL >=5.11,<6 | Optional existing | GPU processing | headless GPU smoke test |
| React 19, Vite 7, TypeScript 5, Node >=22 | Existing authoring | graph editor | npm test/typecheck/build |
| FFmpeg | Optional CI executable | preview MP4s | executable/version probe |
| EGL/OSMesa | Optional CI runtime | headless GPU | explicit skip if unavailable |
| JSON Schema validator | Decision in M1 | strict graph validation | malformed input tests |
| OpenFX, WebGPU, Godot, Three.js, pmndrs | Research references, not runtime dependencies | API and optimisation guidance | no compatibility claims |

Avoid new heavyweight dependencies until a benchmark or missing capability proves the need. Reuse existing DanceRudiment routes, 64-pip/source-time semantics, supersampling, hash-pinned assets, and current render backends. Capture exact versions in CI constraints/lockfiles.

## Global contracts and release gates

**Graph:** versioned DAG of source, effect, fork, mask, transform, resample, colour-convert, composite, mix, switch, cache, temporal-delay, subgraph and output nodes. Typed ports include RGBA, alpha, scalar, depth, normal and velocity. Forks share immutable upstream textures. Same-frame cycles are rejected with an exact diagnostic path; delay nodes read completed prior-frame state.

**Colour/alpha:** premultiplied RGBA compositing; safe unpremultiply when necessary; declared linear/display transforms; configurable SDR/HDR formats; transparent-border default; bounds expansion for spatial effects; no treating black RGB as transparency.

**Controls:** all meaningful effect and branch parameters have stable typed paths, defaults, units, bounds, interpolation, serialization and DanceNotion/DanceRudiment bindings. Support vectors, RGB/HSV/OKLab colours, gradient stops, booleans/enums, masks, structural parameters, multiple inputs, smoothing, hysteresis, clamp, mixing and precise source-time/subframe evaluation.

**Safety:** bound nodes/edges, allocation, resolution, blur radius, history frames, procedural iterations and render time. No arbitrary shader code or unpinned filesystem/network access in presets.

**Compatibility:** opt-in graph field and explicit version migration; zero-effect legacy project renders remain unchanged; no reanalysis of source music or motion. Every milestone needs docs, unit tests, integration tests, golden images and a reproducible CI artifact.

## Milestone dependency graph

`M0 → M1 → M2 → M3`; `M2+M3 → M4,M5,M6,M7`; `M2+M3 → M8`; `M6+M8 → M9`; `M2–M9 → M10`; `M3+M10 → M11`; `M0–M11 → M12`.

Each numbered task below is intended as a separate reviewable PR or independently verifiable commit. Each milestone's exit gate is mandatory.

### M0 — Audit and baseline

**Prerequisites:** none. **Deliverable:** Baseline compatibility matrix and reproducible fixture archive.

- **M0.01** Trace compositor, effect, GPU and motion control call paths. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M0.02** Audit premultiplication, colour spaces, bounds and supersampling. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M0.03** Capture golden renders: text, sprite, spectrum, 3D, nested group and finished frame. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M0.04** Inventory tests, CI, headless GPU and authoring capabilities. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M0.05** Record architecture decisions and unresolved API questions. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** Clean checkout tests pass; fixtures reproducible; architecture map reviewed. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M1 — Versioned graph schema and validation

**Prerequisites:** M0. **Deliverable:** Graph IR, schema, diagnostics and legacy adapter.

- **M1.01** Define typed node/edge/port models with stable IDs. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M1.02** Implement strict validation of references, port types, dimensions and formats. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M1.03** Implement topological sort, exact cycle diagnostics and resource caps. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M1.04** Define canonical JSON serialization, schema versioning and migration. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M1.05** Fuzz 1,000 seeded graphs and test legacy round-trips. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** Invalid graphs fail predictably; no changes to legacy output. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M2 — CPU executor, forks and merges

**Prerequisites:** M1. **Deliverable:** Functional reference DAG renderer.

- **M2.01** Add immutable source buffer handles and fan-out without re-rendering. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M2.02** Implement effect node dispatch and branch isolation. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M2.03** Implement ordered composite with normal, add, screen, multiply and masks. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M2.04** Implement nested subgraphs, transforms and intermediate outputs. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M2.05** Expose source-render counters, solo/bypass and debug output. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** Three branches render source once; no cross-branch mutation; alpha goldens pass. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M3 — Universal DanceNotion controls

**Prerequisites:** M1,M2. **Deliverable:** Parameter registry and native binding adapter.

- **M3.01** Audit existing modulation syntax and preserve its public semantics. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M3.02** Register stable parameter descriptors for nodes, effects and branch inputs. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M3.03** Implement scalar/vector/colour/gradient mappings and multiple control inputs. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M3.04** Add clamp, easing, smoothing, hysteresis and structural invalidation. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M3.05** Test arbitrary timestamp, variable BPM, supersampling and parameter enumeration. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** Every declared animatable parameter is discoverable and exercised by tests. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M4 — Alpha-aware layer styles

**Prerequisites:** M2,M3. **Deliverable:** Shared alpha primitives and complete style family.

- **M4.01** Build blur, morphology, alpha distance, gradients and edge normals. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M4.02** Add drop/inner shadow, inner/outer glow and three outline alignments. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M4.03** Add bevel, emboss, satin, extrusion and directional edge light. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M4.04** Add colour/gradient/pattern overlays and effect bounds expansion. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M4.05** Test opaque, semitransparent, hollow, thin and subpixel silhouettes. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** No clipped glows; all styles controllable; correct semitransparent compositing. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M5 — Chromatic spectral displacement

**Prerequisites:** M2,M3. **Deliverable:** Programmable RGB and arbitrary colour-band displacement.

- **M5.01** Implement six independent RGB offset components and fractional sampling. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M5.02** Add selectable colour bands with tolerance, softness and hue wrap. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M5.03** Add per-band recolour, vector, rotation, scale, opacity and blend. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M5.04** Add radial/edge weighting, temporal hooks and bounds handling. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M5.05** Ship forked neon demo with colour/offset modulation. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** Independent vectors verified numerically; deterministic multi-branch demo. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M6 — Spatial and optical processing

**Prerequisites:** M2,M3. **Deliverable:** Wave, lens and geometric effects.

- **M6.01** Wave/ripple, liquid noise, turbulence and heat haze. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M6.02** Kaleidoscope, polar/tunnel warp, mirror and infinite zoom. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M6.03** Lens/fisheye, perspective/mesh warp, refraction and displacement maps. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M6.04** Directional, radial and rotational blur; edge detection. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M6.05** Build per-effect presets and reference images. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** All effects have bounded sampling, control schema and golden fixtures. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M7 — Colour and lighting

**Prerequisites:** M2,M3. **Deliverable:** Colour, glow and stylisation family.

- **M7.01** Hue/saturation, exposure, lift/gamma/gain, white balance. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M7.02** Gradient map, duotone/tritone, selective hue, solarise and posterise. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M7.03** Selective bloom, rays, vignette and iridescent grading. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M7.04** Pixelation, halftone, ordered dithering and stable grid origin. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M7.05** Validate HDR/SDR and per-parameter music controls. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** Identity transforms preserve input; colour/alpha fixtures pass. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M8 — Temporal and feedback processing

**Prerequisites:** M2,M3. **Deliverable:** Delay/history nodes and deterministic seeking.

- **M8.01** Define frame-delay semantics, warmup and reset policies. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M8.02** Implement echoes, afterimages, stutter and per-colour temporal delay. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M8.03** Implement feedback transforms, motion trails and slit-scan. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M8.04** Add checkpoints, cache limits and seek reconstruction. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M8.05** Test loop, rewind, variable FPS/BPM and temporal supersampling. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** Sequential vs checkpoint-seek frames match within declared tolerance. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M9 — Retro, glitch and procedural

**Prerequisites:** M6,M8. **Deliverable:** Extended creative effects catalogue.

- **M9.01** CRT, VHS, scanlines, chroma tracking and phosphor masks. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M9.02** Pixel sorting, macroblock displacement and datamosh-style smear. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M9.03** Fractal/Voronoi noise, flow fields and procedural alpha masks. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M9.04** Optional bounded reaction-diffusion and cellular automata. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M9.05** Document resource costs, deterministic seeds and preset controls. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** Every shipped effect has reference fixture, bounded work and documentation. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M10 — GPU backend and optimiser

**Prerequisites:** M2–M9. **Deliverable:** GPU execution, caching, pooling and performance report.

- **M10.01** Map reference ops to existing ModernGL backend; avoid mandatory GPU. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M10.02** Implement pooled textures, immutable fan-out and lifetime checks. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M10.03** Implement ROI, format conversion, resolution scaling and cache invalidation. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M10.04** Add safe pass fusion, dead-node elimination and constant folding. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M10.05** Benchmark 1080p/4K, branch scaling, temporal memory and CPU/GPU parity. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** No branch corruption; numerical parity tolerances documented; performance regressions reported. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M11 — Authoring and integration

**Prerequisites:** M3,M10. **Deliverable:** Graph inspection and editor.

- **M11.01** Expose graph introspection, thumbnails, diagnostics and timings. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M11.02** Build React graph editor with fork/merge, solo/bypass and reorder. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M11.03** Generate controls from registry with DanceNotion binding picker. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M11.04** Add presets, subgraph reuse, import/export and migrations. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M11.05** Document Python API, CLI, authoring and integration with DanceMoves. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** Browser tests for edit/save/reopen; clean-install walkthrough succeeds. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

### M12 — CI demos, hardening and release

**Prerequisites:** M0–M11. **Deliverable:** Production-ready release and published showcases.

- **M12.01** Publish text/sprite 3-way fork demo with glow/RGB/original. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M12.02** Publish music-driven waveform, Kandi Boom if asset available, and nested-group demos. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M12.03** Publish temporal seek comparison, CPU/GPU parity and profiling artifacts. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M12.04** Run fuzz, resource exhaustion, leak, migration and cross-platform suites. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.
- **M12.05** Complete release notes, versioning, migration guide and tagged release checklist. **Test:** automated fixture/assertion and reviewable CI evidence specific to this change.

**Exit gate:** GitHub Pages demo loads; all CI gates green; assets verified and downloadable. **CI artifacts:** test log, golden diff/contact sheet, timing and diagnostics where applicable.

## Acceptance test matrix and quantitative initial targets

| Area | Fixture / assertion | Initial threshold or policy |
|---|---|---|
| Source sharing | 1 source, 3 divergent branches | source render count exactly 1 per sample/configuration |
| Branch isolation | modify one branch | other branch pixel hashes unchanged |
| Alpha | opaque, translucent, hollow, subpixel | CPU golden MAE <= 1/255 for integer paths; exceptions documented |
| GPU parity | reference vs accelerated | tolerance established per effect in M0; no silent pixel-perfect claim |
| Graph validity | missing port, invalid type, cycle | reject with stable diagnostic code and offending path |
| Controls | registry introspection | 100% of advertised meaningful animatable parameters covered |
| Timing | same arbitrary source-time/subframe | bit-identical CPU reference output with same inputs/seed |
| Temporal | sequential vs checkpoint seek | exact reference parity or explicit effect-specific tolerance |
| Resource caps | oversized blur/history/texture | reject before allocation, no crash or runaway memory |
| Compatibility | original v1 scenes | baseline golden outputs unchanged unless explicitly migrated |
| Scaling | 1/3/8/16 branch scenes at 1080p | report memory, render time and source reuse; set budget after M0 |
| 4K | nested alpha styles and HDR branches | benchmark with published hardware/backend and budget |
| Editor | graph create/edit/save/reopen | serialized graph and preview outputs unchanged |
| Packaging | clean venv, optional GPU absent | reference engine works without GPU extras |

Performance targets must be established against measured M0 hardware rather than fabricated FPS numbers. Add budgets for p50/p95 frame time, peak VRAM, compilation, cache hits, and shader variant count, with regression alerts. Set per-backend tolerances explicitly.

## CI workflow decomposition

1. `schema-unit`: Python 3.11+ matrix, lint, validator fuzz, serialization and migration.
2. `cpu-reference`: graph execution, alpha and effect goldens, deterministic modulation.
3. `gpu-smoke`: capability probe, headless GL, parity and shader compilation; explicit unsupported skip.
4. `temporal-replay`: checkpoint, seek, loop, variable FPS/BPM and supersampling.
5. `security-budgets`: malformed presets, excessive memory requests, resource limits and path validation.
6. `authoring`: npm ci, TypeScript typecheck, unit and browser interaction tests.
7. `showcase`: render PNG/MP4/contact sheets, provenance manifest, profiling JSON and GitHub Pages publishing.
8. `release`: validate wheel/sdist, optional dependency matrix, changelog, package smoke install.

**Artifact manifest:** git SHA, schema version, asset hashes, backend/driver, Python/npm versions, render dimensions, FPS, seed, source clock, input preset, test result, output checksum and timing. Keep CI demos small enough for predictable budgets; large media should be downloadable artifacts rather than package assets.

## Work breakdown and staffing / sequencing

A sensible PR order is M0.01–05, M1.01–05, M2.01–05, then M3. M4 and M5 may proceed in parallel after control contracts stabilize; M6 and M7 may proceed in parallel; M8 should begin once state and source-time policy is reviewed. GPU adapters can be prototyped during M4–M7 but M10 optimisation should follow reference correctness. Authoring graph inspection may start in M2, but editor persistence depends on M1/M3. Release M12 only after hardening.

**Review checklist for every PR:** scope and linked task ID; API/schema impact; new/changed dependencies; deterministic fixtures; alpha and colour assumptions; parameter registry coverage; CPU fallback; GPU behavior if relevant; resource cap; docs and migration note; CI artifact link.

## Risk register and mitigations

| Risk | Mitigation / required proof |
|---|---|
| Existing effect APIs diverge | M0 mapping and compatibility adapter; no duplicate public modulation DSL |
| Premultiplied-alpha halos | explicit conversions and translucent-edge goldens |
| Branch explosion consumes VRAM | immutable sharing, liveness, pooled buffers, budgets and ROI |
| Feedback breaks random-access renders | explicit history nodes, checkpoints and reset policy |
| Shader fusion changes semantics | compare fused/unfused goldens; prohibit unsafe fusion |
| GPU not available in CI | CPU reference required; GPU job capability-gated and separately reported |
| Colour-band selection ambiguous | explicit selection colour space, tolerance and soft edges |
| Structural parameters thrash shader compilation | hysteresis, cached variants and control-rate separation |
| Schema breaks old projects | opt-in graph version, migration and legacy golden suite |
| Untrusted presets access assets or allocate excessively | hash-pinned assets, validators and hard caps |

## Research / design references

- Three.js WebGPU postprocessing: https://threejs.org/manual/pages/webgpu-postprocessing.html
- pmndrs effect merging: https://github.com/pmndrs/postprocessing/wiki/Effect-Merging
- WebGPU resource validation: https://www.w3.org/TR/webgpu/
- OpenFX typed parameters: https://openfx.readthedocs.io/en/main/Reference/ofxParameter.html
- Godot compositor architecture: https://docs.godotengine.org/en/latest/tutorials/rendering/compositor.html
- FrameRudiments repository: https://github.com/kieransimkin/FrameRudiments
- Existing docs: `docs/scoped-post-effects.md`, `docs/phase3-compositing.md`, `docs/project-format.md`.

## Completion tracking template

For each milestone maintain: `status: proposed|active|blocked|done`, owner, branch/PR, prerequisite SHAs, tests, golden artifact URL, benchmark delta, schema change, release note and sign-off. A milestone is **done** only when its gate passes on the committed SHA, not merely when a patch applies locally.


## Increment 2 — alpha matte and chroma-key CPU reference

- Add a `chroma_key` graph node with a configurable colour, threshold, softness and basic despill.
- Add inversion and Gaussian feathering to alpha matte nodes.
- Validate ranges and finite numeric values at graph construction.
- Preserve input alpha and independent fork results.
- These are CPU reference operations only; perceptual keying, keyframe control,
  DanceNotion bindings, compositor/project integration and GPU parity remain gated.
- Run `python -m pytest tests/test_effect_graph.py -q` after applying the patch.


## M2 integration increment (cumulative v4)
- Add opt-in `effect_graph` project field; validate DAG at compositor construction.
- Source node IDs `frame`, `background`, and `layer:<id>` resolve to captured isolated RGBA surfaces. Other source names require a matching captured element and fail explicitly.
- Run graph after existing postprocessing; old projects keep the same rendering path.
- The CPU graph accepts per-frame validated numeric parameter overrides without mutating persistent graph configuration. Host DanceNotion/DanceRudiment binding evaluation remains a subsequent milestone.
- Acceptance: graph unit tests, full project render integration and backwards-compatibility suite. The latter require a complete checkout and are not claimed by this patch.
