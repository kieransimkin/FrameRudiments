# M5.4 — Declarative transition chains, parallel processing and source effects

**Milestone:** M5.4 / FrameRudiments v22
**Baseline:** `6ce99b12f68e29c713503706aba226da51196be9` (merged M5.3)
**Status:** CPU graph compilation, integration fixtures and visual QA included. Main CI acceptance remains pending.

## Objective

Make a source renderable once and usable by multiple independently filtered
transition branches; permit a compact, ordered sequence of transitions using
three or more sources. Retain **one** execution engine, `EffectGraph`, and one
DanceRudiment control system. No new clock, mutable feedback, GPU rewrite or
implicit media re-rendering is introduced.

## Design: a compile-time macro, not another renderer

`transition_chain` is a declarative, bounded authoring node. During
`EffectGraph` construction, its sources and optional unary preprocessing stages
are expanded into regular effect nodes. Each transition stage becomes an
ordinary `transition` node. The public chain output becomes a regular `copy`
node with optional opacity. This means:

- Exactly the same alpha, matte, spectral, geometric, CPU/GPU and determinism
  rules apply as when writing all of the graph nodes manually.
- The first stage blends source 0 with source 1; the second blends that result
  with source 2; and so on, **in the declared source order**.
- An ordinary `composite` node may merge two or more separately authored
  transition chains in parallel. Explicit graph connections provide arbitrary
  nesting and independent branch postprocessing.
- Referencing an existing graph output instead of a raw `layer:<id>` permits
  all existing effect and mask nodes to be used as per-source preprocessing.
- The same underlying graph node is only evaluated once per frame even when
  several chains or forks refer to it; there is no duplicate original rendering.
- Every internal node has a stable address; the original JSON and its external
  image sources are not mutated by compilation or frame-local controls.

## Project configuration

```json
{
  "effect_graph": {
    "nodes": [
      {"id":"layer:character", "type":"source"},
      {"id":"layer:bars", "type":"source"},
      {"id":"layer:background", "type":"source"},
      {"id":"layer:mask", "type":"source"},
      {
        "id":"scene-chain", "type":"transition_chain",
        "inputs":["layer:character","layer:bars","layer:background"],
        "input_effects":[
          [{"id":"attenuate","type":"opacity","opacity":0.8}],
          [{"id":"rgb-split","type":"chromatic_displacement",
            "red_offset":[4.5,0],"blue_offset":[-4.5,0]}],
          []
        ],
        "stages":[
          {"id":"first","transition_style":"liquid",
           "progress":0.25,"feather":0.1,"amplitude":16},
          {"id":"masked","transition_style":"matte",
           "matte_input":"layer:mask","progress":0.6,"feather":0.2}
        ],
        "opacity":1
      },
      {"id":"final","type":"copy","inputs":["scene-chain"]}
    ],
    "output":"final"
  },
  "effect_graph_controls":[
    {"id":"intro-beat","node":"scene-chain::stage::first",
     "property":"progress","motion":{"kind":"named","pattern":"bounce"},
     "axis":"x","range":[0,1]},
    {"id":"bars-shift","node":"scene-chain::input::1::rgb-split",
     "property":"red_offset.x","motion":{"kind":"named","pattern":"circle"},
     "axis":"y","range":[-20,20]},
    {"id":"final-opacity","node":"scene-chain","property":"opacity",
     "motion":{"kind":"named","pattern":"circle"},
     "axis":"z","range":[0.5,1]}
  ]
}
```

### Generated addresses and controls

| Public item | Generated ordinary effect node |
|---|---|
| Chain output | `scene-chain` (`copy`) |
| Stage `first` | `scene-chain::stage::first` (`transition`) |
| Stage `masked` | `scene-chain::stage::masked` (`transition`) |
| Second source's `rgb-split` effect | `scene-chain::input::1::rgb-split` (`chromatic_displacement`) |
| First source's `attenuate` effect | `scene-chain::input::0::attenuate` (`opacity`) |

These addresses are stable across rearrangements of **stage and effect IDs**,
provided the input source *index* stays fixed. Stage IDs may be reordered while
preserving their control address; control behaviour follows the new declared
order. If an input is moved between indices, its preprocessing control address
changes. All ordinary `effect_graph_controls` numeric and categorical bindings
work on these generated nodes, using native motion time, lag, smoothing and
source provenance. There is no separate chain-control evaluator.

`EffectGraph.chain_expansions` lists the generated IDs for editor discovery.
After expansion, the existing project schema (`framerudiments.project.v1`) and
existing `effect_graph` property are unchanged.

### Shape and resource constraints

- `transition_chain.inputs`: 2–8 existing graph source/output IDs, in order.
- `stages`: exactly `len(inputs)-1` objects. Each stage requires a unique
  lowercase ID matching `[a-z][a-z0-9_-]{0,31}`. Other fields use the existing
  single-transition schema (dissolve, wipe, radial, matte, spectral, zoom,
  rotate, polar, kaleidoscope, liquid).
- A matte transition stage requires a `matte_input` graph node reference.
  Non-matte stages reject `matte_input`; graph cycles and missing references
  fail through existing DAG validation.
- `input_effects`: optional list of `len(inputs)` ordered lists, each allowing
  0–4 unary operations. Currently supported: `copy`, `opacity`,
  `chroma_key`, `chromatic_displacement`, `spectral_displacement`. To use a
  multi-input matte or a custom processing chain, author it in the main graph
  and reference that output as an input to `transition_chain`.
- Maximum 256 **expanded graph nodes**, including generated preprocess nodes
  and transition stages. Generated IDs must remain within 128 characters and
  cannot collide with any explicit or generated node ID.
- Node opacity defaults to 1; values must be finite 0–1. Invalid enum and
  numeric properties are validated by the original effect-node validators.
- No recursive macro expansion, loops, temporal state or implicit processing of
  unavailable layer sources. Missing but valid compositor elements retain the
  existing transparent RGBA representation.

## Performance / memory

The implementation does not render an entire chain into a new composite
renderer. It expands its nodes and evaluates them under the original DAG's
frame-local result table. Each intermediate RGBA stage occupies its normal
allocation; prior results are **not** currently lifetime-evicted during frame
execution. Full texture residency and lifetime tracking belong to M8. Avoid
claiming that a multi-source chain is free of memory or frame-processing cost.
Large 4K/8K frames require explicit project resource benchmarking.

## Verification plan

1. **Exact equivalence:** a two-stage declarative chain produces byte-identical
   RGBA pixels to the corresponding explicitly authored DAG.
2. **Preprocessing:** each input's unary filters execute independently; moving
   a chromatic channel does not alter another source or the original image.
3. **Modulation:** native DanceRudiment controls target generated stage and
   per-source effect IDs; resolved frame values remain stateless.
4. **Parallel:** two separate chains can be forked and merged with existing
   `composite` semantics; alpha values are unchanged from explicit nodes.
5. **Matte:** a chain stage's third matte source is a normal DAG dependency;
   cycles, dangling references and invalid matte arity fail before rendering.
6. **Invalid config:** reject unknown fields, invalid stage IDs, duplicate
   generated names, non-unary inline effects, unbounded stages/nodes and
   invalid parameter values.
7. **Determinism:** run frames out of order; compare SHA-256 hashes and verify
   source images and authored graph settings are unchanged.
8. **Full integration:** test a generated, checksum-pinned demo project with
   independent animated stages, and the no-graph legacy path.
9. **Visual QA:** generate a 3 × 6 contact sheet (single-stage, chained,
   parallel compositing) plus per-frame SHA-256 manifest.
10. **Release:** Ruff, Python 3.11/3.13 and Windows/Linux CI, full test suite,
    and the dedicated GPU workflow remain acceptance gates after merging.

### Run the focused tests

```bash
python -m pytest -q tests/test_effect_graph_transition_chains.py
python -m pytest -q tests/test_effect_graph_transition_chain_project.py
python integration/build_effect_graph_m5_4_visuals.py --output qa/m5-4
python -m pytest -q tests
ruff check .
```

## Remaining milestone work

M5.5: beat-locked progress envelopes, arbitrary non-monotonic phase maps,
reversible presets and repeat-clock/variable-BPM evidence. M8: graph resource
lifetimes, GPU-resident multi-source scheduling and CPU/GPU parity for the
non-accelerated transition styles. M10: visual graph authoring for stage IDs,
preprocessing controls and source routing.
