# Phase 3: declarative compositor animation and intermediate stage routing

FrameRudiments preserves StemLab recordings and native DanceRudiments motion
without extracting or transforming the audio again. This feature is purely
presentation-layer and deterministic at any requested output frame.

`layer_animations` targets known `layer:<id>` compositor surfaces. Properties
`opacity`, `x`, `y`, and `rotation` accept finite scalar values or normalized
keyframe envelopes (`keys` with `time`, `value`, optional `ease`). Keyframe
values 0..1 are mapped to the property's validated range. Rotation and
translation happen after polygon clipping, before masking/displacement and
visualiser input routing. `clip` accepts a fixed polygon with 3..128 vertices
or an animated `{"keys":[{"time":0,"points":[[x,y], ...]}, ...]}` polygon.
All clip keys must have the same vertex count and increasing times; vertex
positions interpolate deterministically with optional easing.

`layer_displacement` applies bounded horizontal displacement of the target
surface using the alpha-weighted luminance of an **earlier** source layer.
Displacement does not modify either StemLab spectral measurements or
DanceRudiments signals, and transparent pixels are preserved on the edges.

```json
{
  "layer_animations": {"layer:dancer": {
    "opacity": {"keys": [{"time":0,"value":0}, {"time":2,"value":1}]},
    "x": 20,
    "clip": [[0,0],[640,0],[640,480],[0,480]]
  }},
  "layer_displacement": {"layer:dancer": {
    "source":"layer:noise", "amount":12
  }},
  "visualiser_stage_injections": [
    {"before":1, "elements":["layer:dancer"], "opacity":0.5}
  ]
}
```

`visualiser_stage_injections` supplies a clean copy of the chosen compositor
surfaces **before the selected indexed GPU effect stage** (index 1 is the
second stage). Original layer order determines composite order, and separate
initial GPU routing and final overlays still work. The setting requires a
visualiser effect chain and the chosen surfaces to be available via isolated
compositor routing. GPU processing remains sequential.

New features are opt-in and create no independent signal feature formats.
