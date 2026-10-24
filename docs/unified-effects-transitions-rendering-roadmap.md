# FrameRudiments unified effect graph, transitions and rendering adapters — implementation roadmap

**Version:** 1.1 — 24 October 2026
**Status:** Active implementation, with independently gated milestones.
**Owner:** FrameRudiments / DanceFlow ecosystem.
**Baseline:** `framerudiments.project.v1`, `effect_graph`, `effect_graph_controls`, existing compositor and native DanceRudiment modulation.
**Related:** [effect-graph-implementation-plan.md](effect-graph-implementation-plan.md), [effect-graph-design.md](effect-graph-design.md), [effect-graph-matte-amendment.md](effect-graph-matte-amendment.md), [effect-graph-phase-v11.md](effect-graph-phase-v11.md), [project-format.md](project-format.md), [scoped-post-effects.md](scoped-post-effects.md).

## Scope, principles and non-goals

Unify alpha-aware per-layer styles, independently forked filter branches, arbitrary compositing, two-or-more-input transitions, temporal effects, DanceNotion controls and optional third-party effects in **one** typed graph. The graph must retain FrameRudiments' exact-frame, source-bound DanceRudiment semantics: rendering frame N must not depend on whether N-1 was rendered first, except where a declared temporal dependency is reconstructible from explicitly identified source frames or checkpointed state.

1. Render a source once, then share its immutable image across branches. Branches may composite into subsequent graph nodes.
2. Every meaningful numeric, vector, colour and discrete parameter must have a validated control descriptor and support binding to native DanceRudiment/DanceNotion controls. Do not create a parallel clock or audio-analysis engine.
3. Support original-alpha, post-effect alpha, track-alpha and luminance mattes. Chroma-key selected colours, despill and matte combination must be separately controllable.
4. Declare pixel format, colour space, alpha association, bounds, optional depth/normals/velocity and temporal requirements per node/port.
5. The base install stays small. Expensive models, native plugin hosts, GPU backends and proprietary codecs are strictly optional.
6. No silent fallback that changes image meaning. Unsupported node/device/model/shader combinations produce explicit diagnostics and discoverable capabilities.
7. Existing projects without `effect_graph` must remain byte-for-byte stable under identical dependencies and input media.

**Not in the present milestone:** live temporal feedback, neural generation, OpenFX plugin execution, scene-linear HDR or an alternate GPU runtime. They are independently gated later deliverables.

## Baseline verified in repository

- Existing `src/framerudiments/effect_graph.py`: bounded source, copy, opacity, alpha/luminance matte, chroma key, chromatic displacement and composite nodes; strict DAG validation.
- Existing `src/framerudiments/effect_graph_modulation.py`: native `_Modulator` adapter and immutable per-frame numeric overrides.
- `src/framerudiments/compositor.py`: opt-in graph execution after legacy element and frame effects, using source `frame`, `background` and captured `layer:<id>` surfaces.
- `docs/effect-graph-implementation-plan.md`: original granular project plan and dependency gates.
- Current increment: CPU two-input `transition` node with dissolve, directional wipe and radial reveal, exact endpoints, premultiplied-safe interpolation and native `progress`/`feather`/`center.x`/`center.y` control targets. **Only isolated graph tests are locally verified; full repository CI is required.**

## Proposed graph API (transition v1)

```json
{
  "effect_graph": {
    "nodes": [
      {"id":"layer:foreground","type":"source"},
      {"id":"layer:background","type":"source"},
      {"id":"mix","type":"transition", "inputs":["layer:background","layer:foreground"],
       "transition_style":"wipe", "direction":"right", "progress":0.5, "feather":0.1,
       "center":[0.5,0.5]}
    ],
    "output":"mix"
  },
  "effect_graph_controls": [
    {"id":"transition-phase","node":"mix","property":"progress",
     "motion":{"kind":"named","pattern":"bounce"},"axis":"y","range":[0,1]}
  ]
}
```

`transition_style`: `dissolve | wipe | radial`. Progress 0 returns input A verbatim; 1 returns input B verbatim. Wipes reveal B toward the given `direction` (`right | left | down | up`); radial reveal expands from normalized `center`. `feather` is normalized across the transition mask, between 0 and 1. This is an *interpolation*, not Porter–Duff source-over. Transparent RGB must not contaminate visible colours; intermediate output remains straight RGBA for the existing Pillow compositor. The current CPU reference operates in display-referred RGBA; **scene-linear interpolation is a future milestone**. Every transition is evaluated at the timestamp provided by the established native motion clock, with no implicit state.

## Required dependencies and interfaces

| Component | Requirement | Milestone | Verification |
| --- | --- | --- | --- |
| Python >=3.11, Pillow >=10 | Required existing base | All | CPU fixtures, alpha regression |
| `dancerudiments>=0.3,<0.4` | Required existing motion contract | M2–M5 | Motion provenance and exact-frame controls |
| NumPy >=1.26 | Optional existing extra; necessary for present CPU transition/chromatic reference | M3+ | Dependency error clearly reported when missing; no import needed if graph inactive |
| pytest >=8, ruff >=0.9 | Development | All | Unit, property, lint, integration jobs |
| ModernGL >=5.11,<6 | Optional existing GPU extra | M8 | GPU-vs-CPU golden images |
| React 19 / TypeScript 5 / Vite 7 / Node >=22 | Existing authoring interface | M10 | npm test, typecheck, build |
| FFmpeg/ffprobe | Optional render/evidence tooling | M11 | Bounded encode/decode smoke |
| OpenColorIO, OpenEXR | Optional production colour/AOV adapters | M12 | Reference colours, roundtrip data |
| OpenTimelineIO | Optional timeline import/export | M13 | Rational timebase, markers, source references |
| ISF / GL Transitions | Shader-format adapter, no native runtime dependency | M9 | Metadata and safe shader fixtures |
| ONNX Runtime + pinned optional models | Experimental analysis pass | M14 | Hash-pinned depth/matte/motion output |
| OpenFX, USD/Hydra, GStreamer, NDI/Spout/Syphon, Vulkan/WebGPU | Research candidates only | M15 | Capability probes and isolated opt-in tests |

All external shader/model/media sources must be pinned or versioned, constrained by project sandbox policies and included in render provenance. Optional dependencies are not imported on the legacy rendering path.

## Granular delivery stages and exit gates

### M0 — Regression baseline and repository audit (existing; revalidate)
- **M0.1** Inventory compositor, project schema, existing scoped effects, masks, supersampling and GPU effect hooks; link actual file paths.
- **M0.2** Store representative golden images for captions, alpha sprites, grouped layers, visualisers and a Kandi Boom rigged-character scene.
- **M0.3** Record existing project output hashes with no effect graph.
- **M0.4** Record CPU/optional GPU availability and versions in CI logs.
- **Exit:** legacy frame hash comparisons pass unchanged; missing GPU is explicit, not silently downgraded.

### M1 — Graph schema and validation (initial core merged; expand)
- **M1.1** Versioned serialization, explicit node/port types and stable identifiers.
- **M1.2** Enforce input counts, unknown fields, duplicate IDs, cycle paths, allocation bounds and dangling nodes.
- **M1.3** Accept source, copy, opacity, chroma, matte, composite and transition nodes.
- **M1.4** Validate every control path at project preparation, including units/ranges and discrete fields.
- **Exit:** invalid projects fail before expensive media rendering; DAG order deterministic.

### M2 — Fork/merge compositor + native control integration (initial core merged; integration gate pending)
- **M2.1** Capture isolated RGBA surfaces once; maintain immutable source image contract.
- **M2.2** Fork one source into independent filter branches; verify no input mutation or unexpected overwrite.
- **M2.3** Integrate node execution with `post_effects` and final compositing without changing legacy output.
- **M2.4** Bind native DanceRudiment controls by stable node/property address; collect evidence and source hashes.
- **M2.5** Run an actual project end-to-end with two timestamps and two modulated branches; verify supersampling.
- **Exit:** full-project CI passes, no-graph baseline stable, outputs differ as expected with native motion.

### M3 — Universal alpha, track mattes, chroma keys and opacity (initial CPU foundation merged; polish)
- **M3.1** Pre/post effect matte input and original/current alpha selection; alpha/luminance key modes.
- **M3.2** Multi-matte boolean/arithmetic combination and inversion, spread/erode/dilate, feather.
- **M3.3** Perceptual chroma key selection, soft threshold, independent despill and edge decontamination.
- **M3.4** Stable track-matte routing (provider need not be visually composited).
- **M3.5** All matte/key/opacity parameters exposed via native control descriptors.
- **Exit:** no green spill in reference keyer fixtures; CPU and GPU match within bounded tolerances.

### M4 — Transition core (current increment: CPU v1)
- **M4.1** Register exactly two RGBA inputs and one RGBA output for transition nodes.
- **M4.2** Implement premultiplied-safe dissolve with exact progress endpoints.
- **M4.3** Implement horizontal/vertical directional wipes and radial centre-out reveals, including feathering.
- **M4.4** Expose `progress`, `feather`, `center.x`, `center.y` to native motion controls; reject invalid ranges.
- **M4.5** Test transparent RGB, endpoint identity, edge direction, bounds, arbitrary frame order and fork isolation.
- **Exit:** isolated CPU transition tests green; before release, full-project native motion test and CI green.

### M5 — Expanded transition families and mattes
- **M5.1** Luma/key/matte-driven transitions; arbitrary graph port as reveal mask, separate feather/threshold.
- **M5.2** Spectral wipe, colour-band segmentation, chromatic motion echo and multi-colour transforms.
- **M5.3** Zoom/rotate/polar/kaleidoscope/liquid displacement transitions.
- **M5.4** Transition stacking/chaining, multi-stage branch outputs and per-input optional preprocessing.
- **M5.5** Beat-locked progress curves, reversed/non-monotonic progress and repeat-clock semantics.
- **Exit:** configurable two-input transition demo across masks and alpha; frame N independent of call order.

### M6 — Alpha-derived layer styles
- **M6.1** Shared alpha silhouette, signed-distance/morphological contour utilities.
- **M6.2** Drop/inner shadow, outer/inner glow, inner/outer/centred outline.
- **M6.3** Bevel/emboss/edge lighting, satin, long shadow/extrusion.
- **M6.4** Alpha-mask overlays, gradient/pattern/colour mapping and compositing blend modes.
- **M6.5** DanceRudiment-modulatable style parameters and retained bounds expansion.
- **Exit:** sprite, text, rig output and grouped layers use identical style contracts.

### M7 — Spatial, spectral and procedural effects
- **M7.1** Arbitrary colour-band selectors in RGB, HSV, OKLab with softness and remapping.
- **M7.2** Independent XY fractional displacement per band, sampling policies and selected-colour recolour.
- **M7.3** Waves, turbulence, vector-field warp, radial/polar transforms and mirror/kaleidoscope.
- **M7.4** Pixel sorting, CRT/glitch/scanline and controlled pixelation/dithering.
- **M7.5** Procedural texture/mask/control-field nodes driven by frequency/motion channels.
- **Exit:** no transparent-edge contamination; control validation and pixel-golden images.

### M8 — GPU graph execution and caching
- **M8.1** Capability registry for Pillow/NumPy/ModernGL and optional backends.
- **M8.2** Texture formats, explicit alpha association and colour-space declarations.
- **M8.3** GPU transition, matte, chromatic, bloom and blur variants.
- **M8.4** Track surface lifetime, texture pooling, shared fork inputs and bounded VRAM use.
- **M8.5** CPU/GPU parity gates, profiling and headless GPU CI evidence; clean CPU fallback when supported.
- **Exit:** documented baseline and performance deltas without regression or hidden GPU→CPU roundtrips.

### M9 — External shader adapters
- **M9.1** ISF metadata parsing, typed parameter discovery and minimum accepted subset.
- **M9.2** GL Transitions two-source/progress adapter, coordinate and time uniform bindings.
- **M9.3** Controlled multipass execution and explicitly bounded shader resources.
- **M9.4** Reject untrusted shader includes, unsupported operations and unsafe resource use.
- **M9.5** Golden images + provenance for pinned shader fixtures.
- **Exit:** supported imported shaders render reproducibly with mapped DanceRudiment controls.

### M10 — Authoring interface
- **M10.1** Graph node/port editor integrated with current React timeline workspace.
- **M10.2** Fork/merge/matte/transition connection authoring and semantic validation.
- **M10.3** Automatically generated parameter and DanceNotion control editors.
- **M10.4** Transition previews and scrub-safe frame evaluation with quality/performance switch.
- **M10.5** Round-trip JSON fixture and undo/redo tests.
- **Exit:** editor exports a valid project that matches CLI-rendered output.

### M11 — Temporal engine, media and CI showcases
- **M11.1** Temporal dependency declarations (past/future frame indexing) and deterministic scheduling.
- **M11.2** Feedback history with explicit delay, checkpoints and bounds; random-access reconstruction.
- **M11.3** Motion echoes, directional blur, slit-scan and time-remapping effects.
- **M11.4** Video input/output adapters with rational timebase handling.
- **M11.5** CI-published side-by-side gallery: music-driven Kandi Boom ⇄ visualiser, mattes and spectral transitions.
- **Exit:** sequential vs nonsequential renders agree; CI publishes inspected media and metadata.

### M12–M15 — Optional production and ecosystem integrations
- **M12 Colour + AOV:** OpenColorIO/ACES, HDR buffers, EXR depth/normals/velocity roundtrip.
- **M13 Timeline:** OpenTimelineIO clip/transition import-export, precise duration and source provenance.
- **M14 Computer vision:** optional ONNX segmentation/depth/flow outputs with model checksums and memory limits.
- **M15 Experimental adapters:** isolated OpenFX host, OpenUSD/Hydra, GStreamer/NDI/Spout/Syphon, Vulkan/WebGPU, OSC/MIDI. Each integration requires licensing review and CI capability declaration before shipping.
- **Exit:** no new hard runtime dependencies, pinned reproducible fixtures, explicit availability documentation.

## Test matrix and release evidence

| Test class | Cases | Gate |
| --- | --- | --- |
| Schema fuzzing | unknown kinds/fields, cycles, missing inputs, malformed types, NaN/inf, excessive bounds | Reject with actionable errors |
| Transition pixels | `p=0/1`, `p=0.5`, transparent RGB, feathered edges, reversed directions, radial centre | Exact endpoints; pixel tolerances ≤1 for 8-bit reference |
| Graph semantics | shared immutable forks, source reuse, merge order, matte and chroma across branches | Repeatable outputs and unmodified inputs |
| Controls | native named/recording motion, repeat clock, clamped ranges, smoothing, random frame order | Deterministic and provenance-tracked |
| Compatibility | without `effect_graph`, without NumPy/GPU extras, legacy post effects/routing | Unchanged baseline or explicit dependency gate |
| Integration | live `Project` → `Compositor` → RGB encoder and temporal supersampling | Entire pipeline runs without loss of alpha/mattes |
| GPU | texture transfer/parity, memory bounds, deterministic shader sampling | Baseline/golden comparison and measured resources |
| External formats | invalid/untrusted model, shader, media or file path | Explicit reject; no filesystem escape |
| Packaging | wheels, optional extras, supported Python versions, app build | Artifact installation and CI pass |

**No milestone is considered complete solely because isolated CPU tests pass.** The code branch must pass `python -m pytest tests -q` on a full checkout, Ruff, the relevant authoring checks, reproducible render-fixture comparisons, and CI review. Preserve skipped GPU/optional integration tests as clearly labelled evidence rather than counting them as a success.

## Research/integration references (further engineering verification required)

- OpenFX image-effect plugin API and transition contexts: https://openfx.readthedocs.io/
- GL Transitions shader convention and gallery: https://gl-transitions.com/
- Interactive Shader Format (ISF) metadata and multipass buffers: https://isf.video/
- OpenColorIO documentation: https://opencolorio.readthedocs.io/
- OpenImageIO and OpenEXR: https://openimageio.readthedocs.io/ ; https://openexr.com/
- FFmpeg filters and libplacebo: https://ffmpeg.org/ffmpeg-filters.html
- OpenTimelineIO: https://opentimelineio.readthedocs.io/
- ONNX Runtime execution providers: https://onnxruntime.ai/docs/execution-providers/
- OpenUSD/Hydra: https://openusd.org/
- GStreamer: https://gstreamer.freedesktop.org/

These are architectural reference points and prospective adapters. Merely citing a standard does not establish compatible runtime integration or performance.
