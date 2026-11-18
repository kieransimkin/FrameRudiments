# M14.5 — Live adaptive vision preview, cancellation and Q5 refinement

**Status:** implementation candidate. This extends M14.4's opt-in prediction
profiles; it does not alter a project without `vision_sources` or automatically
download a model. The native authoring preview remains localhost-only.

## Execution path

1. `PreviewStreamAPI` already binds the edited draft, source hashes, segment
   invalidation and monotonic client cursor sequence before scheduling work.
2. `NativePreviewRenderer.render_cancellable()` creates its private Q0–Q5
   project view and tags it with an explicit preview quality. The ordinary
   `Project` and normal encoded output have **no preview-quality override**.
3. Before drawing, `prepare_live_frame()` plans the exact vision dependencies,
   verifies their cache receipts, and uses M14.1's isolated ONNX subprocess to
   fill missing CPU predictions. GPU results must already have passed M14.4's
   CPU-comparison import gate: **there is no implicit GPU→CPU fallback**.
4. The already validated `vision:<id>` graph source and element-mask adapters
   see the **same** frame and Q-level when they produce RGBA surfaces.
5. The normal S2 frame cache admits a render only if the request's revision,
   cursor sequence, frame and cache ticket are still current. A superseded
   prediction stops its subprocess on a bounded cooperative cancellation poll,
   then rolls back its incomplete staging directory. It never publishes stale
   RGB pixels to the browser.
6. On pause, the existing S4 independent refinement worker renders **authored
   Q5**. It uses the same native preview path, but only exact-frame predictions
   qualify at Q5. No intermediate model outputs are relabelled as Q5.

A live stream emits a content-free `vision-progress` event with
`frame`, `profile_id`, `phase`, `verified_predictions` and `reused_taps`.
The React stream panel rejects old cursor revisions and displays the status.
It does not expose source filenames, model bytes, output images or session
secrets in the progress event; ordinary image delivery uses the existing
verified preview cache route.

## Native supersampling and limits

- Q0–Q2 reduce the native **canvas before rendering**. Their vision sources
  must specify `resample: nearest` or `resample: bilinear` in the M14.2 source
  profile; `none` disables the reduced profile explicitly rather than rendering
  broken pixels. Q3 and Q4 retain full canvas dimensions.
- In temporal subframe rendering, the native `_time_sampling_factor` maps a
  high-rate sample `n` to its source **encoded frame** `n // factor`. A model
  estimate is held across subframes of that one encoded frame. This is not
  invented optical flow or a new temporal model sample.
- Q5 preserves authored spatial/temporal AA and requires an exact encoded-frame
  prediction, even where a lower Q-level may reuse recent segmentation or
  relative-depth predictions under its explicit age limit.
- Model-estimated optical flow is never reused between distinct encoded frames.
- M14.5 does not qualify `vision_sources` with sequential Butterchurn/projectM
  effects or native feedback history. Those previews fail capability admission
  rather than silently showing an unqualified stateful reconstruction.
- The optional inference worker remains bounded by M14.1's pinned model and
  input-size limits, cache quota and per-request deadline. Cancellation
  targets the subprocess; it does not certify that an arbitrary ONNX model
  cannot spawn its own descendants or overallocate native/GPU memory.

## Run focused qualification

```bash
python -m pip install -e '.[dev,vision]'
python -m pip install 'onnx>=1.17,<2'
python -m pytest -q tests/test_vision_live_m14.py tests/test_vision_temporal_m14.py
ruff check src/framerudiments/vision_live_m14.py tests/test_vision_live_m14.py
```

The separate `vision-live-m14.yml` workflow runs Windows/Linux Python 3.11/3.13
and the React typecheck. Existing M14 native ONNX and S4/S5 adaptive preview
jobs retain independent status. A green Python unit suite alone does **not**
certify browser paint, physical GPU parity, native-Q5 media output or perceptual
quality.

## Further milestones

Deep integration with sequential external visualiser feedback, production-grade
multi-device GPU resource telemetry, and untrusted ONNX model isolation remain
separate acceptance gates. The M15 ecosystem adapters have their own roadmap.
