# Music-reactive 3D and procedural terrain

FrameRudiments provides three **new opt-in** display modes:
`spatial-music-torus`, `spatial-music-sphere` and `spatial-music-terrain`.
They consume the existing `RudimentFrame` created by a native DanceRudiments
movement, a checksum-pinned StemLab motion/FFT recording, a named stem source,
or a multi-stem motion blend. FrameRudiments does **not** recompute an FFT,
perform stem separation, estimate pitch or change the original audio clock.

## Choose the rasteriser

The `backend` style field accepts `cpu` (default), `gpu`, or `auto`.
`gpu` requires OpenGL 3.3 through an offscreen ModernGL context and fails
clearly if unavailable; `auto` falls back to CPU **with an explicit fallback
reason in the frame report**. The deterministic CPU renderer performs mesh
projection, heightfield erosion, local direct lighting and approximate fog.
The native OpenGL path performs GPU triangle rasterisation, depth tests, a
light-space shadow map with PCF filtering, a procedural normal/bump material,
and a shadow-aware full-screen participating-media pass.

Install the optional runtime:

```sh
python -m pip install 'framerudiments[gpu]'
```

A software OpenGL renderer (Mesa llvmpipe) still executes the GL pipeline
but is **not hardware GPU acceleration**. Check the OpenGL driver and renderer
in the runtime diagnostic output when measuring performance. CPU and GPU
outputs are not expected to be pixel-identical across machines.

The volumetric pass integrates **Beer–Lambert extinction and an approximate
Henyey–Greenstein single-scattering phase function**, shadowed by the light's
depth map. It is physically motivated, but **not exact light transport**:
multiple scattering, caustics, global illumination, full atmospheric radiative
transfer and spectral polarization are out of scope. Procedural rock, snow,
weathering, glaciation and hydraulic/thermal erosion are artistic approximations,
not geological forecasting.

## New terrain rows from calibrated FFT history (optional)

Enable `style.terrain_source: "fft"` on `spatial-music-terrain`.
The columns correspond to calibrated **StemLab Fourier frequency bands**, and
successive rows correspond to actual historical recorded frames, with the
**newest sampled spectrum at the near edge**. Band amplitudes use each band's
pinned `SpectrumBand.rms` calibration, dB normalization and logarithmic
frequency interpolation. This does not increase spectral resolution, perform
new DSP or label synthetic history as measured data.

For a valid source, select a StemLab exported `dancerudiments.recording`
with calibrated Fourier-band metadata. The existing `spectrum_bands()` validator
checks source SHA-256, FFT size, sample rate, ascending band intervals, zero
clipping and provenance. Use `history_frames` and `history_stride` to control
how quickly successive spectrum slices scroll through terrain. Rendered rows
are **deterministic on seeking**, including repeated excerpts and variable BPM.

```json
{
  "id": "fft-alpine-ridge",
  "type": "rudiment",
  "renderer": "spatial-music-terrain",
  "bounds": [0, 240, 1080, 1320],
  "stem_source": "spectrum",
  "history_frames": 100,
  "history_stride": 2,
  "style": {
    "backend": "gpu",
    "terrain_source": "fft",
    "rings": 36,
    "segments": 48,
    "fft_floor_db": -78,
    "fft_gain": 1.2,
    "terrain_height": 1.6,
    "hydraulic_erosion": 0.35,
    "thermal_erosion": 0.45,
    "glaciation": 0.6,
    "weathering": 0.55,
    "erosion_iterations": 5,
    "snowline": 0.35,
    "ice_coverage": 0.65,
    "shadow_strength": 0.8,
    "volumetric_steps": 16,
    "scattering_density": 0.28,
    "absorption_density": 0.06
  },
  "modulations": [
    {
      "id": "kick-ripple",
      "motion": {"kind": "named", "pattern": "bounce"},
      "axis": "y", "target": "style.audio_displacement",
      "range": [0.05, 0.72], "curve": "smoothstep"
    },
    {
      "id": "ambient-travel",
      "motion": {"kind": "named", "pattern": "circle"},
      "axis": "x", "target": "style.camera_pitch",
      "range": [-20, 20]
    },
    {
      "id": "ice-shimmer",
      "motion": {"kind": "named", "pattern": "circle"},
      "axis": "z", "target": "style.ice_coverage",
      "range": [0.15, 0.95]
    }
  ]
}
```

The corresponding top-level `stem_sources` example is:

```json
{
  "stem_sources": {
    "spectrum": {
      "kind": "recording",
      "file": {"path": "stemlab-fourier-recording.json", "sha256": "<actual SHA256>"},
      "outside": "error"
    }
  }
}
```

Use the actual checksum; placeholders are illustrative and cannot pass
project validation. Recorded Fourier streams are **not eligible for invented
multi-stem spectral aggregation**: `stem_blend` remains a presentation-domain
blend of motion values, not a calibrated spectral source. To visualise separate
stems, create separate layers with their individual pinned Fourier recordings.

## Other mesh types and direct music reaction

For a torus/sphere, use `spectral_deform: true` to distribute an actual
calibrated Fourier spectrum spatially over mesh vertices. Otherwise any native
DanceRudiments motion drives the mesh directly (position-independent geometry
is not merely rotated): X affects sinusoidal height/radius displacement, Y
controls twist and counter-ripples, and Z affects cross-phase distortion.

Numeric `style.*` properties such as `audio_displacement`, `audio_frequency`,
`mesh_gain`, `terrain_height`, `roughness`, `metallic`, `normal_strength`,
`bump_strength`, `light_power`, `light_azimuth`, `light_elevation`,
`camera_pitch`, `glaciation`, `weathering`, `ice_coverage`, `fog` and
`scattering_density` accept existing native DanceRudiments modulation.
Modulators are evaluated at the real source clock, and their ranges/blending
obey the existing `RudimentModulations` rules. No independent oscillators,
new pips-per-beat assumptions or duplicate movement engines are introduced.

### Performance and quality

* Tune `rings`, `segments`, `erosion_iterations`, `shadow_map_size`,
  `volumetric_steps`, and `render_quality` for your target hardware.
* The existing 4× spatial/8× temporal defaults can multiply render cost.
* CPU results are deterministic and random-access. GPU rendering is evaluated
  for random-access consistency on the **same driver**; bit-exact output
  across GPU drivers is not guaranteed.
* The source SHA, terrain row frame IDs and the selected rasteriser are
  recorded in the per-frame analysis sidecar.

## Verification

`tests/test_music_spatial_renderers.py` tests spectral rows, invalid
provenance, deterministic erosion/weathering/glaciation, mesh motion and CPU
fallback. `integration/test_music_spatial_gpu.py` exercises native OpenGL
framebuffer, lighting, shadows and scattering in the visualiser-acceptance CI.
The gallery automatically includes the new renderer families using synthetic
procedural samples, not private audio.
