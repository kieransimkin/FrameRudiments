# M3 — Universal alpha, track-matte and chroma-key processing

**Implementation increment:** 2026-10-27 (CPU reference).
**Baseline:** FrameRudiments main `8d038dab00caf03335a5344f68a05640df59d7df` (v13 merged).
**Status:** CPU reference, numeric and categorical DanceRudiment controls implemented; GPU parity and advanced UI remain future gates.

## Inputs, ordering and alpha contracts

Every `effect_graph` node exposes an `opacity` scalar in `[0,1]`, applied to its **output alpha** after the node has finished processing. This includes sources, `matte`, `chroma_key`, transitions, composites, copies and chromatic displacement. `opacity=1` retains the previous output. Per-node opacity is different from a matte's `mask_opacity` (how fully the matte restricts the foreground), and both are independently controllable.

The `matte` node accepts `[foreground, matte1, ... matte8]` (2..9 graph inputs). Each provider can be any reachable graph branch or captured compositor source. A matte's mask channel is either `alpha` or **alpha-weighted luminance**; completely transparent white input never reveals the foreground. Inputs are equal-sized straight RGBA and are not modified.

Order of operations for the `matte` node:

1. Extract a mask from each provider, using alpha or alpha-weighted luminance.
2. Combine masks *in input order*, using the selected `mask_combine` operation.
3. `expand`: positive dilation / negative erosion, signed fractional pixels.
4. Gaussian `feather`, measured in pixels.
5. Optional `invert`.
6. `mask_opacity`: blend the processed mask toward full white; zero bypasses masking and one applies it fully.
7. Multiply the foreground's original alpha by the mask, then apply the node's independent `opacity`.

Mask combination modes: `multiply` (default, legacy-compatible), `min`, `max`, `add` (saturating), `subtract` (first minus subsequent, saturating), `difference` (absolute difference), and `screen` (probabilistic union). `mask_combine`, `matte_channel` and `invert` are also DanceRudiment-controllable categorical properties using a validated `choices` array; the existing native motion clock supplies a nearest-choice index.

## Chroma key

`chroma_key` selects one `key_color` (legacy setting) or up to eight `key_colors`; when both are supplied, the primary `key_color` is prepended. Pixels matching *any* selected colour are removed, based on distance to the nearest key colour. `key_space=rgb` (default) retains the existing RMS sRGB distance for compatibility; `key_space=oklab` compares colours in perceptually oriented OKLab space. Tolerance and softness are both normalized numbers in `[0,1]`; thresholds are not numerically equivalent between spaces, so authoring UI should expose recommended presets rather than auto-convert them.

After extracting the keep mask, `edge_expand` (signed -64..64 px) and `edge_feather` (0..128 px) refine its silhouette. The mask always multiplies the **original source alpha**, so key dilation can never reveal pixels outside the original silhouette. Existing `despill` (0..1) suppresses excess key-dominant channel contribution on partially keyed edges. Optional `edge_decontaminate` (0..1) replaces residual edge colour using an `edge_radius` (0.5..16 px) spatial average of confidently retained nearby pixels. Clean interior pixels are unchanged. Decontamination is not a full background colour reconstruction or spill-estimation algorithm.

The implementation iterates key colours without allocating an `[height,width,number_of_keys,3]` temporary cube, which avoids an especially costly intermediate on 4K and 8K frames. The CPU reference uses NumPy for chroma keying; the unchanged Pillow-only matte pipeline does not require NumPy.

## Example project graph

```json
{
  "effect_graph": {
    "nodes": [
      {"id":"layer:foreground","type":"source"},
      {"id":"layer:matte-a","type":"source"},
      {"id":"layer:matte-b","type":"source"},
      {"id":"key","type":"chroma_key", "inputs":["layer:foreground"],
       "key_space":"oklab", "key_colors":["#00ff00", "#00c8b0"],
       "tolerance":0.08, "softness":0.06, "despill":0.35,
       "edge_decontaminate":0.4, "edge_radius":3},
      {"id":"masked","type":"matte", "inputs":["key","layer:matte-a","layer:matte-b"],
       "matte_channel":"luminance", "mask_combine":"screen",
       "expand":1.5, "feather":2, "mask_opacity":0.9, "opacity":0.75}
    ],
    "output":"masked"
  },
  "effect_graph_controls": [
    {"id":"key-soften","node":"key","property":"edge_feather",
     "motion":{"kind":"named","pattern":"circle"},"axis":"x","range":[0,8]},
    {"id":"matte-growth","node":"masked","property":"expand",
     "motion":{"kind":"named","pattern":"bounce"},"axis":"y","range":[-3,5]},
    {"id":"branch-opacity","node":"masked","property":"opacity",
     "motion":{"kind":"named","pattern":"circle"},"axis":"z","range":[0.2,1]}
  ]
}
```

## Numeric control registry (native DanceRudiment/DanceNotion)

| Node | Address | Range | Unit |
| --- | --- | --- | --- |
| All graph node types | `opacity` | 0..1 | fraction |
| Matte | `mask_opacity` | 0..1 | fraction |
| Matte | `expand` | -64..64 | pixels |
| Matte | `feather` | 0..128 | pixels |
| Chroma key | `key_color.r`, `.g`, `.b` | 0..255 | encoded sRGB channel |
| Chroma key | `tolerance`, `softness`, `despill` | 0..1 | fraction |
| Chroma key | `edge_expand` | -64..64 | pixels |
| Chroma key | `edge_feather` | 0..128 | pixels |
| Chroma key | `edge_decontaminate` | 0..1 | fraction |
| Chroma key | `edge_radius` | 0.5..16 | pixels |

All numeric addresses use the existing `effect_graph_controls` and the native `_Modulator`: original musical clock, source provenance, curves, combinations, lag and smoothing. Overrides are validated at frame evaluation and never mutate the authored graph. `key_colors.N.r/g/b` also independently controls each additional key colour (zero-indexed N, for existing entries only). With `key_colors` and no `key_color`, `key_color.r/g/b` aliases the first key rather than creating another one.

Categorical controls support `matte_channel` (`alpha`,`luminance`), `mask_combine` (seven modes), `invert` (true/false), `key_space` (`rgb`,`oklab`), `transition_style`, `direction` and composite `mode`. Their control records use `choices` instead of `range`, for example:

```json
{"node":"masked", "property":"mask_combine", "choices":["multiply","screen","max"],
 "motion":{"kind":"named","pattern":"circle"}, "axis":"x"}
```

Options are validated before any native motion source is loaded. Selection is stateless nearest-index: deterministic for random frame access but no hysteresis yet. Categorical drivers require `combine=set` and `weight=1` and do not accept explicit `range`. The native transfer curve, phase and smoothing still apply.

## Required test gates

- `tests/test_effect_graph_mattes.py` and `tests/test_effect_graph_m3_modulation.py`: multi-input combinations, original alpha invariants, morphology including fractional radius, opacity semantics, OKLab/multi-colour keying, edge processing, invalid values and per-frame controls.
- `tests/test_effect_graph.py`: updated legacy invalid-control expectation because `opacity` is now *valid* for chromatic-displacement outputs.
- `tests/test_effect_graph_project_integration.py`: existing synthetic, hash-pinned compositor test to be run unchanged as a backwards compatibility gate.
- `python -m pytest tests -q` and Python 3.11/3.13 CI matrix, with optional `[effect_graph,dev]` extra for NumPy-backed chroma operations.
- Capture visual golden evidence under `docs/media/` before declaring perceptual keying production-grade.

## Remaining work and dependencies

1. M3.5: Categorical modulation and per-entry key colours are implemented; add parameter metadata discovery, UI widgets, and optional phase-locked hysteresis in the authoring milestone.
2. M3.6: original-vs-current alpha selection and configurable pre-/post-effect mask placement, including nested group semantics.
3. M3.7: GPU kernels, CPU/GPU parity image fixtures, bounds tests at 4K/8K, performance and memory budgets.
4. M3.8: advanced keyer refinements (colour-space calibrated tolerances, spill-preserving skin/subject edges, premultiplied colour reconstruction and HDR references).
5. M3.9: editor controls, live previews and visual proof across sprites, text, 3D and video sources.

No new hard dependency is introduced. The new helpers remain within the existing Python/Pillow/optional NumPy setup; GPU and external model integrations are independent later milestones.

## M3.2 update: inherited alpha versus input alpha

The original v14 statement that a matte uses the foreground's "original alpha"
meant the alpha of the image *provided to that matte node*. Starting with M3.2,
`alpha_source` makes the distinction explicit: `current` (default) uses that
processed input alpha, while `original` follows the first source's alpha through
unary effects. After a transition or composite, the resulting image begins a
new alpha lineage. See [M3.2 alpha lineage and matte ordering](effect-graph-m3-alpha-lineage.md)
for reference graphs, exact guarantees and visual acceptance cases.
