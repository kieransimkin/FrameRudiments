# Effect graph v10 — native DanceRudiment controls

Add `effect_graph_controls` to a project with `effect_graph`. Each binding specifies `node`, `property`, and the existing native `motion` configuration plus optional `axis`, `range`, `curve`, `combine`, `weight`, `lag_frames`, `smoothing_frames`, etc. The adapter delegates sampling and mapping to `modulation._Modulator`, preserving source-clock and historical-frame sampling. Example:

```json
"effect_graph_controls": [{"id":"kick-red-x", "node":"rgb-split", "property":"red_offset.x", "motion":{"kind":"named","pattern":"bounce"}, "axis":"y", "range":[-12,12]}]
```

Bindings are checked against the graph's target whitelist before motion loading. Resolved values are frame-local and do not mutate graph nodes. No new DSP or clock authority is introduced. Note: `effect_graph_controls` is only meaningful alongside `effect_graph` and is currently evaluated on CPU. Full render fixtures, dependency file provenance integration, and GPU parity remain outstanding.
