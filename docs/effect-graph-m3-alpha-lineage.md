# M3.2 — Original/current alpha lineage and matte ordering

**Baseline:** `4cfde09c5634fa93ea016417aa6ad77feff33323` (v14 merged).
**Status:** CPU implementation and independent tests in this increment; GPU parity is not claimed.

## Semantics

Graph `matte` and `chroma_key` nodes accept `alpha_source: "current" | "original"`.
The default `current` exactly preserves v14: apply matte or chroma selection against the
input image's *present* alpha. In `original` mode, use the inherited base source
alpha, before intermediate unary processing. The result is still bounded by the
selected matte/key silhouette and the node's independent `opacity`.

The graph evaluates with two pieces of per-node data:

- its immutable-by-contract straight RGBA image;
- its inherited original alpha reference, a single-channel `L` image.

`source` nodes create a new original-alpha reference **after their own opacity**;
`copy`, `opacity`, `matte`, `chroma_key` and `chromatic_displacement` inherit the
first input's original alpha. `transition` and `composite` nodes begin a new
alpha lineage after their output opacity, because they combine multiple images
and there is no unambiguous earlier source alpha. No parent buffer is modified;
original alpha images are shared references, not duplicated for every node.

**Important:** `original` can intentionally restore pixels previously removed by
an earlier key or matte. It is an explicit creative operation, **not** a claim
that those pixels can be perfectly recovered. Colour decontamination or a later
key can still affect the RGB channels independently.

## Pre- and post-effect mattes

No separate `placement` switch is needed or permitted. Placement is defined
unambiguously by DAG edges and can be different for each fork:

```text
layer:ink --> matte-pre --> chroma_key --> transition --> matte-post --> output
                  ^                             ^
layer:mask-a -----+          layer:mask-b -------+
```

A matte upstream of `chroma_key`, distortion or a transition restricts pixels
**entering** that effect; a matte downstream restricts the effect's **result**.
Graph composition order therefore offers pre/post placement without hidden
reordering, and the effect's output can fork into multiple matte placements.
`alpha_source` is orthogonal to this placement.

## Project example

```json
{
  "effect_graph": {
    "nodes": [
      {"id":"layer:ink","type":"source"},
      {"id":"layer:mask","type":"source"},
      {"id":"pre","type":"matte","inputs":["layer:ink","layer:mask"],
       "alpha_source":"current","mask_opacity":0.8},
      {"id":"key","type":"chroma_key","inputs":["pre"],
       "alpha_source":"original","key_color":"#00ff00","tolerance":0.08},
      {"id":"post","type":"matte","inputs":["key","layer:mask"],
       "alpha_source":"current","feather":2}
    ],
    "output":"post"
  },
  "effect_graph_controls": [
    {"id":"alpha-mode","node":"post","property":"alpha_source",
     "choices":["current","original"],
     "motion":{"kind":"named","pattern":"circle"},"axis":"x"}
  ]
}
```

`alpha_source` is a categorical native DanceRudiment control, using the same
motion clock and nearest-choice selection as v14 `mask_combine` and `key_space`.
The graph rejects unknown alpha modes before rendering.

## Visual acceptance and regression

`tests/test_effect_graph_alpha_lineage.py` verifies source-vs-processed alpha,
fanout independence, colour-key reconstruction limits, transition lineage reset,
node validation, categorical DanceRudiment choice selection and pre/post
matte ordering. `integration/build_effect_graph_m3_visuals.py` generates a
reproducible reference contact sheet from synthetic sources, with a JSON
manifest of image hashes and selected pixel values. It requires only Pillow,
NumPy (for transitions and keying) and the normal FrameRudiments installation.

The following gates remain open: run complete project render tests on all
supported CI platforms; optional ModernGL matte and key execution with
CPU/GPU numeric parity, deterministic colours at varied resolutions; visually
review real video footage and higher-quality despill/edge recovery. The CPU
reference deliberately does not silently claim GPU equivalence.

Generate the six-panel QA evidence with:

```bash
python integration/build_effect_graph_m3_visuals.py --output qa/m3-alpha
```

For automated CI protection:

```bash
python -m pytest -q tests/test_effect_graph_alpha_lineage.py tests/test_effect_graph_alpha_visuals.py tests/test_effect_graph_project_integration.py
```

The exact alpha-vector golden signatures are deliberately limited to the
Pillow-only masked scenes; the more complex key/transition scenes use meaningful
pixel and semantic comparisons across NumPy/Pillow versions.
