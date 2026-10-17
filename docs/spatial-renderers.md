# Phase 4: deterministic 3D projection foundations

The built-in `spatial-torus`, `spatial-sphere`, and `spatial-terrain`
renderers draw 3D parametric wire geometry using a perspective camera,
depth-sorted edges, distance-fog approximation and optional glow. They consume
**normalized native Rudiment channels**, exactly like the existing motion
renderer family. They do **not** calculate audio features or infer pitch.

Example layer:

```json
{"id":"bass-orbit","type":"rudiment","renderer":"spatial-torus",
 "motion":{"kind":"recording","file":{"path":"bass-motion.json",
 "sha256":"<recording SHA-256>"}},
 "bounds":[0,0,1080,1080],
 "style":{"camera_distance":4.8,"fov":55,"orbit":35,
          "depth":0.7,"fog":0.22,"glow":0.35,"rings":13,"segments":30}}
```

For bass/drums/stems, use a pinned StemLab recording or the Phase 2 named
source aliases/cross-stem driver. DanceRudiments continues to supply movement
and modulation. Repeated source excerpts and variable-BPM beat clocks remain
handled by the existing motion/context system.

This is an initial **CPU perspective wire renderer**, not yet a depth-buffered
triangle renderer, physical lighting system or volumetric fog engine. It is
pure, seekable and deterministic. A future GPU mesh backend can build on the
same feature routing without breaking the JSON contract.

CI: `tests/test_spatial_renderers.py` verifies registry integration, source
motion responsiveness, camera/atmosphere variation and deterministic seeking.
The main visualisation atlas includes all three modes.
