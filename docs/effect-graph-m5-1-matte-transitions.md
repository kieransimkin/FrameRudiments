# M5.1 — Matte-, luminance- and chroma-driven transitions

**Phase:** M5.1 of the [unified effects and transitions roadmap](unified-effects-transitions-rendering-roadmap.md)
**Scope:** deterministic CPU reference, validated three-input DAG transitions, and native DanceRudiment controls.
**Baseline:** `0f95fe1f379550b855a51095d3eee18ef4e513af` (29 October 2026).
**Compatibility:** the existing two-input `dissolve`, `wipe`, and `radial` styles retain their original behaviour.

## Input and graph contract

`transition_style: "matte"` requires three ordered input graph nodes:

1. **A:** outgoing RGBA layer or forked graph branch.
2. **B:** incoming RGBA layer or branch.
3. **M:** any RGBA surface used to compute a reveal mask (including another effect's output).

M is **not** implicitly composited into the result. The output is a normal straight-RGBA graph surface and can be forked, masked, keyed, transitioned again, or composited over another branch. All three inputs must have equal dimensions. The graph validates source identities, input count and same-frame cycles; unknown sources fail at preparation. A valid but inactive source is provided as a transparent surface by the existing compositor bridge.

```json
{
  "effect_graph": {
    "nodes": [
      {"id": "background", "type": "source"},
      {"id": "frame", "type": "source"},
      {"id": "layer:native", "type": "source"},
      {
        "id": "reveal",
        "type": "transition",
        "inputs": ["background", "frame", "layer:native"],
        "transition_style": "matte",
        "matte_channel": "alpha",
        "progress": 0.5,
        "threshold": 0.5,
        "feather": 0.12,
        "mask_gamma": 1.0,
        "invert": false,
        "opacity": 1.0
      }
    ],
    "output": "reveal"
  },
  "effect_graph_controls": [
    {
      "id": "beat-reveal",
      "node": "reveal",
      "property": "progress",
      "motion": {"kind": "named", "pattern": "circle"},
      "axis": "x",
      "range": [0, 1],
      "smoothing_frames": 2
    }
  ]
}
```

No independent DSP, beat detector or oscillator is introduced; native `effect_graph_controls` drive the node using the existing project clock, motion provenance and deterministic source-time sampling.

## Mask selection

| `matte_channel` | Selection rule | Transparent mask behaviour |
|---|---|---|
| `alpha` (default) | M's original alpha | Transparent selects nothing |
| `luminance` | Pillow-compatible luma of RGB multiplied by alpha | Transparent bright RGB selects nothing |
| `chroma` | Soft RGB or OKLab colour-key **selection**, using the existing M3 keyer | Fully transparent selected colour selects nothing |

In `chroma` mode, pixels **matching** `key_color` (default green) reveal B. This is the inverse of the traditional *remove-key-colour* meaning of the standalone `chroma_key` node. Selection uses the existing keyer and then `source_alpha - kept_alpha`, so it preserves soft key boundaries and avoids exposing colour values from transparent pixels. Key settings can remain configured while an animated `matte_channel` chooses a different mode.

- `key_color` — CSS colour (`#00ff00` by default); independent `.r/.g/.b` controls in 0–255.
- `key_space` — `rgb` or `oklab`; native categorical selection.
- `tolerance`, `softness` — independently controllable 0–1. Threshold matching follows the same metric as M3.
- `matte_channel` and `invert` — categorical DanceRudiment controls with validated choices.

## Reveal and alpha mathematics

Let `m ∈ [0,1]` be mask strength, `p` be transition progress, `t` the authored threshold, `f` the feather and `g` the gamma. Optional inversion occurs before gamma:

```
m' = (invert ? 1-m : m)^g
cutoff = t + 0.5 - p
weight = f>0 ? smoothstep(clamp((m'-cutoff)/f+0.5,0,1)) : (m'>=cutoff)
```

`progress=0` is **exactly A** and `progress=1` is **exactly B**, regardless of matte content or threshold. Between endpoints the implementation interpolates A and B in **premultiplied display-referred RGBA**, then safely unassociates colour into the established straight RGBA output. Transparent RGB cannot contribute visible colour. Scene-linear/HDR transition blending remains a later colour-pipeline milestone.

| Parameter | Type | Range | Default | DanceRudiment |
|---|---|---|---|---|
| `progress` | float | 0–1 | 0 | Yes |
| `threshold` | float | 0–1 | 0.5 | Yes |
| `feather` | float | 0–1 | 0 | Yes |
| `mask_gamma` | float | 0.1–8 | 1 | Yes |
| `invert` | enum/boolean | true/false | false | Yes, choices |
| `matte_channel` | enum | alpha/luminance/chroma | alpha | Yes, choices |
| `key_color.r/g/b` | channel | 0–255 | 0/255/0 | Yes |
| `key_space` | enum | rgb/oklab | rgb | Yes, choices |
| `tolerance` | float | 0–1 | 0.1 | Yes |
| `softness` | float | 0–1 | 0.1 | Yes |
| `opacity` | float | 0–1 | 1 | Yes |

`direction`, `center`, and `transition_style` remain exposed for the existing two-input transition family. A live categorical `transition_style` change **cannot change input arity**. In particular, two-input wipes cannot suddenly become three-input matte transitions through an enum control. Use two branches with a `mix`/transition node to change between these processing paths.

## Implementation and required dependencies

- **Required:** Python >=3.11, Pillow >=10, DanceRudiments (already required by the package).
- **Optional `effect_graph` extra:** NumPy >=1.26, required when evaluating this node; no NumPy import on the no-graph path.
- **GPU:** existing `effect_graph_backend` setting remains unchanged. The new transition **uses the deterministic CPU reference even with an available GPU backend**; a native ModernGL matte-transition shader is deferred to M8.3. No claim of GPU acceleration is made.
- **Memory:** the implementation uses NumPy image-sized arrays; large 4K/8K textures require explicit profiling before live-performance guarantees. The graph's normal resolution and resource bounds still apply.
- **Security:** invalid types, NaN/Inf, unknown key colours, invalid ranges and incompatible input counts fail closed rather than producing partially rendered videos.

## Testable deliverables and gates

1. **Exact endpoints:** returns pixel-identical A at progress 0 and B at progress 1, regardless of mask.
2. **Alpha safety:** RGB under a fully transparent matte cannot reveal a layer; alpha-sensitive transitions do not mutate provider images.
3. **Feature coverage:** alpha, luma-alpha, RGB and OKLab chroma, inversion, gamma, feather and threshold all have pixel assertions.
4. **Native controls:** per-frame overrides are ephemeral; configurable `key_color` channels, progress, threshold and categorical modes pass validation.
5. **Graph integration:** three-input transition accepts forked graph output as matte; source-level dependency cycles and invalid names fail early.
6. **Random-access:** evaluating frame N, any other frame and N again yields byte-identical frame N.
7. **Full project test:** the generated, hash-pinned synthetic demo drives progress with a native `named` DanceRudiment; the original video pathway has no new required dependencies.
8. **Visual evidence:** `integration/build_effect_graph_m5_visuals.py` creates 12 labelled alpha/luma/chroma/inverted examples and JSON SHA-256 checksums; repeated executions must match.

```bash
python -m pytest -q tests/test_effect_graph_transition_mattes.py tests/test_effect_graph_transitions.py
python -m pytest -q tests/test_effect_graph_transition_matte_project.py
python integration/build_effect_graph_m5_visuals.py --output m5-matte-transitions.png
python -m ruff check .
python -m pytest -q tests
```

### CPU-matrix skip gate repair

On merged M4.2 the full Python matrix reported **1,317 passed and 20 skipped**, then failed the `No skipped rendering checks` gate. The 20 hardware-dependent spectral test cases are housed in `tests/test_effect_graph_gpu_spectral.py`, and the existing reusable `effect-graph-parity` job runs those tests on actual Mesa/EGL. M5.1 updates only the CPU matrix collection command to exclude this GPU-only module, retaining the zero-skips check for ordinary CPU tests and the mandatory GPU job. This is not a waiver of GPU acceptance.

**Exit gate:** complete repository test matrix and native control integration must pass CI, in addition to the focused unit tests. Since this CPU-only increment makes no changes to existing M4 GPU shaders, M4.2 GPU parity must remain green.

## Next phases

- **M5.2:** multi-colour/spectral wipe; use per-band masks from `spectral_displacement` as third-input matte sources.
- **M5.3:** zoom, rotate, polar, liquid and geometry-aware motion between sources.
- **M5.4:** stacked transitions and multi-stage pipelines.
- **M5.5:** beat-locked progress envelopes, reversal and source-time phase policies.
- **M8.3:** GPU-native matte transitions with parity and 4K/8K measurements.
