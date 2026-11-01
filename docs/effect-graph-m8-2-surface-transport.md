# M8.2 — explicit surface transport and alpha/colour formats

Increment **v32**, based on merged v31 commit `b1c67455c6a9310877ec0c9e9c07a04c43412bb9`.
This is a development patch, not a release. It does not change published tag
identity, project schemas, shader programs, default rendering, or source clocks.

## Scope and boundaries

`effect_graph_surface_transport.py` defines a small, immutable **byte transport**
ABI separate from the M8.1 `SurfaceContract`. M8.1's graph port ABI remains
**RGBA8, straight alpha, display-referred, top-first, host-resident**. The GPU
backend still returns a host `PIL.Image` at the end of every accelerated node.

| Transport payload | Alpha interpretation | Colour interpretation | Uses a GPU shader? |
| --- | --- | --- | --- |
| `rgba8`, straight | Unassociated RGBA | Original display-referred values | **Yes**: existing GL bridge |
| `rgba8`, premultiplied | RGB associated with coverage | Original display-referred values | No, explicit host staging only |
| `r8` | One-channel coverage | Data, no RGB | No, explicit host staging only |
| `rgba32f`, straight | Unassociated floating-point RGBA | **Explicit** linear-sRGB | No, opt-in host staging only |
| `rgba32f`, premultiplied | Linear RGB times coverage | **Explicit** linear-sRGB | No, opt-in host staging only |

Formats not in this table, such as RGBA16F/ACES/HDR, are **not** represented as
working transports and are rejected. No colour-profile inference occurs.
`display-referred` in the legacy renderer does not prove that its RGB bytes
are sRGB-encoded. Only an explicit caller assertion, `source_encoding='srgb'`,
permits linearisation. Re-encoding a float surface into sRGB bytes likewise
requires `output_encoding='srgb'`. This conversion is *not* inserted into the
legacy render path, keying algorithms or spectral selectors.

## Buffer contract

`TransportContract(width,height,pixel_format,alpha,colour_space,row_order,alignment)`
is frozen and validates its complete shape and semantics. It has a positive
integer size, a maximum dimension of 16384, a maximum 67,108,864 pixels, and
row alignments of 1, 2, 4 or 8. Rows can be top-first or bottom-first; explicit
alignment padding is **zero-filled on packing and omitted on unpacking**.
Float pixels are little-endian IEEE-754 float32 and checked for finite values
in `[0,1]` during decoding. `TransportPayload(contract,data)` is immutable and
rejects incomplete, excess or mutable buffers before unpacking.

The RGBA8 straight/default path is **byte-identical**, including hidden RGB
beneath alpha zero. Premultiplication and float conversions necessarily lose
some RGB precision beneath low alpha; alpha zero decodes as transparent black.
These are explicit choices, not the default project output policy.

```python
from framerudiments.effect_graph_surface_transport import (
    TransportContract, pack_image, unpack_image,
)

# Exact Pillow wire image, with explicit rows and padding for an external tool:
contract = TransportContract(320, 180, row_order='bottom-first', alignment=8)
packed = pack_image(rgba_image, contract)
assert unpack_image(packed).tobytes() == rgba_image.tobytes()

# Opt-in float linear-light staging. Caller certifies that source RGB is sRGB:
linear = TransportContract(320, 180, 'rgba32f', 'premultiplied', 'linear-srgb')
float_payload = pack_image(rgba_image, linear, source_encoding='srgb')
restored = unpack_image(float_payload, output_encoding='srgb')
```

## Existing ModernGL bridge integration

The previous `.tobytes()` upload and `Image.frombytes(..., fb.read(...))` download
are now routed through the validated `bridge_contract()` and
`TransportPayload`/`pack_image`/`unpack_image`. The actual transmitted format
is deliberately **unchanged**: four 8-bit channels, straight alpha,
`top-first` Pillow byte sequence, alignment 1. There is no row flip, colour
conversion or new shader. Original alpha metadata is still uploaded expanded
as **RGBA8**, exactly as in M8.1; reducing it to an R8 GPU texture is a later,
separately parity-tested optimisation.

`GLMatteBackend.begin_transfer_sample()` resets a small bounded counter object.
Each successful upload/readback adds its physical payload size. The graph resets
these counters for each frame before GPU dispatch; direct backend callers can
reset explicitly. Failed or malformed readbacks are not credited. Counts cover
only bytes handed to/from the ModernGL API and do **not** measure PCIe traffic,
GPU allocation overhead, VRAM residency or asynchronous driver copies.
The M8.1 `backend_plan` and `backend_registry` expose the same transport policy.
They do not allocate, probe a GPU, or assert that all host nodes execute on GPU.

## Integration and acceptance gates

- CPU tests cover all supported format/alpha/row-order combinations, nontrivial
  strides, byte-exact legacy RGBA8, transparent hidden RGB, premultiplication,
  linear-sRGB references, malformed payloads, and physical transfer accounting.
- Required Mesa/EGL tests check a real upload/fragment-copy/readback roundtrip,
  plus original-alpha/matte GPU parity and exact bytes transferred. The workflow
  checks JUnit collection and rejects **all** skips, failures or errors.
- Native Project/Compositor/DanceRudiments tests check frame seeking, opt-in
  preflight, temporal supersampling and source-project immutability.
- No new package dependencies, DSP, models or network transports are introduced.
- The CPU-only path must not import or create ModernGL. Legacy graph renders and
  all no-effect projects must retain byte-identical output under identical inputs.

**Not implemented:** GPU-resident DAGs, R8 device lineage, float OpenGL textures,
OpenColorIO/ACES, mixed-alpha shader ports, texture pooling, residency scheduling,
external shader hosts, and actual HDR rendering. These belong to later M8/M12
stages. The graph remains authored and driven through existing DanceRudiment
controls and saved StemLab recording inputs.
