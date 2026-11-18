# M12.1 — scene-linear colour and pinned float32 AOV interchange

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

## Qualification / follow-on work

`tests/test_colour_aov_m12.py` checks validation, file pins, no overwrite, rollback, the OCIO processing
boundary and an injected EXR interface. The separate workflow
[Native M12 colour + AOV qualification](../.github/workflows/colour-aov-m12.yml) installs real `OpenEXR`
and `opencolorio` on Windows/Linux, Python 3.11/3.13, then runs actual float32 EXR readback through
`tests/test_colour_aov_m12_real.py`. Full device/HDR/ACES colour reference approval remains open.

Next: M12.2 explicit scene-linear producer buffers and real depth/normal/velocity attachments from
supported spatial/mesh engines; M12.3 ACES reference profiles, actual external LUT identity and HDR
GPU/CPU parity. M13 timeline I/O remains independently scheduled. M12.1 never equates synthetic AOVs
with measured, motion-captured or production geometry.
