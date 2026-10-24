# Effect graph v11 — integration hardening and end-to-end branch contract

This increment adds graph-control binding collision validation and a two-branch motion-driven graph regression test. It deliberately preserves the existing `Motion` and `_Modulator` evaluation semantics and does not introduce a parallel signal engine.

## Integration contract

1. `effect_graph` is opt-in. Without it, legacy `post_effects`, layer masks, stage routing and rendered RGB outputs remain unchanged.
2. `effect_graph_controls` requires a graph and must resolve to a supported numeric property. Duplicate `set` bindings to the same target are rejected; explicitly combined controls remain possible.
3. Isolated surfaces are immutable inputs; branching must not alter the original compositor's retained matte sources.
4. At frame time, controls resolve from the native motion clock; graph values are evaluated without changing the graph definition.
5. The graph's output is composited into the existing final-frame rendering path. Its RGBA result is converted to RGB only at the existing output boundary.

## Mandatory integration verification before merging

- Apply to an actual clean `main` checkout with `git apply --check`, then run the complete Python test suite.
- Render one pinned, short example project with `effect_graph` absent, and assert identical output to the baseline.
- Render a second project with a fork, alpha matte, chroma key and independent branch opacity; compare frame hashes at two timestamps.
- Render a native `named` DanceRudiment-driven RGB displacement and assert the two sampled timestamps differ.
- Verify full input-file provenance, report evidence, temporal supersampling and non-graph project compatibility.

**Status:** The above full-project integration gates remain pending until run against the complete repository and CI. The isolated regression tests do not substitute for them.
