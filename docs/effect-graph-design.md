# FrameRudiments — Forkable Dance-Modulated Effects Graph

**Status:** Proposed architecture and implementation roadmap · **Date:** 24 October 2026

## 1. Executive decision
Replace the assumption of a linear postprocessing stack with a versioned, typed, directed acyclic render graph (DAG). Render source layers once, share immutable intermediate textures across arbitrary downstream branches, process each branch independently, and composite in explicitly ordered merge nodes. Temporal feedback is represented by delayed edges whose previous-frame state is external to the current-frame DAG. Every meaningful visual parameter—including colour-selection and per-channel displacement vectors—must be discoverable and controllable through the existing DanceNotion binding architecture.

## 2. Goals, constraints and non-goals
**Goals:** universal applicability to text, sprites, 2D graphics, video, waveform and spectrum visualisers, particles, rigged meshes, 3D output, and nested groups; alpha-aware effects; arbitrary forks and recombination; consistent preview and offline output; controllable parameters; extensible effects; visual tests and CI demonstrations.

**Constraints:** preserve existing scene configurations and rendering semantics; avoid unnecessary full-resolution intermediate allocations; maintain premultiplied-alpha correctness and declared colour spaces; support CPU fallback/reference output and accelerated backends as available; validate all graphs before rendering; isolate effect plugins and enforce resource limits.

**Non-goals in initial milestones:** general-purpose scene-node editor, arbitrary shader execution from untrusted presets, physically accurate spectral optics, perfect CPU/GPU pixel identity, unrestricted cyclic same-frame graphs.

## 3. Research findings and implications
- Three.js WebGPU node postprocessing supports node composition, multiple render targets and effect combining. Implication: design an explicit graph IR and optimise compatible operations; do not force every effect into a separate pass. [1]
- pmndrs/postprocessing merges compatible effects into compound shaders, but texture-coordinate modification and additional buffer sampling constrain merging. Implication: annotate each effect with neighbourhood, coordinate and history dependencies; fuse only proven-safe sequences. [2]
- WebGPU imposes texture usage and aliasing validation rules. Implication: track read/write lifetimes, allocate scratch targets by usage and format, and prevent simultaneous read/write hazards. [3]
- OpenFX uses typed, host-managed, animatable parameter descriptors. Implication: DanceNotion should consume a shared parameter registry rather than effect-specific binding code. [4]
- Godot compositor effects expose render-stage insertion and optional motion, depth and normal inputs. Implication: graph ports should advertise required buffers and graceful fallback when they are unavailable. [5]

## 4. Graph data model
A graph owns nodes, typed input/output ports, ordered edges, graph outputs, parameter bindings, format contracts and a schema version. Stable IDs must survive reordering. Nodes: Source, Effect, Mask, Transform, Fork (logical fan-out), Composite, Mix, Switch, ColourConvert, Resample, Cache, TemporalDelay, Output. A node may emit multiple named outputs (RGBA, alpha, depth, normals, velocity, masks). Connections must match declared types, dimensions and time domains; conversions must be explicit or inserted by the compiler with warnings.

A source is evaluated at most once for a given frame, resolution, configuration and relevant parameter state. Forked consumers reference the same immutable result until mutation is needed. A composite has ordered inputs with per-input opacity, blend mode, transform, mask, z-order and clipping. Merge results can themselves be forked or used as inputs to other merges. Nested graphs expose public ports and controls.

A TemporalDelay reads a previously completed frame or checkpoint, thereby breaking same-frame dependency cycles. Stateful nodes specify warm-up, checkpoint, seek and discontinuity policies. Compiler rejects all other cycles and reports the exact path.

## 5. Alpha and colour semantics
Use premultiplied RGBA for compositing; effects that require straight RGB must unpremultiply with an epsilon guard, then repremultiply. Specify linear-light versus display-referred processing per effect; perform colour transforms explicitly. Support SDR and HDR intermediates (RGBA8 and RGBA16F candidates), with memory-aware selection. Never infer transparency from RGB black. Every spatial effect must declare expanded bounds and border sampling policy (transparent, clamp, mirror, wrap). Alpha-mask sources: original source alpha, current branch alpha, named upstream alpha, luminance, colour selection, depth or external mask.

Alpha processing primitives: threshold with softness, erosion, dilation, signed distance, separable Gaussian blur, gradient/normal extraction, edge distance, morphological outline, inside/outside masks. Preserve subpixel edges and configurable antialiasing; cache reusable mask transforms where dependencies permit.

## 6. Universal DanceNotion control contract
Each parameter descriptor must expose: stable path, type, default, hard/soft range, units, interpolation, precision, mapping space, animatability, structural-change flag, UI hints, documentation, validation and dependency impact. Types: scalar, integer, bool, enum, vector2/3/4, angle, colour, gradient, curve, matrix, string/resource reference and seed. Colour controls may target full colour or components in RGB/HSV/OKLab. Support multiple sources per target, precedence, add/multiply/replace/min/max mixing, clamping, quantisation, hysteresis, smoothing, easing, envelopes and beat-synchronous interpolation.

Evaluation order: base value → keyframe/automation → DanceNotion bindings → validation → parameter snapshot → graph execution. Snapshot at render time or sample time, not wall clock; motion-blur subframes must evaluate controls at each subframe. Structural parameters may trigger graph recompilation and should expose switching costs; ordinary scalar changes should update uniforms. Expose introspection through UI, CLI and API. Missing controls must produce explicit diagnostics, never silently bind to unrelated signals.

## 7. Chromatic and arbitrary colour displacement
Implement standard RGB split as a preset of a more general colour-band displacement engine. Each band specifies source colour/space, selection range and tolerance, softness, alpha handling, displacement X/Y, rotation, scale, radial/tangential offset, wave/noise field, temporal offset, destination tint/colour transform, opacity, blend and sampling mode. Multiple bands can overlap; define normalization and accumulation policies to avoid unintended energy growth. Include luminance and alpha selectors, channel matrix modes, edge-only mode, radial dispersion, prism presets, spectral-gradient approximation, and independently time-delayed channels. All offsets and selected colours are DanceNotion controllable.

Acceptance: independently animate red/green/blue X and Y; select a non-primary colour (e.g. magenta), move only that colour band, recolour it, and composite against an unaltered original branch. Verify with transparent anti-aliased assets and HDR input.

## 8. Full effect catalogue and integration priorities
**P0—foundations:** opacity, blend, masks, colour overlays, transforms, crop, blur, alpha morphology, outlines (inner/outer/centre), drop/inner shadows, outer/inner glows, bevel/emboss, satin, extrusion, long shadows.

**P1—musically expressive spatial:** chromatic and colour-band displacement, sine/ripple/liquid displacement, displacement-map warping, swirl/twist, radial zoom, lens distortion, fisheye, mirror, kaleidoscope, polar/tunnel transforms, mesh/perspective warp, shockwaves, directional smear, edge detection and relighting.

**P1—colour/light:** brightness/contrast, lift/gamma/gain, exposure, white balance, saturation/vibrance, hue remapping, gradient map, duotone/tritone, colour LUT, posterise, solarise, selective bloom, light rays, vignette, chromatic fringing, iridescence and selective highlights.

**P1—temporal:** afterimages, multi-echo, motion trails, recursive feedback, zoom/rotation feedback, temporal RGB split, temporal frame blending, motion-vector blur (where velocity exists), slit-scan, time displacement and frame hold/stutter.

**P2—digital/retro:** pixelation, RGB halftone, dithering, CRT mask/scanlines, VHS tracking, film grain, macroblock displacement, datamosh-style simulation, scanline tearing, pixel sorting, ASCII/glyph shading, quantisation, digital ringing and compression-like artifacts.

**P2—procedural:** fractal noise, Voronoi/cellular distortion, flow-field advection, reaction-diffusion, cellular automata, particle disintegration, procedural caustics, height-map lighting, refraction, feedback fractals and audio-driven procedural textures.

**P3—optional expensive 3D-aware:** depth-of-field, SSAO-like effects, screen-space reflections, depth fog, normal-aware edge light and motion-vector reconstruction. These require optional depth/normal/velocity ports and must not masquerade as accurate 3D effects when inputs are absent.

Each effect registers parameter schema, port requirements, precision, colour-space expectation, edge/bounds growth, deterministic/stateful status, CPU implementation availability, GPU shader availability, resource estimate, tests and preset examples.

## 9. Effect graph compiler and runtime
Validation → type checking → cycle checking → parameter dependency resolution → colour/format/resolution negotiation → liveness analysis → immutable texture sharing → compatible pass fusion → scheduling → texture pooling → backend dispatch. Optimisations: dead-node elimination, common-subexpression elimination (including parameter snapshots), zero-strength bypass, constant folding, ROI propagation, resolution scaling, tile-aware evaluation, separable convolution, cached alpha/SDF, and texture lifetime reuse. Do not fuse across semantic boundaries where sampling order, colour space, precision, alpha or history changes results.

Resource budget: configurable max nodes, branches, texture dimensions, temporary VRAM, convolution radius, iterations, history frames and compilation time. Provide per-node timings, allocation peaks, shader compile logs, cache hit rates and fallback warnings. Allow per-branch preview and intermediate inspection. For backend portability, keep a reference execution model separate from shader implementations.

## 10. Presets, packaging, authoring and interoperability
Versioned JSON graph serialization with schema migration and unknown-field preservation. Graph presets and subgraphs should be importable and parameterized, with namespaced IDs and external asset manifests. Support preview thumbnails, parameter discovery, effect browser, node graph UI, branch solo/mute, bypass, live parameter inspector, before/after, debug overlays, and provenance for bundled examples. Share parameter schema with DanceMoves/related tools rather than duplicating control definitions. External integrations may later use OpenFX-inspired adapters, GLSL/WGSL shader libraries and interchange adapters, but no claim of binary OpenFX host compatibility until implemented and tested.

## 11. Staged implementation and acceptance gates
**M0 — repository audit and baseline:** inventory compositor, effect interfaces, alpha conventions, DanceNotion bindings, backend support, tests and CI; capture representative reference renders. Gate: baseline render comparison and compatibility matrix published.

**M1 — schema, registry and validation:** typed graph IR, stable IDs, effect metadata, JSON schema, compiler diagnostics, migrations, introspection. Gate: malformed edges, invalid types and cycles rejected; legacy scenes round-trip without visual change.

**M2 — forks and merges:** immutable source sharing, branch fan-out, ordered composite, nested graph, masks, transforms, branch solo and intermediate output. Gate: 3-branch source rendered once; composite ordering and alpha verified in golden tests.

**M3 — universal controls:** expose all meaningful node/effect/branch parameters through DanceNotion, including colour and vectors; smoothing, range mapping, multi-binding, deterministic subframes. Gate: property enumeration coverage test and automated modulation test for every parameter descriptor.

**M4 — alpha-aware styles:** shared alpha primitives, shadows, glows, outlines, bevel, emboss, satin, extrusion, overlays. Gate: golden fixtures on opaque, semitransparent, hollow, thin and anti-aliased silhouettes; no cropped effect bounds.

**M5 — chromatic displacement:** RGB vector split, arbitrary colour-band selection, recolour, radial/edge modes and controls. Gate: six independently modulated RGB offset components plus colour selection, with deterministic sample frames and branch merging.

**M6 — spatial and colour suite:** warps, kaleidoscope, wave, pixel sorting baseline, grading, bloom and light. Gate: each effect has documented parameters, CPU/reference fixture, GPU fixture if supported, and one musical preset.

**M7 — temporal effects:** delay ports, echo, feedback, motion trails, stutter, slit-scan; checkpoint/seek model. Gate: repeated renders produce identical frames; seek either reconstructs history or reports declared limitation.

**M8 — retro/procedural suite:** CRT/VHS, dithering, halftone, macroblock glitches, procedural noise and optional simulation effects. Gate: presets, stress tests, documented limits and graceful fallback.

**M9 — performance/backend optimisation:** fusion, caching, ROI, texture pooling, profiling, resource budgets, backend parity. Gate: baseline comparisons on representative 1080p/4K scenes; regressions tracked rather than hidden; peak memory documented.

**M10 — editor, CI and release:** graph editor or inspectable graph view, examples, API docs, migration guide, GitHub Pages interactive showcases, downloadable stills/videos, cross-platform CI and release checklist. Gate: CI publishes a multi-branch, music-modulated demonstrator using available example assets; installation and playback validated.

## 12. Testing matrix
Golden-image fixtures: opaque/transparent layers, subpixel alpha, hollow silhouettes, large blur bounds, nested groups, arbitrary colour-space selections, branch isolation, blend ordering, high dynamic range, different resolutions, odd dimensions and rotated transforms. Property tests: DAG topological invariants, no unexpected source recomputation, texture lifetime safety, parameter range enforcement, deterministic seeds, schema migrations. Temporal tests: playback, seek, loop, rewind, variable FPS, variable BPM, checkpoint recovery, frame supersampling. Security: untrusted presets cannot request filesystem/network access or allocate unbounded resources. Performance: warm/cold compilation, branch count scaling, 1080p/4K latency, VRAM peaks, memory leaks, cache reuse.

## 13. CI showcase scenarios
A. One text/sprite layer forked into clean, cyan glow and magenta RGB-split branches, composited independently.
B. Kandi Boom or another available rigged mesh with colour-band displacement and bass-driven afterimages.
C. Waveform/spectrum layer with beat-modulated liquid distortion, edge glow and kaleidoscope.
D. Nested group with per-child effects and independent parent effects.
E. Temporal feedback with reproducible checkpoints and scrub/reseek demonstration.
F. Performance dashboard showing source render counts, graph passes, allocations and frame timings.

## 14. Open decisions
Confirm canonical DanceNotion schema/API and current FrameRudiments backends before choosing concrete class names; select baseline HDR format and colour management policy; decide whether graph editing belongs in FrameRudiments or DanceMoves; choose cache invalidation granularity; decide GPU portability target and supported fallback tiers. Resolve these in M0/M1, not by inventing new parallel frameworks.

## References
[1] Three.js WebGPU postprocessing: https://threejs.org/manual/pages/webgpu-postprocessing.html
[2] pmndrs postprocessing and effect merging: https://github.com/pmndrs/postprocessing ; https://github.com/pmndrs/postprocessing/wiki/Effect-Merging
[3] WebGPU specification: https://www.w3.org/TR/webgpu/
[4] OpenFX parameter specification: https://openfx.readthedocs.io/en/main/Reference/ofxParameter.html
[5] Godot compositor effects: https://docs.godotengine.org/en/latest/tutorials/rendering/compositor.html
[6] FrameRudiments repository: https://github.com/kieransimkin/FrameRudiments
