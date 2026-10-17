# Cross-stem Rudiment visual drivers

`stem_blend` is a presentation-only weighted mix of two to eight separately
pinned native `stem_sources` recordings. The same source-clock arguments are
passed to each existing DanceRudiments `Motion`; the engine does not decode
PCM, compute loudness, extract instruments or perform new spectral analysis.

```json
{
  "stem_sources": {
    "bass": {"kind": "recording", "file": {"path": "bass-motion.json", "sha256": "<sha>"}},
    "drums": {"kind": "recording", "file": {"path": "drum-motion.json", "sha256": "<sha>"}}
  },
  "layers": [{
    "id": "combined-motion", "type": "rudiment", "renderer": "waveform-ribbon",
    "stem_blend": [{"stem_source": "bass", "weight": 3},
                   {"stem_source": "drums", "weight": 1}],
    "bounds": [0, 0, 1080, 500]
  }]
}
```

Sources must share identically named dimensions and the existing source-time
contract. Weighted channel values are clamped to [-1,+1] after normalisation.
`stem_blend` cannot be combined with `motion` or `stem_source`. Frequency-band
spectrum/spectrogram displays reject blended motion: spectrum aggregation needs
calibrated measurements, not arithmetic averaging of normalised motion values.
For spectrum views, use a single verified StemLab Fourier recording per layer.
