# Phase 4 solid 3D scenes and atmospheric rendering

FrameRudiments provides three new **CPU rasterized, depth-tested 3D** viewport
renderers: `spatial-solid-torus`, `spatial-solid-sphere`, and
`spatial-solid-terrain`. The original `spatial-torus`, `spatial-sphere`, and
`spatial-terrain` wireframe renderers are unchanged.

Each solid mode constructs a triangle mesh, perspective-projects its vertices,
computes energy-conserving roughness/metallic Cook-Torrance/GGX flat-shaded direct lighting, and rasterizes
triangles into an actual per-pixel depth buffer using perspective-correct
reciprocal depth. A bounded, low-resolution volume ray-march approximates
front-to-back single scattering. Rays terminate at visible mesh geometry;
therefore foreground meshes block fog behind them. Fog is composited back into
the RGBA viewport, followed by an optional soft glow.

All rendering remains deterministic for random-access frames and uses the
**existing native Rudiment `motion` and `modulations`** infrastructure.
FrameRudiments does not duplicate StemLab's analysis, DanceRudiments' movement
library, or the master audio clock. StemLab stem analysis must be exported to
the existing checksum-pinned recording format and referenced through a native
recording, named stem source, or cross-stem motion driver.

## Example

```json
{
  "type": "rudiment",
  "id": "bass-surface",
  "renderer": "spatial-solid-torus",
  "bounds": [80, 100, 1000, 1020],
  "motion": {
    "kind": "recording",
    "file": {"path": "bass-rudiment.json", "sha256": "<actual digest>"}
  },
  "style": {
    "camera_distance": 4.2,
    "fov": 52,
    "orbit": 30,
    "rings": 14,
    "segments": 24,
    "metallic": 0.45,
    "roughness": 0.35,
    "ambient": 0.26,
    "light_direction": [-0.45, -0.35, 0.82],
    "light_colour": "#ffffff",
    "fog": 0.18,
    "fog_colour": "#597ca3",
    "fog_steps": 6,
    "fog_resolution": 240,
    "glow": 0.18,
    "colours": ["#5bc9ef", "#f6a6c9", "#bea9fc"]
  }
}
```

Supported geometry controls include `rotation` and `orbit` in degrees,
`camera_distance`, `fov`, `depth` (music-driven displacement magnitude),
`rings`, `segments`, and `padding`. Surface lighting controls are `ambient`,
`metallic`, `roughness`, `light_direction`, `light_colour`. Atmospheric controls
are `fog` (0 disables scattering), `fog_colour`, `fog_steps` (2–24),
`fog_resolution` (64–720 maximum fog grid dimension), and `glow`.

## Rendering boundaries

- **Depth**: per-pixel Z-buffered triangle visibility, not painter sorting.
- **Lighting**: GGX direct-light BRDF in linear RGB; no image-based lighting,
  cast shadows, indirect/global illumination or full path tracing.
- **Fog**: low-resolution procedural single-scattering approximation, not a
  measured participating-media simulation or physically accurate shadows.
- **GPU feedback**: produced RGBA surfaces enter existing compositor routing,
  Butterchurn and projectM chains normally. Mesh computation is still CPU-based;
  no native Vulkan/OpenGL 3D mesh backend is claimed.
- **Performance**: 4× source-vector supersampling and 8× temporal samples may
  multiply CPU mesh work substantially. Reduce `rings`, `segments`,
  `fog_resolution`, or `render_quality` explicitly if necessary.
- **Sound semantics**: normalized Rudiment channels animate visuals. No claims
  about true PCM, note, pitch, spectral phase, loudness, or stereo are made.

The synthetic catalogue gallery tests all three solid shapes with no private
sound recordings. `tests/test_solid_spatial_renderers.py` checks renderer
registration, depth ordering, fog geometry occlusion, materials, and random
frame seeking. The existing pipeline remains the source of truth for real audio,
lyric, frame-time and hash provenance.
