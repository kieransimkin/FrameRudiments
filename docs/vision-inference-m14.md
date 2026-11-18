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
