# StemLab stem sources and layer mattes

`stem_sources` provides **named aliases** for existing native, SHA-256 pinned
StemLab-compatible DanceRudiments recordings. It does not decode stems, derive
FFT features, or invent new analysis formats. Motion sampling, spectrum
calibration, source-time provenance, interpolation rules and bounds checking
continue through the existing `Motion`/`RudimentLayer` implementation.

```json
{
  "stem_sources": {
    "bass": {"kind": "recording", "file": {"path": "bass.json", "sha256": "<actual sha256>"}},
    "drums": {"kind": "recording", "file": {"path": "drums.json", "sha256": "<actual sha256>"}}
  },
  "layers": [
    {"id": "bass-spectrum", "type": "rudiment", "renderer": "spectrum-ring",
     "stem_source": "bass", "bounds": [0, 0, 512, 512]},
    {"id": "drum-dots", "type": "rudiment", "renderer": "particles",
     "stem_source": "drums", "bounds": [0, 0, 512, 512]}
  ]
}
```

Spectrum renderers require calibrated StemLab Fourier-band recordings with
correct source identity. A generic named movement recording is not a spectrum.
For a layer, supply **either** `motion` or `stem_source`; conflicting values and
unknown aliases fail validation. Native/recording motion remains unchanged.

## Reusable alpha/luminance track mattes

`layer_masks` is an optional top-level mapping. A target must be a normal
`layer:<id>` entry; mask sources must be previously composed `layer:<id>`
surfaces or `background`. Only previously drawn sources are permitted to avoid
feedback cycles. Matte alpha is applied after the target layer's own scoped
post-effects, before the target enters GPU routing or the final composition.

```json
"layer_masks": {
  "layer:drum-dots": {"source": "layer:bass-spectrum", "mode": "luminance", "invert": false}
}
```

`mode` is `alpha` (default) or `luminance`. Luminance multiplies the source's
alpha by grayscale RGB intensity; `invert` negates that coverage. The matte is
not automatically hidden: set its own layer opacity/visualizer route as needed.
All operations are per-frame and deterministic, including with supersampling.
