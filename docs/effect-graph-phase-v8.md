# Effect graph increment v8: controllable chroma-key colour

The chroma-key node now supports independent per-frame `key_color.r`, `key_color.g` and `key_color.b` control targets (0–255). The host can resolve these from DanceNotion or native DanceRudiment bindings and pass them to `EffectGraph.render(parameter_values=...)`. Controls are validated, applied to a frame-local configuration, and never mutate the original node definition. Multiple component overrides compose deterministically.

## Acceptance tests

`python -m pytest tests/test_effect_graph.py -q` covers independent component updates, frame-local isolation, and invalid bounds, nonfinite and boolean values. This is a CPU graph parameter contract, **not** native DanceNotion evaluator integration. The current chroma-key algorithm remains a basic straight-RGB colour-distance key, with advanced despill and perceptual selection outstanding.

## Remaining integration gates

The graph must be exercised by a complete project render, with real motion bindings, before claiming end-to-end integration. NumPy remains an optional extra in `pyproject.toml` although some graph effects require it; dependency policy needs resolving before general release.
