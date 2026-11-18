# M14.4 — provider-scoped ONNX estimate caches and bounded preview scheduling

**Status:** opt-in implementation candidate. This milestone does not change the
FrameRudiments production FPS, timing, ordinary compositor, OpenEXR geometry AOVs,
or public browser timeline. Model-generated masks, depth and flow remain
**inferences, not physical measurements**.

## Two separate prediction identities

M14.2 retains the canonical CPU cache key and its original frame-addressed
semantics. M14.4 adds a **different**, provider-qualified GPU cache identity:

- Exact SHA-256 hashes of the M14.1 model, current/previous image, request and
  model profile, plus both worker implementations.
- Explicit `CUDAExecutionProvider` or `DmlExecutionProvider` name and the exact
  SHA-256 of the **M14.3 batch.json result receipt**, itself verified against
  its manifest and per-frame data.
- Exact selected native frame, so an identical source image reused at a later
  frame still has its own traceable provider-cache entry.

A provider cache can be **seeded only** from a completed M14.3 GPU batch with
recorded GPU-node assignments and an independent CPU result within the declared
maximum/mean absolute-error tolerances. Neither GPU-provider availability nor
software-EGL proves physical GPU execution. The GPU cache is verified byte by
byte before use, using float32 tensor validation and the original frame receipt;
corruption, missing inputs or a cache miss are errors. **No CPU fallback occurs.**

GPU caches are offline prediction artifacts, not live inference. Rendering does
not open a GPU ONNX Runtime session, and a GPU-specific output cannot silently
seed M14.2's ordinary CPU cache. Both providers share the existing bounded
project cache quota, but occupy different, content-addressed directories. Model
weights, proprietary user media and provider traces are never downloaded or
published by this adapter. **No driver identity or persistent VRAM cap is claimed**
by the offline cached prediction alone; M14.3's separately reviewed device
qualification remains mandatory.

A GPU-backed source uses M14.2's existing `vision_sources` object with one new,
explicit provider block:

```json
{
  "id": "people-mask",
  "profile": "primary-segmentation",
  "provider": {
    "name": "CUDAExecutionProvider",
    "batch_sha256": "<SHA-256 of the verified M14.3 output/batch.json>"
  },
  "frames": [
    {"frame": 0, "request": {"path": "requests/0000.json", "sha256": "<SHA-256>"}}
  ]
}
```

The preceding `profiles`, `cache_dir`, model/request pins and graph source node
remain exactly as described in [M14.2](vision-inference-m14.md). The `provider`
property is **optional**; omit it for the unchanged CPU implementation. Attach
and verify the original GPU batch before using that source:

```bash
framerudiments-vision infer-batch ./batch.json --output ./gpu-result
framerudiments-vision seed-provider-graph-batch ./project.json ./batch.json ./gpu-result
```

`seed-provider-graph-batch` accepts **only** a manifest matching the configured
provider and pinned `batch_sha256`. It checks all frame/source identities,
validates each float32 tensor, verifies the independent CPU comparison, stages
all entries without overwriting existing ones, checks the aggregate quota,
and rolls back newly committed entries if publication fails. For CPU batches
continue to use `seed-graph-batch` instead.

## Quality-bounded preview frame taps

A vision source may optionally declare **six monotone nonincreasing integer
maximum-age limits**, one for each explicit preview tier Q0..Q5. The last one
must be 0, so Q5 always uses an exact frame. For example:

```json
"preview_max_age_frames": [8, 5, 3, 2, 1, 0]
```

A preview request for output frame `N` may use the nearest **earlier pinned
source request** only when its age does not exceed that tier's limit. No
interpolation, time extrapolation, model prediction, re-timing or invented
optical flow is performed. This reuse applies to segmentation and model-relative
depth only; optical flow requires `[0,0,0,0,0,0]` or an omitted policy.

**Authored rendering is unchanged.** The current Project/Compositor path calls
`VisionGraphSources.source(id, frame)` with no quality argument and continues
to use the original `exact` / `hold` / `transparent` / `error` missing-frame
policy. A preview host has to explicitly call `source(id, frame, quality=N)`.
At that point **Q0..Q5** use the new limits and require a matching source when
there is no allowed reuse, except for the explicit `transparent` policy. The
existing general-purpose preview-stream service has **not yet been connected**
to this API; that is a separate integration and interaction acceptance gate.

Use the read-only planner or explicitly warm only the unique selected requests:

```bash
framerudiments-vision inspect-preview ./project.json --frame 8 --frame 9 --quality 2
framerudiments-vision warm-preview ./project.json --frame 8 --frame 9 --quality 2
```

Both accept repeatable `--source vision:<id>` to limit the selection. The plan
admits at most 256 frame indices and 4,096 source taps; it reports frame/source
mappings, source-frame reuse, provider/cache identity and **unverified cache
presence** without invoking ONNX or reading user pixels. Prewarming verifies
cache contents. A missing CPU entry still uses the existing isolated M14.1
worker; a missing GPU entry **fails** until seeded by a qualified batch.
Changing the model, input assets, frame mappings, provider evidence or preview
limits changes the source/graph dependency fingerprint. The existing S5 preview
cache invalidates shared graph dependencies conservatively rather than claiming
locality the host cannot prove.

## Acceptance and limitations

- M14.1–M14.4 CPU unit/graph integration tests run on Windows and Linux as
  part of `vision-m14.yml`. No additional mandatory runtime packages are added.
- Windows 64-bit worker RSS reporting uses correctly declared WinAPI pointer
  types for `GetProcessMemoryInfo`. Older M14.3 builds could raise
  `ctypes.ArgumentError: int too long to convert` on Windows during otherwise
  successful model inference. The optional measurement now reports unavailable
  instead of failing the inference, with simulated 64-bit handle tests.
- The separate **manual, physical-GPU** workflow runs a real selected-provider
  batch, compares it independently to the CPU, seeds the GPU cache and reads
  back all predicted frames. This cannot be qualified by CPU-only CI.
- Per-element segmentation is the same isolated graph-layer alpha operation
  introduced in M14.3; this increment changes its source selection and cache
  identity, not its colour-space or masking semantics.
- Pixel-perfect GPU/CPU parity is not asserted for all models or devices;
  tolerances must be explicitly authored in M14.3 and independently reported.
- Continuous live-camera inference, asynchronous preview host scheduling,
  cached optical-flow resampling, model accuracy approval and physical-device
  VRAM isolation are **not provided** here.

**Next:** M14.5 host-level preview integration, input pin/revision-aware
cancellation, staged Q0→Q5 promotion and full render/preview interaction QA.
