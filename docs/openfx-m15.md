# M15.3 — owned native OpenFX v1 Filter/RGBA8 host

**Status: implementation candidate.** This is a *working, intentionally restricted*
OpenFX host boundary, not a claim that arbitrary OpenFX effects, third-party
bundles, GPU surfaces or plugin suites run inside FrameRudiments. Full CI must
accept the exact merged commit. Normal rendering never loads a plugin binary.

## Supported protocol

The adapter implements the standard **OpenFX Image Effect Plugin API v1** (`OfxImageEffectPluginAPI`),
using real `OfxGetNumberOfPlugins` / `OfxGetPlugin`, the plugin's `setHost` and
`mainEntry`, the official `OfxPropertySuiteV1` and `OfxImageEffectSuiteV1`
function-pointer layouts, and the prescribed load → describe →
describe-in-context → create-instance → render → destroy-instance → unload actions.

It supports only the **Filter** context, one `Source` and one `Output` clip,
8-bit interleaved RGBA CPU pixels, unassociated straight alpha, sRGB bytes,
full frame, square pixels, no parameters, no tiling and no temporal/sequence state.
The host copies Pillow top-left pixels into OpenFX bottom-left image bounds
with positive rowbytes, and reverses the row order on output. `RGB8` inputs
are temporarily converted to opaque RGBA8 for the plugin and written back as
RGB8. Frame identity is explicit `(frame, rational fps)` with no new media clock.
A plugin requiring an unsupported suite, context or image format fails explicitly.

This is *not* the full suite/parameter/clip semantics provided by a production
OpenFX host. An otherwise standards-compliant plugin may legitimately be
incompatible and must not be forced to run. This version does not support
imageMemoryAlloc, parameter suites, OpenGL/CUDA/Metal interop, GPU textures,
colour transforms, animated parameters, custom clip layouts or OpenFX bundles
that require deployment of other shared libraries.

## Trust and process ownership

`framerudiments-openfx scan request.json` reads exact local binary hashes,
PE/ELF/Mach-O architecture metadata and an image header **without loading
native code**. The plugin identifier and licence are *manifest declarations*:
scan cannot prove the plugin's ABI or licence. The user must independently
review the plugin's code/licence and explicitly set `trusted_execution: true`.
A SHA-256 match does **not** mean a binary is safe or licensed. Never treat
this as sandboxing untrusted plugins.

`framerudiments-openfx render request.json --output ./new-frame.png` performs
all pin/size/format checks before starting a separately spawned process. It
uses FrameRudiments' existing `preview_process_scope` handshake to establish
an owned POSIX process group or Windows Job **before** the native library is
loaded. The parent terminates the owned scope on completion, plugin crash,
timeout, or error. Worker output remains inside a unique staging directory;
all pins are checked again and the PNG read independently before the output
and its JSON receipt are published using no-overwrite hard links. Failed
publication rolls back both files. No new hard Python package dependency is
introduced by this adapter; `ctypes` and Pillow are already available.

The **pixel-transfer cap** is 128 MiB combined source+destination (max 4096 × 4096,
64 MiB source PNG, 512 MiB native library), and the execution deadline is a
strict integer 1..120 seconds. These bounds are **not a general guarantee of
maximum total RAM/VRAM use inside third-party native code**. A trusted plugin
runs with the user's OS file/network permissions and may request additional
memory; the worker boundary provides bounded waiting and owned-process cleanup,
not security isolation from an actively malicious plugin.

## Versioned request

Place all files under one trusted local directory. Pins must be real SHA-256
of the bytes, and output paths must be new:

```json
{
  "schema": "framerudiments.openfx-filter.v1",
  "plugin": {
    "path": "plugins/curated-filter.so",
    "sha256": "REPLACE_WITH_SHA256_OF_BINARY",
    "identifier": "org.example.my-filter",
    "declared_license": "MIT",
    "trusted_execution": true
  },
  "source": {
    "path": "frame.png",
    "sha256": "REPLACE_WITH_SHA256_OF_FRAME"
  },
  "frame": 18,
  "fps": "30000/1001",
  "timeout_seconds": 10,
  "pixel_contract": {"mode": "RGBA8", "alpha": "straight", "transfer": "sRGB"}
}
```

```powershell
framerudiments-openfx scan .\request.json
framerudiments-openfx render .\request.json --output .\new-frame.png
```

On Windows the plugin is a `.dll`, Linux `.so`, macOS thin `.dylib`.
No plugin/model binaries are downloaded, bundled, loaded by scan, or stored
in the regular `framerudiments` package. Source or executable paths cannot
escape the manifest directory via relative traversal or external symlinks.
The output receipt records the plugin's *runtime-reported* identifier/version,
source/render SHA-256, selected frame/FPS, accepted suites, straight-alpha
sRGB contract and the fact that production approval remains **false**.

## Required native test and CI

The project ships the **original MIT-licensed C source**
`tests/fixtures/openfx_m15/reference_filter.c`. A real C compiler builds a
small OpenFX v1 plugin exporting `OfxGetNumberOfPlugins` and `OfxGetPlugin`.
It requests both standard suites, defines Source/Output clips and inverts
RGB while copying alpha unchanged.

`integration/qualify_openfx_m15.py` compiles that source, loads it only inside
an owned worker, and compares the **entire decoded pixel buffer** to an
independently computed Python reference for RGBA8 at 24, 24000/1001 and 30000/1001 FPS, plus opaque RGB8 at 25 FPS.
 It also compiles fixtures which return a failed status, call `abort()`,
and hang beyond the configured timeout: none may publish an output or receipt.
The dedicated OpenFX workflow runs Python/OS metadata tests on Windows/Linux,
executes the actual C ABI on Ubuntu at two Python versions, and uploads the
exact generated images and JSON receipts.

The existing GitHub Pages workflow publishes the real before/after evidence at
[`/m15-3/`](https://kieransimkin.github.io/FrameRudiments/m15-3/)
**only after the workflow succeeds**. Synthetic input images do not assert
artistic/perceptual quality or license third-party binaries.

## Later work

- **M15.3b:** reviewed parameter/property suites, distinct image depths,
  parameter automation, real third-party plugin compatibility.
- **M15.4:** pinned OpenUSD scene import and optional Hydra qualification.
- **M15.5:** explicit live transport/control profiles and alternative GPU backends.

OpenFX ABI source reference (BSD-3-Clause):
[ofxCore.h](https://github.com/AcademySoftwareFoundation/openfx/blob/main/include/ofxCore.h),
[ofxProperty.h](https://github.com/AcademySoftwareFoundation/openfx/blob/main/include/ofxProperty.h),
[ofxImageEffect.h](https://github.com/AcademySoftwareFoundation/openfx/blob/main/include/ofxImageEffect.h).
This project's reference plugin implementation is original, under MIT.
