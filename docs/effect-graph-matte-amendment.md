# Mandatory matte, chroma-key and opacity contract

This document amends `effect-graph-design.md` and `effect-graph-implementation-plan.md`.

Every effect result, branch, and composite input SHALL expose independent opacity, blend mode, optional alpha/luma/colour/depth/track matte, and chroma-key configuration. These are properties of graph edges and outputs, not restricted to individual effect plugins. Matte placement SHALL support pre-effect and post-effect modes and explicit matte nodes at arbitrary graph positions. A hidden track-matte source may be consumed by multiple branches without being composited visibly.

Keying SHALL support selected key colour(s), perceptual colour distance, tolerance, softness, saturation and luminance gating, despill, edge decontamination, inversion and multiple-key combination. Sampling unassociated RGB SHALL guard zero-alpha pixels. Mask arithmetic SHALL operate on alpha, preserve partial coverage, and define clipping, transforms and resolution matching. Every meaningful scalar, vector, colour and categorical choice SHALL be addressable through the existing DanceRudiment/DanceNotion control mechanism; no new independent audio analysis or motion clock may be introduced.

## Milestone M2A (required after graph CPU executor, before advanced effects)

- M2A.1 Define validated opacity/matte/key schema, stable source IDs, pre/post placement, and versioned migration. Test: invalid/missing/cyclic references fail before rendering.
- M2A.2 Implement alpha and luminance track mattes with invert, multiply, union, intersection, subtract, erosion/dilation and feather. Test: golden RGBA images and nested fork/merge cases.
- M2A.3 Implement perceptual chroma key with multi-key selection, tolerance, feather, despill and edge decontamination. Test: green/blue-screen fixtures including hair, transparency and antialiasing.
- M2A.4 Bind every control to native modulation and prove deterministic random-access frame evaluation. Test: out-of-order renders byte-identical to sequential stateless results.
- M2A.5 Implement GPU parity and authoring controls. Test: CPU/GPU alpha errors within documented tolerance, visual CI contact sheet, validation of non-rendered matte sources.

M2A is not complete until all five acceptance tests pass. The initial `effect_graph.py` module is intentionally only an isolated CPU graph prototype supporting source, copy, opacity, alpha matte and compositing; it is not yet wired into project loading, DanceNotion or chroma-key rendering.
