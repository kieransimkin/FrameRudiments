# M5.2 — Dance-modulated spectral transitions and spatial colour echoes

**Implementation increment:** v20 (29 October 2026)
**Source baseline:** `7c4e93a31b647ec5c6df3863ea712dedf7c36be6` (M5.1)
**Scope:** bounded deterministic CPU reference; GPU transition shader is a separate parity gate.

## Design and compatibility contract

The existing `transition` node gains `transition_style: "spectral"` with **exactly two RGBA inputs**. Its output is straight RGBA with colour interpolation performed in premultiplied display-referred RGBA; scene-linear/HDR interpolation is not claimed. The already supported dissolve, wipe, radial and three-input matte transitions are unchanged. No files, model weights, temporal history or secondary clock are used.

An authored list of **1–8 independent colour bands** steers the transition. Each band can select outgoing and incoming colours separately, in RGB/HSV/OKLab space; the band progressively replaces the usual two-source dissolve with an individually timed, displaced and optionally recoloured dissolve. Unselected pixels continue with the ordinary global progress interpolation. The two exact endpoints return the unmodified source images (prior to the graph node's universal output-opacity stage).

```json
{
  "effect_graph": {
    "nodes": [
      {"id": "layer:character", "type": "source"},
      {"id": "layer:visualiser", "type": "source"},
      {
        "id": "spectral-swap", "type": "transition",
        "inputs": ["layer:character", "layer:visualiser"],
        "transition_style": "spectral", "progress": 0.35,
        "mix": 1, "feather": 0.4, "alpha_mode": "expanded",
        "bands": [
          {"id": "warm-highlights", "source_color": "#ff5020", "to_color": "#2854ff",
           "target_color": "#a040ff", "space": "oklab", "mask_source": "union",
           "tolerance": 0.09, "softness": 0.07,
           "start": 0, "end": 0.45, "opacity": 1, "recolor": 0.55,
           "offset_from": [-12.5, 0], "offset_to": [8, 1.5],
           "echoes": 2, "echo_decay": 0.4},
          {"id": "cool-shadows", "source_color": "#18dfe8", "to_color": "#eb20b8",
           "space": "hsv", "tolerance": 0.08, "softness": 0.1,
           "start": 0.5, "end": 1, "offset_to": [0, -8]}
        ]
      }
    ],
    "output": "spectral-swap"
  },
  "effect_graph_controls": [
    {"id": "beat-progress", "node": "spectral-swap", "property": "progress",
     "motion": {"kind": "named", "pattern": "circle"}, "axis": "x", "range": [0, 1]},
    {"id": "bass-red-shift", "node": "spectral-swap", "property": "bands.warm-highlights.offset_to.x",
     "motion": {"kind": "named", "pattern": "bounce"}, "axis": "y", "range": [-20, 20]}
  ]
}
```

## Parameter schema

| Parameter | Type / range | Function |
| --- | --- | --- |
| `progress` | float 0–1 | Native DanceRudiment-controllable transition time, reversible and non-monotonic |
| `mix` | float 0–1 | Strength of spectral processing relative to an ordinary dissolve |
| `feather` | float 0–1 | Interpolation between linear and smoothstep **temporal** reveal within each band |
| `alpha_mode` | `expanded`, `source` | Permit shifted alpha outside original silhouettes, or clip to union of original silhouettes |
| `bands` | array 1–8 | Ordered colour-band operations; later bands composite over earlier bands |
| `bands.<id>.source_color` | CSS RGB colour | Selected outgoing colour; channels independently controllable |
| `bands.<id>.to_color` | CSS RGB colour | Selected incoming colour (default outgoing colour) |
| `bands.<id>.target_color` | CSS RGB colour | Optional incoming-colour replacement (default incoming selection) |
| `bands.<id>.space` | `rgb`, `hsv`, `oklab` | Perceptual colour-selection metric |
| `bands.<id>.mask_source` | `from`, `to`, `union`, `intersection` | How alpha-weighted colour matches are combined |
| `bands.<id>.tolerance`, `.softness` | float 0–1 | Colour-selection core/rolloff |
| `bands.<id>.start`, `.end` | floats 0–1, start < end | Independent reveal windows; validated again after frame-local controls |
| `bands.<id>.offset_from.x/y`, `.offset_to.x/y` | floats −4096…4096 px | Fractional outgoing/incoming image-space displacements |
| `bands.<id>.opacity` | float 0–1 | Band's influence on the base dissolve |
| `bands.<id>.recolor` | float 0–1 | Incoming RGB replacement strength during the transition |
| `bands.<id>.echoes` | integer 0–4 | Stateless extra spatial offset samples; never historical video frames |
| `bands.<id>.echo_decay` | float 0–1 | Spatial-echo contribution |

Stable band IDs are part of the control path. Reordering bands does not break bindings. Category controls (`space`, `mask_source`, `alpha_mode`) reuse native `choices` and cannot be multiplied by continuous values. All numeric properties are independently addressable via `effect_graph_controls`; no new DanceRudiment evaluator or beat clock is introduced.

A `spectral` transition is **structural**, so changing `transition_style` to an incompatible mode using a categorical control is rejected at validation. To switch styles, use separate effect-graph branches, and later an explicit graph switch/mix node. A transition with a third matte input must remain a `matte` transition.

## Pipeline algorithm

1. At project preparation, validate band count, stable IDs, colour strings, selection modes, bounds, echo counts and input arity.
2. At the requested frame, resolve native DanceRudiment values without altering graph definitions; revalidate resulting band intervals and controls.
3. For an intermediate progress, compute the ordinary straight-alpha output using **premultiplied** source contributions. Endpoints zero/one return exact outgoing/incoming RGBA copies.
4. Process in bounded 64-row stripes; for each band evaluate alpha-weighted colour coverage of outgoing/incoming *unassociated* source colours. Transparent RGB cannot contribute. Selection can be from either source, union or intersection.
5. Compute local band progress from its `[start,end]` window; optional `feather` applies smoothstep easing. Bilinearly sample outgoing and incoming RGB **premultiplied by alpha** at their independent animated offsets with a zero-alpha border.
6. Mix sampled contributions by local band progress; optionally replace incoming RGB colour and apply up to four displaced spatial echo samples. Blend the band into the previously processed result by its colour-selection strength, opacity and global `mix`.
7. Clip alpha to the source-silhouette union when `alpha_mode=source`; otherwise permit displaced alpha. Convert back to straight RGBA at the node boundary. Subsequent layer mattes, opacity and graph composites work as before.

The implementation keeps both full input images in byte RGBA and only allocates floating intermediate arrays per stripe. At 8K the CPU path is expensive (especially multiple HSV/OKLab bands with echoes); neither HD frame rate nor GPU performance is promised. No temporal frame cache is introduced.

## Dependencies and deployment

- Existing Python 3.11+, Pillow 10+, native DanceRudiment motion: unchanged.
- NumPy ≥1.26 via existing `effect_graph` optional extra is needed when this node evaluates; it is **not** imported by no-graph projects.
- GPU: the current ModernGL backend has no spectral-transition shader. `effect_graph_backend=auto` executes this node using CPU and records a fallback; `gpu` strict mode rejects it. This is not the M4.2 spectral-*displacement* shader.
- Synthetic visual evidence needs Pillow only beyond the renderer's optional NumPy dependency; no font or private media.

## Testable deliverables and acceptance

- **M5.2.1** Independent colour selection (`source_color`, `to_color`) with RGB/HSV/OKLab; compare selected vs unselected pixels and transparent edges.
- **M5.2.2** Band-local progress windows and arbitrary-time evaluation. Render frame N, another frame, then N again and verify identical bytes.
- **M5.2.3** Independent fractional XY displacement for both sources; transparent-border and alpha clipping tests.
- **M5.2.4** Recolouring and bounded spatial echoes, with fixed-input deterministic pixel comparisons.
- **M5.2.5** Stable DanceRudiment paths, enum choice validation, reordering and invalid-time-range rejection.
- **M5.2.6** Graph fork/merge and follow-on mattes; unchanged legacy transitions.
- **M5.2.7** Generated 12-panel QA contact sheet and manifest with SHA-256 hashes, plus configurable CPU timing tool.
- **M5.2.8** Full-project smoke test with hash-pinned synthetic assets, real native `named` motion and non-sequential render checks.
- **M5.2.9** Full repository Ruff + pytest CI matrix and optional rendering suite. **Pending until merged CI completes.**

Useful commands:

```sh
python -m pytest -q tests/test_effect_graph_transition_spectral.py tests/test_effect_graph_spectral_transition_visuals.py
python -m pytest -q tests/test_effect_graph_spectral_transition_project.py
python -m pytest -q tests
python integration/build_effect_graph_m5_2_spectral_visuals.py --output qa/m5-2
python integration/benchmark_effect_graph_m5_2_spectral.py --size small --repeats 3
```

## Known limitations and M5.3 / M8 handoff

- Spatial echoes are within-frame re-samples, **not** optical flow, motion-vector interpolation or recursive history.
- Selection/interpolation is display-referred; scene-linear/HDR handling is a later colour-pipeline milestone.
- Exceeding configured limits fails validation rather than silently changing effect meaning.
- GPU-native multi-band transition processing, device parity and 4K/8K benchmarks remain **M8** work; strict GPU rejects unsupported spectral transitions.
- **M5.3:** zoom, rotate, polar and kaleidoscope transitions, followed by fluid displacement, with native controls and deterministic edge sampling.
- **M5.4:** transition mixing/stacking and multi-stage branching, including reusable groups and nested mattes.
- **M5.5:** dedicated beat-locked curves and cue-controlled durations (continue using native source clock).

## Existing M5.1 integration test correction

The M5.1 full-project CI at `7c4e93a` had a single failed test: it assumed a binary matte must visibly respond to every progress value, even though the selected mask was saturated. The updated test uses a soft matte, checks resolved native progress and then verifies a visibly changing frame at an appropriate timestamp; production M5.1 defaults are unchanged.
