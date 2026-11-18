# M14.1 — pinned, bounded, offline computer-vision inference

**Status:** implementation candidate (full native Windows/Linux CI pending). The
optional `framerudiments-vision` command runs **trusted, user-supplied ONNX**
models on checksum-pinned RGB8 PNG frames. There are no automatic downloads,
public model registries, telemetry, online API calls or imports on the ordinary
project / compositor / video render paths. Segmentation, *relative* depth and
optical-flow outputs are **model estimates** — **never** equivalent to the true
mesh-camera depth/normal/velocity AOVs produced by M12.2 and M12.3.

## Scope and strict tensor contract

| Task | Input | Output | Interpretation |
| --- | --- | --- | --- |
| `segmentation` | Single `[1,3,H,W]` float32 | `[1,1,H,W]` | Probabilities in `[0,1]`; opt-in sigmoid for logits |
| `relative_depth` | Single `[1,3,H,W]` float32 | `[1,1,H,W]` | Finite nonnegative **relative** values, not metres |
| `optical_flow` | Previous/current `[1,3,H,W]` float32 inputs **or** one `[1,6,H,W]` with previous channels first | `[1,2,H,W]` | Authored previous-to-current x/y displacement, in input pixels |

All models must use *static*, exact shapes: batch 1, 16–1024 pixels per
axis, at most 1,048,576 pixels total. Input PNGs are **exactly** the model's
resolution, RGB8 (not RGBA, non-RGB or silently resampled). Normalization is
RGB-to-float divided by 255, then `(value - mean) / std`, with explicit
finite `mean` and strictly positive `std` triples. Outputs must be float32,
finite and within their task's numeric bounds. Values violating the declared
contract fail instead of being clamped. No softmax, semantic labels, image
rescaling, upscaling, confidence calibration or pixel-geometry synthesis is
implied. Use models that actually meet this published input/output contract.

## Request example

Place the JSON beside the local pinned model and input files. Replace all
example hashes with the real lowercase SHA-256 digests. The model is read as
one bounded in-memory ONNX blob; external tensor files or custom-op libraries
are not supported.

```json
{
  "schema": "framerudiments.vision-request.v1",
  "task": "segmentation",
  "model": {"path": "models/segmentation.onnx", "sha256": "<real model sha256>"},
  "current": {"path": "frames/frame-0001.png", "sha256": "<real image sha256>"},
  "input_names": ["image"],
  "output_name": "mask",
  "width": 512,
  "height": 512,
  "preprocess": {"mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]},
  "activation": "identity",
  "timeout_seconds": 60
}
```

Flow requires a `previous` pinned RGB PNG and either the two tensor names
`["previous", "current"]` in that exact order or one name for a six-channel
`previous || current` tensor. Do **not** interchange the direction silently.
Output names and shapes are checked against ONNX Runtime metadata *before*
executing the graph; if the model provides incompatible layouts, a wrapper
model is required. `activation: "sigmoid"` is allowed only for segmentation
logits; probability mode is the default. Depth has **no** physical unit
conversion. An optical-flow receipt identifies the claimed direction and units,
but cannot establish the trained model actually matches them.

## Run and read outputs

```bash
python -m pip install -e '.[dev,vision]'
framerudiments-vision inspect ./request.json
framerudiments-vision infer ./request.json --output ./vision-result-0001
```

The output path **must not exist**. A successful transaction publishes a new
directory containing `estimate.npy` (float32 `(H,W,1|2)`), `preview.png` and
`receipt.json` (input/model/manifest SHA-256, ONNX Runtime version, tensor
shapes, task units, value bounds, and preview mapping). The PNG is **display
only**: relative depth is normalized to the current frame maximum; flow maps
components and relative magnitude to RGB. Source PNG/model files are never
modified. An interrupted, failed or invalid worker leaves no output directory.
The target parent directory must already exist. Concurrent writers must
choose distinct output paths; a directory rename is atomic on local
filesystems but not a portable atomic `no-replace` primitive.

## Boundaries and risks

- Only `CPUExecutionProvider` is admitted in M14.1. ORT fallback is explicitly
  disabled; we do **not** claim CUDA/TensorRT/DirectML parity, GPU memory quotas
  or cross-driver deterministic pixels. These require a separately qualified
  GPU-provider increment.
- Model files are limited to **256 MiB**, PNGs to **64 MiB each**, input pixels
  to **1 MiPixel** and estimated decoded payload to **256 MiB**. The CPU worker
  has a hard parent-enforced **5–120 second timeout** (default 60), one intra-op
  and inter-op thread, and memory-pattern/CPU-arena allocation disabled.
  These are *admission budgets* — **not a hard cap on ONNX Runtime's native
  peak RSS**. A malicious or pathological model can still exhaust resources.
- The child is a **process isolation boundary for timeout and cleanup**, not a
  security sandbox. Only open **trusted models and trusted image files**.
  ONNX native parsing and PNG decompression are not certified safe for
  arbitrary hostile inputs. Hashes protect identity, not the trustworthiness
  of the model or licensing rights. No opaque auto-downloaded weights.
- No model estimates are automatically injected into `post_effects`, the
  effect graph, M12 geometric EXRs, the preview cache or StemLab output.
  Such routing needs explicit source identity, per-frame provenance and separate
  temporal dependency/invalidation rules, and belongs to M14.2.

## Qualification

`tests/test_vision_m14.py` validates strict schema, pin enforcement, topology,
provider refusal, finite output checks, ordering, no overwrite and snapshots
without the native runtime. `tests/test_vision_m14_real.py` *generates new tiny
ONNX graphs locally* and uses the actual ONNX Runtime CPU provider through the
same subprocess as the CLI, including output SHA receipts and failure cleanup.
These graphs are structural examples, **not trained useful CV models**.

CI: [M14 native model qualification](../.github/workflows/vision-m14.yml)
installs `onnx`, `onnxruntime` and the optional `vision` extra for Python
3.11/3.13 on Windows and Linux. A real native acceptance run and independent
model-quality/temporal stability approval must be recorded separately.

**Next M14.2:** supported pretrained model profiles with fully pinned model and
preprocessing identities, optional per-frame post-effect routing, selective
caching and conservative frame-invalidation boundaries. **M14.3:** explicit
GPU-provider capabilities and actual output/budget parity, without claiming
that software fallbacks qualify physical devices.


## M14.2 — versioned model profiles and native effect-graph sources

**Implementation candidate:** the opt-in `vision_sources` field in a normal
`framerudiments.project.v1` declares one or more **explicitly frame-addressed**
M14.1 inference requests and shared versioned profiles. No model or media is
fetched, and no inference runs in projects without `vision_sources`.

A graph may use `vision:<id>` in the same way as any other `source` node. A
segmentation prediction becomes white RGBA with probability in **alpha** (for
`matte` inputs); relative depth is visualised as fixed-range grayscale and
optical flow as bounded signed-R/G component previews with B magnitude. These
RGBA8 surfaces **are not** physically measured geometry AOVs, input-pixel
flow fields or scene-linear HDR data. M12 remains the authority for real
geometry-derived depth, normals and motion. The model task and tensor shape
must match the named profile exactly.

```json
{
  "vision_sources": {
    "schema": "framerudiments.vision-graph.v1",
    "cache_dir": "_vision_cache",
    "max_cache_bytes": 536870912,
    "profiles": [{
      "schema": "framerudiments.vision-profile.v1",
      "id": "people", "task": "segmentation",
      "model_sha256": "<actual-model-sha256>",
      "width": 640, "height": 360,
      "input_names": ["input"], "output_name": "output",
      "preprocess": {"mean": [0, 0, 0], "std": [1, 1, 1]},
      "activation": "identity", "display_range": [0, 1]
    }],
    "sources": [{
      "id": "people-mask", "profile": "people", "missing": "transparent",
      "resample": "nearest",
      "frames": [{"frame": 0, "request": {
        "path": "vision/frame-0000.json", "sha256": "<actual-request-sha256>"
      }}]
    }]
  },
  "effect_graph": {
    "nodes": [
      {"id": "frame", "type": "source"},
      {"id": "vision:people-mask", "type": "source"},
      {"id": "masked", "type": "matte",
       "inputs": ["frame", "vision:people-mask"], "matte_channel": "alpha"}
    ], "output": "masked"
  },
  "render_quality": {"enabled": false}
}
```

The excerpt above shows **additional project fields**; preserve all existing
required project fields, including pinned audio and the authoritative clock.
A request manifest is exactly the [M14.1 format](#request-manifest), and its
own model/current/previous PNG references are verified before compositor
construction. Each `profiles[]` entry identifies a specific model hash,
preprocessing, activation and tensor I/O contract; it is **never** inferred from
a filename. `frames[]` contains at most 256 unique integer output-frame IDs,
with `missing` equal to `error` (default), `transparent` or `hold`. `hold`
never reads a future authored frame.

There is no implicit resampling. If model and native project sizes differ,
segmentation/depth sources may explicitly choose `nearest` or `bilinear`.
Optical-flow projections require matching dimensions: remapping signed
vectors into a different pixel ruler is not silently permitted. `display_range`
is authored and remains fixed across frames; relative depth does not invent
metres or normalise independently each frame. Since M14.1 inputs are bounded to
1024×1024, larger native projects need explicit input resizing for mask/depth.

Caches live under the declared local project directory, have a 64 MiB–2 GiB
quota (512 MiB default), and are keyed by profile digest, request and media
hashes, implementation source SHA-256 and installed ONNX Runtime version.
A hit independently validates every output byte, float32 shape, M14.1 receipt,
cache seal and current model/PNG pins. Missing entries run M14.1's strict
CPU subprocess and publish only after verification; incomplete or edited
entries **fail closed** instead of falling back to another model. Full-frame
preview/S5 includes `vision_sources` among global dependencies: edits to
model pins, frame mappings, preprocessing, display mapping or graph parameters
invalidate dependent output frames conservatively. Changing the prior PNG
invalidates a flow estimate through the M14.1 request identity.

This candidate intentionally limits a vision-backed graph to a **single native
sample per encoded frame at 1× spatial resolution**; set `render_quality` to
`{"enabled": false}` or explicitly configure both sample factors to 1.
It does not pretend to support subframe inference, high-resolution inferred
flow, GPU inference, unbounded model sequences or arbitrary realtime video
analysis. The normal RGB8 pipeline, external visualisers and actual M12
geometry AOVs are unchanged. The cache is not trusted storage; do not edit
or symlink cache entries and prune stale keys offline if the quota fills.

The new CLI command can warm graph caches before a production render, without
opening a compositor or starting an HTTP preview server:

```bash
framerudiments-vision warm-graph ./project.json --frame 0 --frame 12 \
  --source vision:people-mask
```

The first miss invokes the existing isolated ONNX CPU worker; subsequent hits
load verified cached float32 arrays. A warm receipt reports the exact source,
frame and cache key. Missing model inputs and unreadable cache contents are
errors, never silently replaced with random predictions. A `transparent`
missing-frame policy returns a transparent RGBA image with no inference.

M14.2 regression tests include real Project/Compositor graph-source routing,
cache hits and corruption, profile conflicts, frame gaps, signed vector
projection, model input edits and the Windows absolute-path rejection fix.
The M14 CI workflow runs the tests across Windows/Linux Python 3.11/3.13;
real native ONNX and full production acceptance are separate from mock-worker
and pixel regressions.

## M14.3 — frame batching, isolated element masks and explicit device qualification

M14.3 adds `vision_batch_m14.py`, a strictly opt-in **offline batch worker**. It
accepts a bounded sequence of SHA-256-pinned M14.1 request manifests for **one
model, one tensor topology and one preprocessing profile**, sorted by exact
encoded frame number. No frame is inferred from another frame's nominal time;
flow still requires its explicitly pinned prior image. The existing M14.2
`vision_sources` cache remains CPU-based, with independent per-request keys.
Batch output is a separate, fully verified artifact and **is not silently
inserted into that CPU cache**.

```json
{
  "schema": "framerudiments.vision-batch.v1",
  "provider": "CPUExecutionProvider",
  "batch_size": 4,
  "timeout_seconds": 120,
  "gpu_mem_limit_mib": 512,
  "parity": {"max_abs": 0.02, "mean_abs": 0.005},
  "items": [
    {"frame": 0, "request": {"path": "requests/frame-0.json", "sha256": "<64 hex>"}},
    {"frame": 1, "request": {"path": "requests/frame-1.json", "sha256": "<64 hex>"}}
  ]
}
```

Run `framerudiments-vision inspect-batch ./batch.json`, then
`framerudiments-vision infer-batch ./batch.json --output ./result`.
To prepopulate M14.2 graph sources from a **CPU** batch, run
`framerudiments-vision seed-graph-batch ./project.json ./batch.json ./result`.
The import checks that every frame actually belongs to the project, verifies
the runtime and all source pins, then installs independently sealed cache
entries without re-running the model. All newly written entries are rolled
back if publication fails. GPU outputs cannot be imported into the CPU cache.
The optional `providers` command displays installed providers, but *presence is
not qualification*. An output directory is created **only after** an isolated
worker completes, every predicted tensor and checksum is independently verified,
and the inputs are rechecked. A failure/timeout removes staging data. Existing
output directories are never overwritten. Per-frame artifacts include
`estimate.npy`, `preview.png`, and `receipt.json`, with one top-level
`batch.json` recording exact frames, model hash, provider assignments, the
actual run grouping and numerical parity evidence.

**Real batching:** A model declaring dynamic `N` supports batches of 1–8
frames; ONNX models with fixed `N=1` instead run frames serially **within one
retained inference session**. The receipt distinguishes `dynamic_batch` from
`shared_session_single`; it never counts the latter as a batched GPU kernel.
Input/output shape, type, channel meaning and spatial sizes remain as in M14.1.
The worker admits at most 16 requests and 8 Mi input-pixels total, with a
conservative 256 MiB per-group decoded-payload estimate and an explicit 5–600 s
process timeout. ONNX Runtime internal allocations and GPU memory are **not**
claimed to be bounded by that host allocation estimate.

**Execution-provider policy:** The manifest must explicitly name
`CPUExecutionProvider`, `CUDAExecutionProvider`, or `DmlExecutionProvider`.
There is no `auto` choice and no implicit GPU-to-CPU fallback. GPU runs disable
CPU kernel assignment at session creation as well as runtime recovery, require
the exact selected provider to be attached, and inspect actual ONNX kernel
profiling events to establish that every observed node ran on that provider.
A GPU run also executes an independent CPU reference, checks both per-frame
maximum and mean absolute float32 errors, and fails if either tolerance is
exceeded. The receipt includes provider execution counts and wall-clock
measurements. CUDA's optional `gpu_mem_limit_mib` is an arena allocation hint,
**not a guarantee of global VRAM isolation**; DirectML has no equivalent hint.
No result is called physically GPU-qualified until the dedicated manual
self-hosted device workflow passes on that device and driver.

**Per-element segmentation:** `vision_sources` accepts an optional
`element_masks` list, each `{ "element": "layer:portrait", "source":
"vision:people-mask" }`. The source must be a segmentation probability profile
and the element must name a real declared project layer. At graph evaluation,
its probability alpha multiplies **only that isolated layer RGBA source**,
leaving pixel RGB, unrelated layers, the original source and the `frame` source
unchanged. Existing alpha/matte graph controls still govern subsequent
compositing. In particular, this does **not** erase objects already composited
into `frame`; build the graph from `layer:<id>` if the per-element mask should
change its final output. Relative depth and model optical flow cannot be
silently reinterpreted as alpha masks. Frame/asset changes retain M14.2's
conservative preview-cache dependency invalidation.

**Acceptance:** The normal `vision-m14.yml` matrix on Windows/Linux and Python
3.11/3.13 runs static- and dynamic-batch synthetic ONNX models and flow pairs,
and requires native tests without skips. The separate manual
`vision-gpu-m14.yml` workflow requires a trusted physical GPU runner labelled
`framerudiments-gpu`, selects exactly one GPU provider and publishes a numeric
comparison receipt. CPU CI cannot be used as evidence of GPU support. Complete
hardware/driver coverage, inference kernels unsupported by the strict provider,
and model perceptual/semantic accuracy remain independently gated.


## M14.4 — qualified GPU estimate caching and Q0..Q5 prediction reuse

The next opt-in increment is documented in [M14.4 provider caches and bounded
preview prediction scheduling](vision-preview-m14-4.md). It adds a distinct
checksum-pinned GPU provider cache imported only from independently CPU-compared
M14.3 batches, as well as a read-only frame-tap planner and explicit
`inspect-preview` / `warm-preview` commands. Normal authored renders and M14.2
CPU prediction identities are unchanged. Real preview-host integration and
physical GPU acceptance remain separate gates.
