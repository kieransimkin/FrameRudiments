# Effect graph increment v7: subpixel chromatic displacement

Chromatic displacement now uses bilinear sampling for independently controlled fractional X/Y offsets on each RGB component. Sampling outside the source returns zero; the original alpha channel remains unchanged. Integer displacement remains exact. The host resolves control values before invoking `EffectGraph.render`.

## Tests

`python -m pytest tests/test_effect_graph.py -q` now verifies horizontal and vertical half-pixel displacement, transparent-border behaviour, original alpha preservation and source immutability. This is CPU reference behaviour; GPU parity and integration with the native DanceRudiment modulation evaluator remain pending.

## Dependency note

This CPU graph currently uses NumPy in its chromatic and keying operations. Before enabling the graph for default installs, either declare NumPy as a mandatory dependency or add a Pillow-only fallback. It is presently available under the existing `gpu`, `materials` and `dev` extras.
