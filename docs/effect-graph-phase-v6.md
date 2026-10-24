# Effect graph increment v6: independently modulated RGB vectors

This cumulative patch extends the CPU effect graph's per-frame resolved control contract.

## New controls

`parameter_values` accepts `(node_id, property)` keys. For chromatic displacement nodes, properties `red_offset.x`, `red_offset.y`, `green_offset.x`, `green_offset.y`, `blue_offset.x`, and `blue_offset.y` each accept finite pixel displacements in [-4096, 4096]. Each component is independent and never modifies the stored graph configuration. Chroma-key `tolerance`, `softness`, `despill`, matte `feather`, and opacity-node `opacity` remain independently overridable.

The existing host must resolve DanceRudiment / DanceNotion control bindings before passing values to `EffectGraph.render`. This increment does **not** claim to integrate the native binding evaluator. Invalid node/property pairs are rejected instead of silently ignored.

## Verification

Run `python -m pytest tests/test_effect_graph.py -q`. New tests cover two-frame override isolation, independent chromatic channel motion, invalid targets, bounds, and keying parameter changes. Full project render/CI and GPU parity are still outstanding.

## Next gate

Introduce a project-level validated control binding map and adapt the existing `RudimentModulations` evaluator without inventing a second motion-source format. Verify with a pinned example project and end-to-end render.
