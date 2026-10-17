# Keyframed compositor mattes

`layer_masks` supports the existing prior-layer alpha/luminance masking with an optional
output-time `opacity` property. A number from 0 to 1 is constant. An envelope
provides nonnegative seconds and a normalized value at each keyframe. Segment
easing can be `linear`, `smoothstep` or `hold` (assigned to the starting key).

```json
{"layer_masks": {"layer:foreground": {"source": "layer:shape", "mode": "alpha",
  "opacity": {"keys": [{"time": 0, "value": 0, "ease": "smoothstep"},
                        {"time": 2, "value": 1}, {"time": 5, "value": 0}]}}}}
```

This is a deterministic image presentation envelope, **not** audio analysis;
StemLab recording data and native DanceRudiments movements keep their clocks.
Keyframe times are output seconds (not source excerpt time), so transitions
continue smoothly across repeated audio excerpts. Outside the keys' time range,
values clamp to the nearest endpoint. A missing matte surface remains fully
transparent before inversion; the final mask combines alpha, optional luminance,
inversion, and opacity. Frame seeking has no hidden state.
