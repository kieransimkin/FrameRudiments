# M15.4 — OpenUSD scene import and native geometry AOV interchange

**Status: implementation candidate.** This adapter is deliberately opt-in and
restricted; the normal FrameRudiments compositor and GStreamer/FFmpeg outputs
are not modified. `usd-core` is a native optional package, **not** a sandbox or
an arbitrary plugin-hosting permission. Only use trusted scene files.

## Scope and admission

- A single **UTF-8 ASCII `.usda`** file with a mandatory matching SHA-256.
  Maximum 4 MiB. Its top-level text must begin `#usda 1.0`. Absolute/reference
  asset delimiters (`@`), external paths, sublayers, payloads, variants,
  inherits, specializes and clips are rejected. Binary `.usd` / `.usdc`, USDZ,
  nested reference composition, procedural schemas and untrusted third-party
  shaders are **not** supported. Root/source bytes are rechecked before and
  after the time-limited native worker. Only platform-supported `usd-core`
  and trusted author-owned inputs should be used.
- Mesh types support **triangles only**, no subdivision or clipping across the
  near/far planes, at most 8192 points / 4096 triangles / 128 stage prims.
  Unsupported prim types fail, never silently disappear. Supported hierarchy:
  `Xform`, `Scope`, `Mesh`, `Camera`, `Material`, `Shader`.
- Geometry uses `UsdGeom.XformCache` at the declared USD timecode; animated
  points and transforms are evaluated from the native stage. Camera support is
  explicitly a perspective `UsdGeom.Camera` with authored Xform, focal length,
  horizontal/vertical aperture and clipping range. The stage's Y/Z up axis and
  metres-per-unit are preserved in receipts; no invented axis conversions.
- Each mesh uses either a single authored `displayColor`/`displayOpacity` or a
  bound **constant** `UsdPreviewSurface` diffuseColor/opacity. Other shader IDs,
  texture input connections, image assets and material networks fail explicitly.
  Colour is interpreted as scene-linear authoring data (not an sRGB texture).

## Timing and AOVs

The requested `--frame` is an *integer output frame* at an explicitly authored
rational `--fps`. `sample_timecode = frame * USD_timeCodesPerSecond / FPS`; the
native stage samples at the corresponding floating-point USD timecode, and
reports the exact rational source calculation. USD itself uses double-valued
sample times; no claim of sub-double perfect time sampling is made.

Native CPU triangles are depth-tested at pixel centres using reciprocal-camera
Z, preserving winning material colour and straight alpha. The M12 `AOVFrame`
contains float32 linear RGB, `A`, camera-space positive `Z` in **metres**, and
camera-oriented unit normals `N.X/Y/Z` (zero on empty pixels). It does **not**
invent optical flow, GPU shading, true shadowing, volume depth or Hydra output.
OpenEXR writes undergo independent lossless float32 readback. A separately
created SDR preview applies an explicitly requested display tone map.

## Installation and commands

```powershell
python -m pip install -e '.[dev,openusd,colour_aov]'
framerudiments-usd capabilities

# Replace with the hash of your own local trusted USD scene:
framerudiments-usd inspect .\trusted.usda --sha256 <64-hex-digest> `
  --camera /World/Camera --frame 24 --fps 24000/1001 --width 320 --height 180

framerudiments-usd render-exr .\trusted.usda --sha256 <64-hex-digest> `
  --camera /World/Camera --frame 24 --fps 24000/1001 --width 320 --height 180 `
  --output .\usd-frame.exr
```

The CLI produces a new EXR, `.receipt.json` and `.png` display preview, refusing
to overwrite any of them. Failure rolls back generated outputs; no renderer
state or media assets are changed. No USD models or textures are downloaded.

## Acceptance and limitations

`tests/test_usd_m15.py` runs without native packages and covers pins, rejected
external arcs, rational source selection, exact z-buffer order and rollback.
The separate [native M15 USD workflow](../.github/workflows/usd-m15.yml)
installs `usd-core` / OpenEXR on Windows/Linux for Python 3.11/3.13. It
**authors a real time-sampled USD scene via pxr**, imports its geometry,
samples three frames, confirms a constant material binding and verifies actual
OpenEXR depth/normal/beauty outputs against independent decoding. The Pages
site publishes the actual frames at `/m15-4/` only after successful CI.

**Hydra is not implemented or qualified by this increment.** Real Hydra
render delegates, physical GPUs, shader/material feature parity, external USD
assets, memory telemetry, licensing of specific scenes/plugins and perceptual
approval each require their own gates. A functioning `pxr` reader is not proof
that Hydra rendering works.
