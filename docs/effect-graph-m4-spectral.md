# M4 — Arbitrary spectral colour-band displacement

**Status:** M4.1 CPU reference and graph/DanceRudiment integration implemented; M4.2 GPU and performance gates remain open.  **Baseline:** FrameRudiments commit `96e7ab3` (28 October 2026).

## Rationale and execution contract

The original `chromatic_displacement` node shifts fixed R/G/B components. New `spectral_displacement` nodes instead extract **up to eight arbitrary selected colours**, optionally recolour them, displace each selection with an independent subpixel XY vector, and composite their output in an authored order. A single rendered layer can be forked into multiple spectral effects or transitions without rendering it again.

The M4.1 CPU reference uses tiled NumPy processing (imported lazily only when this node runs) and always consumes and returns straight RGBA. Selection operates on unassociated source RGB; weights and fractional bilinear interpolation are applied in premultiplied space to avoid hidden RGB leaking from transparent pixels. A source pixel's match is alpha-weighted. Unsupported colours outside the frame sample transparent black. This implementation remains **display-referred SDR**; HDR/scenelinear conversions are explicitly deferred.

The new node is opt-in and does not alter the original `chromatic_displacement` node or non-graph projects. A source alpha lineage is inherited through spectral nodes; the standard graph's per-node opacity is applied afterward. The `cpu` graph backend is the reference implementation. In `auto` mode, the spectral node runs on CPU and records a fallback; in `gpu` strict mode, it rejects the unsupported shader rather than silently changing backend.

## Project schema

```json
{
  "effect_graph": {
    "nodes": [
      {"id": "layer:character", "type": "source"},
      {
        "id": "prism", "type": "spectral_displacement",
        "inputs": ["layer:character"],
        "spectral_mode": "replace",
        "alpha_mode": "expanded",
        "mix": 1.0,
        "bands": [
          {
            "id": "magenta",
            "source_color": "#ff40e0",
            "target_color": "#22ddff",
            "space": "oklab",
            "tolerance": 0.08,
            "softness": 0.08,
            "offset": [-12.5, 3],
            "opacity": 0.85,
            "recolor": 0.8
          },
          {
            "id": "highlights",
            "source_color": "#fefefe",
            "space": "rgb",
            "tolerance": 0.05,
            "softness": 0.1,
            "offset": [8, 0],
            "opacity": 0.6
          }
        ]
      }
    ],
    "output": "prism"
  },
  "effect_graph_controls": [
    {
      "id": "bass-prism",
      "node": "prism",
      "property": "bands.magenta.offset.x",
      "motion": {"kind": "named", "pattern": "bounce"},
      "axis": "y",
      "range": [-20, 20]
    },
    {
      "id": "hue-selector",
      "node": "prism",
      "property": "bands.magenta.source_color.r",
      "motion": {"kind": "named", "pattern": "circle"},
      "axis": "x",
      "range": [50, 255]
    }
  ]
}
```

## Parameter registry

| Path | Type / range | Default | Notes |
|---|---|---|---|
| `mix` | float 0..1 | 1 | Premultiplied interpolation of original and processed RGBA |
| `spectral_mode` | `overlay` / `replace` | `overlay` | Duplicate or move selected ink |
| `alpha_mode` | `expanded` / `source` | `expanded` | Allow out-of-bounds alpha or cap to source silhouette |
| `bands.<id>.source_color.{r,g,b}` | integer-like 0..255 | from `source_color` | Arbitrary selected colour |
| `bands.<id>.target_color.{r,g,b}` | integer-like 0..255 | source colour | Recolour destination |
| `bands.<id>.space` | `rgb` / `hsv` / `oklab` | `oklab` | HSV uses wrapped hue and saturation-aware weighting |
| `bands.<id>.tolerance` | float 0..1 | 0.08 | Fully selected inside threshold |
| `bands.<id>.softness` | float 0..1 | 0.08 | Smoothstep falloff outside threshold |
| `bands.<id>.offset.{x,y}` | float -4096..4096 | 0 | Pixels at native layer resolution |
| `bands.<id>.opacity` | float 0..1 | 1 | Per-band alpha; deterministic authored blend order |
| `bands.<id>.recolor` | float 0..1 | 0 | Mix source-selected colour toward target colour |
| `opacity` | float 0..1 | 1 | Universal graph node opacity |

The band ID, not its list position, forms the address; reordering bands preserves DanceRudiment bindings. Numeric paths use the existing source-clocked modulator and categorical paths use its existing validated `choices` mechanism. There is no new music-analysis engine.

## M4 delivery and acceptance gates

- **M4.1 (this increment):** strict configuration and control validation; indexed band tables resolved by stable IDs; RGB, hue-wrapped HSV and OKLab colour-distance matching; independent signed floating-point displacement; premultiplied bilinear selection; recolouring, opacity, replace/overlay modes and source/expanded alpha; deterministic tiled CPU evaluation; unit and graph-control tests, including fork/merge and two-input transition chaining.
- **M4.2:** fragment/compute shader with the same selection functions and correct sampling; GPU-resident graph integration or explicit upload/readback accounting; reference image parity across a set of alpha/colour edge cases. Validate at least one Mesa/EGL GPU and an independently supported native driver.
- **M4.3:** deterministic headless contact-sheet generator (introduced in this increment), integration with a hash-pinned synthetic project, and measured HD/4K/8K latency/peak-memory results. A bounded CPU benchmark tool is included; full hardware results remain outstanding. Set acceptable budgets only after collecting representative hardware results; do not invent performance numbers.
- **M4.4:** integration in the graph authoring editor, user-facing presets, transition combinations, provenance for spectral settings, CPU/GPU matrix and docs.

**Compatibility gate:** existing `chromatic_displacement`, matte, key, transition and legacy rendering tests must continue passing. `spectral_displacement` cannot become a default node or silently alter the behaviour of an existing project. Shader-less GPU strict mode must fail explicitly rather than accepting an unannounced CPU fallback. Optional NumPy remains isolated to invoking the spectral effect.

## CI issue addressed alongside M4.1

The latest general CI run at `96e7ab3` failed with eight Ruff `E701/E702` style errors in `effect_graph_gpu.py`, before pytest started. This patch fixes those formatting errors without changing the GLSL algorithms or the validated M3.3 GPU path. The separate Mesa/EGL GPU parity workflow had succeeded. The next general CI run must pass `ruff check .` **and** the complete tests.

### Evidence and benchmark commands

```bash
python integration/build_effect_graph_m4_visuals.py --output qa/m4
python integration/benchmark_effect_graph_spectral.py --size smoke --iterations 3
python integration/benchmark_effect_graph_spectral.py --size 4k --iterations 3 --allow-large
python -m pytest tests/test_effect_graph_spectral.py tests/test_effect_graph_spectral_graph.py tests/test_effect_graph_spectral_visuals.py -q
```

Benchmark outputs are per-run measurements, not fixed performance guarantees.
No CPU timing should be represented as a GPU result.
