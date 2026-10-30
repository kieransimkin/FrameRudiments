# M5.3 — Deterministic geometric transitions

**Status:** CPU implementation and tests prepared against FrameRudiments `160fa884db7f3256e71553ef64fb1ae5e9aa78b8` (30 October 2026). End-to-end CI and GPU parity are subsequent acceptance gates.

## Goal

Extend the existing two-input `effect_graph` transition node with five transform-driven styles: `zoom`, `rotate`, `polar`, `kaleidoscope`, and `liquid`. Keep the source renderer, graph DAG, alpha lineage and the existing DanceRudiment control clock unchanged. Avoid new runtime dependencies beyond the existing optional NumPy effect-graph extra. Each transition is a deterministic function of two RGBA inputs, its validated configuration and its resolved per-frame controls.

## Types and visual algorithms

| Style | Source warp | Reveal geometry | Primary controls |
| --- | --- | --- | --- |
| `zoom` | Scale outgoing and incoming images around a normalised pivot | Radial reveal | `zoom`, `strength`, `center` |
| `rotate` | Counter-rotate samples around a pivot | Angular sector sweep | `rotation`, `strength`, `center` |
| `polar` | Rotate source coordinates proportionally to radius | Radial reveal with twist | `twist`, `strength`, `center` |
| `kaleidoscope` | Fold image coordinates into mirrored angular sectors | Repeated sector reveal | `segments`, `rotation`, `twist`, `strength` |
| `liquid` | Sinusoidal XY image displacement, with opposite outgoing/incoming offsets | Oscillating spatial wipe | `amplitude`, `frequency`, `phase`, `strength`, `direction` |

All styles use full-frame transparent-border bilinear sampling, premultiplied RGBA interpolation and straight RGBA output. Progress `0` and `1` return byte-identical input A and B respectively, before the graph node's optional universal output-opacity adjustment. Parameters `center`, `direction`, `progress`, and `feather` retain their earlier contracts. The algorithm accepts progress moving backwards or forwards; there is no mutable motion state.

### Example

```json
{
  "effect_graph": {
    "nodes": [
      {"id": "layer:character", "type": "source"},
      {"id": "layer:spectrum", "type": "source"},
      {"id": "liquid-swap", "type": "transition",
       "inputs": ["layer:character", "layer:spectrum"],
       "transition_style": "liquid", "progress": 0.4,
       "center": [0.5, 0.5], "direction": "right", "feather": 0.12,
       "strength": 0.85, "frequency": 3, "amplitude": 28, "phase": 0}
    ],
    "output": "liquid-swap"
  },
  "effect_graph_controls": [
    {"id": "kick-progress", "node": "liquid-swap", "property": "progress",
     "motion": {"kind": "named", "pattern": "bounce"},
     "axis": "x", "range": [0, 1]},
    {"id": "bass-waves", "node": "liquid-swap", "property": "amplitude",
     "motion": {"kind": "named", "pattern": "circle"},
     "axis": "y", "range": [5, 48]},
    {"id": "phase-drift", "node": "liquid-swap", "property": "phase",
     "motion": {"kind": "named", "pattern": "circle"},
     "axis": "x", "range": [-3.14, 3.14]}
  ]
}
```

### Validated parameters

| Parameter | Default | Range / unit |
| --- | --- | --- |
| `progress` | 0 | 0–1; endpoint overrides exact |
| `feather` | 0 | 0–1; at least one pixel of antialiasing for geometry styles |
| `center` | `[0.5, 0.5]` | Each coordinate 0–1; independently controllable |
| `direction` | `right` | `right`, `left`, `up`, `down` |
| `strength` | 0.75 | 0–1 |
| `zoom` | 1 | 0–4 additional scale |
| `rotation` | 180 | −720 to +720 degrees |
| `twist` | 0.75 | −4 to +4 turns across radius |
| `segments` | 6 | Integer 2–24; animated values rounded at evaluation |
| `frequency` | 4 | 0–32 cycles across frame |
| `amplitude` | 24 | 0–256 output pixels |
| `phase` | 0 | −8π to +8π radians |
| `opacity` | 1 | 0–1, inherited from universal graph-node opacity |

All numeric parameters are available through native `effect_graph_controls`, with the same source-bound curves, smoothing, combination, lag and evidence as other effects. Geometric parameters may be serialized together to allow switching between geometric styles; only those relevant to the active style affect the image. Non-geometric transition styles reject geometric-only settings. Switching a two-input geometric transition into a three-input matte transition, or into a spectral transition without appropriate band configuration, is rejected at project preparation.

## Alpha, colour and determinism

- Perform bilinear sampling on **premultiplied colour**, not unassociated RGB. Transparent green pixels must not contaminate sampled red or blue edges.
- A sample outside the source bounds contributes zero alpha and premultiplied colour. There is no edge clamping, looping or implicit crop.
- Evaluate each output frame in row tiles of at most 48 rows by default to limit transient memory; the source buffers remain per-frame and must not be mutated.
- Sampled frames are stateless; exact timestamps can be rendered in any order, including high-quality temporal supersampling.
- The CPU reference currently uses display-referred sRGB channels for interpolation, matching previous transition nodes. Scene-linear/HDR output and 3D-aware warps are deferred.

## Backend rules

The M5.3 reference uses optional NumPy, installed via `pip install -e ".[effect_graph,dev]"` or the existing GPU extra. **No geometric GPU shader is claimed in this milestone.** With `effect_graph_backend="gpu"`, the graph must reject a geometric transition rather than report it as accelerated. With `auto`, it may run the CPU reference, recording a CPU fallback. Full GPU-resident graph scheduling and GPU-specific geometric effects belong to M8.

## Testable delivery gates

- **G1 Schema:** reject wrong input counts, unknown properties, NaN, invalid radii/angles, invalid segment count and numeric range violations before rendering.
- **G2 Exactness:** progress endpoints reproduce source RGBA bytes, intermediate render is deterministic and input images are unchanged.
- **G3 Alpha:** transparent RGB remains invisible, warped pixels outside image bounds are transparent, with no premultiplication fringes.
- **G4 Pipeline:** source forks, compositing, opacity and existing transitions still produce correct output; geometric controls do not mutate graph configurations.
- **G5 Controls:** independently bind progress, centre and geometric parameters to native DanceRudiment sources with repeatable values at arbitrary frames.
- **G6 Evidence:** generate a reproducible 5×6 style/progress contact sheet and SHA-256 manifest from synthetic inputs.
- **G7 Performance:** CPU benchmark reports measured latency and output hash at small/HD/4K sizes; 8K requires explicit opt-in. These are not GPU performance claims.
- **G8 CI:** full Python CI matrix, Ruff, source isolation and actual project-render fixture pass. GPU-specific parity is deferred until GPU geometric effects are implemented.

## Commands

```bash
python -m pytest -q tests/test_effect_graph_transition_geometry.py tests/test_effect_graph_transition_geometry_visuals.py
python -m pytest -q tests/test_effect_graph_transition_geometry_project.py
python integration/build_effect_graph_m5_3_visuals.py --output qa/geometric-transitions
python integration/benchmark_effect_graph_geometry.py --size small --repeats 2
python -m pytest -q tests
ruff check .
```

## Future milestones

M5.4 continues transition chains, nested branch outputs and optional per-input preprocessing. M5.5 adds beat-locked progress curves and nonmonotonic timing presets. M8 adds GPU-native geometric transitions, texture-resident graph composition, and cross-device parity and performance measurements.
