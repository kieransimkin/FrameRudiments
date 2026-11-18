# M12 — scene-linear colour, OpenEXR interchange and native spatial AOVs

**Status: implementation candidate.** This is an opt-in, offline adapter. It does **not** silently change
FrameRudiments' established RGB8 compositor or existing encoded H.264/AAC renderer. A native character,
particle, or scene renderer must explicitly produce real depth, normals or velocity data; the adapter **will
never infer** AOVs from RGB pixels.

## Behaviour

- `AOVFrame` owns immutable detached float32 image buffers (RGB, optional A/Z/N/motion), validates dimensions,
  numeric finiteness, alpha range, nonnegative camera depth and an aggregate 512 MiB payload budget.
  Unassociated colour is **scene-linear**, not premultiplied. Values outside 0..1, including negative RGB,
  remain untouched until a separately requested display preview.
- `srgb8_to_scene_linear` implements the standard sRGB decoding curve from 8-bit *display* pixels; this does
  not recreate HDR information or magically make the original renderer scene-linear.
  `scene_linear_to_srgb8` is an explicit display preview with optional Reinhard compression and exposure.
- `transform_ocio` uses the native Python OpenColorIO bindings against a **trusted checksum-pinned local**
  `.ocio` configuration and explicitly named source and destination spaces. To use ACEScg, your configuration
  must actually declare `ACEScg`; the adapter does not ship or invent an ACES config. Auxiliary float
  channels are carried over without colour processing. Any referenced LUTs are the caller's responsibility:
  pin them with `pinned_resources` and treat the entire OCIO configuration as trusted local content; this
  API is **not** a sandbox for arbitrary third-party configs.
- `write_exr` uses OpenEXR `File` with a single flat scanline part and **lossless ZIP float32** channels.
  The EXR carries `R/G/B`, optionally `A`, `Z`, `N.X/Y/Z`, `motion.X/Y`, explicit original colour space and
  association metadata. The adapter independently opens the temporary EXR, checks every float32 value,
  then publishes a new `.exr` plus `.receipt.json` without overwriting either path. Failure leaves neither
  output behind. `read_exr` validates the exact SHA-256, channels, shape and local header declarations.
- FrameRudiments includes a **separate opt-in executable**, `framerudiments-colour`; production
  `framerudiments render` and `framerudiments demo` are unaffected.

This is intentionally narrow. It supports neither multipart, deep, tiled or HDR framebuffer integration,
nor untrusted EXR parsing. The OpenEXR Python API eagerly reads the whole image; a 1 GiB file cap does not
bound the decoded size *before* the native codec allocates it. Inspect files only from trusted sources.

## Installation and CLI

```bash
python -m pip install -e '.[dev,colour_aov]'
framerudiments-colour export-exr ./colour-aovs.json --output ./output.exr
framerudiments-colour inspect-exr ./output.exr --sha256 <actual_sha256>
framerudiments-colour transform-exr ./output.exr --sha256 <actual_sha256> \
    --ocio-config ./trusted/config.ocio --config-sha256 <actual_sha256> \
    --from-space ACEScg --to-space 'Output - sRGB' --output ./converted.exr
```

The manifest is a UTF-8 JSON object. Every channel references a **separate .npy float32 array** with an
exact SHA-256; no pickle, implicit conversion or side-effectful Python object loading. All paths must be
local to the manifest directory. `RGB` is mandatory; optional arrays are named `A`, `Z`, `N`, `motion`:

```json
{
  "schema": "framerudiments.colour-aov-input.v1",
  "colour_space": "ACEScg",
  "channels": {
    "RGB": {"path": "beauty.npy", "sha256": "<sha256 of beauty.npy>"},
    "Z": {"path": "depth.npy", "sha256": "<sha256 of depth.npy>"},
    "N": {"path": "normals.npy", "sha256": "<sha256 of normals.npy>"},
    "motion": {"path": "velocity.npy", "sha256": "<sha256 of velocity.npy>"}
  }
}
```

Shapes are RGB `(H,W,3)`, A/Z `(H,W)`, N `(H,W,3)`, motion `(H,W,2)`, all float32.
`Z` is authored camera depth in **producer-defined camera units**, nonnegative and finite. `N` is authored
three-component normal direction in the producer's declared frame of reference. `motion` is authored 2D
velocity in **pixels per encoded frame**. **No inferred geometry, units or coordinate transform** is applied.
Data consumers should agree on camera space and motion-vector convention explicitly before interchange.


## M12.2 — Native CPU solid mesh HDR and geometry AOVs

`framerudiments.solid_spatial_aov` adds explicit float32 captures from the **actual**
`SolidSpatialRenderer` triangle rasteriser, supporting its existing torus, sphere and
animated terrain families. There is no image-based depth, normal, or motion estimator:
all pixel coverage and occlusion use the native `_raster_triangle` reciprocal-Z test,
with the same source-clock mesh vertices, camera projection and material settings.
The only entry point is opt-in; normal `render()` and project MP4 output retain their
existing bytes, postprocessing, clock and API.

```python
from framerudiments.solid_spatial_aov import render_solid_aov, export_solid_exr

# Obtain renderer and current/previous RudimentFrame objects from the existing
# native rendering pipeline. Supplying previous_frame is optional.
frame = render_solid_aov(renderer, current_frame, previous_frame=previous_frame,
                         light_stops=0.0)
assert frame.colour_space == "Linear-sRGB"
assert {"RGB", "A", "Z", "N", "motion"} <= set(frame.channels())
receipt = export_solid_exr(renderer, current_frame, "./solid-aovs.exr",
                           previous_frame=previous_frame)
```

- **Beauty RGB** comes from the same real Cook-Torrance/GGX native material equations,
  **before** the legacy sRGB8 clamp. An explicitly requested `light_stops` between -8
  and +8 changes direct illuminance and preserves float highlights above 1.0.
  The background is explicitly decoded from its authored sRGB8 colour; it is not
  silently upgraded to a captured HDR environment. The colour-space name is
  `Linear-sRGB`, **not** ACEScg unless a separate trusted OCIO transform is applied.
- **Depth Z** is positive camera-forward distance, calculated at the winning
  surface using the native perspective-correct z-buffer. Background has depth 0;
  the channel is **not** a world-space height map or a luminance estimate.
- **Normals N.X/Y/Z** are camera-oriented unit face normals at the winning
  triangle, using the native camera rotation. Background normals are zero.
- **Motion motion.X/Y** is the screen-pixel displacement from the *explicitly
  supplied* adjacent previous native frame to the current one, reconstructed
  from the same mesh vertex identities with homogeneous perspective reprojection.
  No previous frame => no motion AOV. Incorrect adjacency, reversed clock and
  faces unprojectable at the earlier frame fail closed. Static meshes produce
  approximately zero motion, rather than bogus vectors from screen blending.
- **Alpha A** records opaque winning geometry over the actual configured
  background alpha. The captures are **pre-fog, pre-glow, pre-output-AA**;
  they do not reconstruct depth or coverage for postprocessing and external
  visualiser effects. Rendering optical flow, separate volume depth and object-ID
  AOVs remains future work.

Captures are detached, immutable float32 `AOVFrame`s using M12.1 EXR encoding
and readback. Preflight checks the combined logical payload against **512 MiB**
before allocating, and the actual peak can be higher because AOVFrame detaches
copies and the z-buffer is separately retained. M12.3 provides a separately
opted-in GPU coverage/AOV path for the same mesh projector; the existing GPU
terrain/fog shader, multi-layer composites and physical HDR acceptance are not
implicitly supported.

Run local deterministic kernels with
`python -m pytest -q tests/test_solid_spatial_aov_m12.py`, and the existing
`.github/workflows/colour-aov-m12.yml` matrix runs that suite alongside the
M12.1 native OpenEXR and OCIO tests on Windows/Linux.

## M12.3 — GPU float32 AOVs and pinned ACEScg reference

`render_solid_aov_gpu(renderer, frame, previous_frame=..., backend="egl")`
uses the **actual** native CPU mesh projector and linear-light flat-facet GGX
reference, then rasterises triangle coverage, camera Z, normals and screen-space
motion vectors into **OpenGL 3.3 RGBA32F multiple render targets** with hardware
Z-testing. It does not reuse the unrelated GPU landscape shader's material
system; claiming independent GPU lighting parity would be misleading.

The GPU path is explicitly optional and bounded. It records GL vendor, renderer,
version, shader SHA and raster evidence, and produces detached M12.1 `AOVFrame`
objects. It never silently falls back to the CPU if GL is missing. A companion
`parity_report` checks float32 colour, depth, normals, screen motion and raw
silhouette overlap against the M12.2 CPU capture. It excludes facet boundary
ownership differences from pixel-value statistics while retaining raw IoU as a
separate pass/fail condition. No HDR data is inferred from an RGB8 preview.

`to_acescg_reference` transforms `Linear-sRGB` (linear Rec.709/D65) through an
**exact SHA-pinned trusted OpenColorIO config** into ACEScg (AP1/D60), verifies
reverse-transform tolerance and proves unchanged alpha/depth/normals/velocity.
The CI test serialises the specific published ASWF ACES **1.3 CG config**
`cg-config-v2.2.0_aces-v1.3_ocio-v2.4`, then SHA-pins that actual config;
it never uses the moving `ocio://latest` alias. This is a scene-linear colour
space transform, **not** the ACES Reference Rendering Transform / Output Device
Transform or a display-approved HDR image.

The independent Linux software-EGL gate in
`.github/workflows/colour-aov-m12.yml` requires real ModernGL, OpenEXR and
PyOpenColorIO. `scripts/qualify_gpu_aces_m12.py` saves float32 CPU/GPU parity
reports, comparison PNGs, actual EXR and pinned OCIO receipts in an Actions
artifact. The Windows/Linux M12.1/M12.2 codec matrix stays separate. Hardware
GPU parity, display calibration, OpenEXR over tiled/deep inputs, native volume
AOVs, animated character geometry and production scene-wide HDR composition
remain future integration work.

To reproduce the reference fixture in a configured EGL environment:

```bash
python -m pip install -e '.[dev,colour_aov,gpu]'
FRAMERUDIMENTS_REQUIRE_GPU_AOV=1 FRAMERUDIMENTS_REQUIRE_ACES=1 \
  python -m pytest -q tests/test_gpu_spatial_aov_m12.py tests/test_aces_reference_m12.py
python scripts/qualify_gpu_aces_m12.py --output m12-3-evidence
```

## Qualification / follow-on work

`tests/test_colour_aov_m12.py` checks validation, file pins, no overwrite, rollback, the OCIO processing
boundary and an injected EXR interface. The separate workflow
[Native M12 colour + AOV qualification](../.github/workflows/colour-aov-m12.yml) installs real `OpenEXR`
and `opencolorio` on Windows/Linux, Python 3.11/3.13, then runs actual float32 EXR readback through
`tests/test_colour_aov_m12_real.py`. Full device/HDR/ACES colour reference approval remains open.

M12.3 includes a real OpenGL float32 reference kernel and a trusted pinned
ACEScg colour-space conversion. Next: physical-GPU and HDR-display validation,
independent GPU materials, full scene-layer geometry, HDR encoded output and
native volume AOVs. M13 remains the separately scheduled timeline I/O milestone.
None of these stages equates synthetic AOVs with measured motion capture.
