# S0 — seekable preview contracts and performance instrumentation

**Status:** implementation candidate; full exact-head CI and project-level
validation pending. S0 does **not** implement streaming, rendering-quality
fallback, frame-cache storage, background rendering or stateful seeking.
See the [S0–S7 implementation roadmap](seekable-adaptive-streaming-plan.md).

## Pure runtime contracts

`framerudiments.preview_stream_contract` is a dependency-free/stdlib-only
package module. It never creates a GL context, loads models, reads media, starts
workers or changes the project's render settings. The project itself must
already be validated with FrameRudiments' existing `Project` contract.

- `frame_clock(project, absolute_frame)` delegates *both* source time and beat
  phase to `Project.source_time()` and `Project.continuous_beat()`. Its exact
  output time is a rational numerator/denominator. Nonsequential seeks and
  repeated excerpts do not advance mutable local time or reinvent StemLab BPM.
- `project_fingerprint(project, renderer_abi=...)` hashes the full project JSON,
  authoritative sample window, all registered SHA-256 source references and
  the explicitly supplied renderer ABI. The diagnostic is a digest; no path,
  lyric or project contents are returned. This fingerprint is a **conservative
  whole-project dependency closure**, not a future selective invalidation proof.
- `section_segments()` uses frame ordinals from the native cue ledger as
  half-open `[start_frame, end_frame_exclusive)` boundaries. If there are no
  section cues, it falls back to fixed 30-second output sections. `segment_for_frame`
  checks contiguity and bounds. Empty/out-of-range cues are ignored; more than
  2,048 boundaries are rejected before any cache is created.
- `frame_cache_identity()` defines S1's candidate identity as a digest of the
  project fingerprint, segment, generation, absolute frame, profile metadata,
  and renderer ABI. **No cache read/write is authorised by the digest alone.**
  S1 must verify the live generation and all source/pipeline identities again
  before admission. The current dependency scope intentionally forbids an
  assertion that an edit is isolated to one segment.

## Declared Q0–Q5 profile ladder

`quality_profiles(project)` returns six immutable profiles, with a strict rank
ordering and output dimensions derived from the validated project. All have
`execution_status: not_implemented` and `production_equivalent: false` until
S3 builds validated profile-specific renderers and M11 establishes stateful
feedback eligibility. S0 deliberately does not silently omit effects.

| Profile | Preview dimensions | Spatial cap | Temporal cap | Effect policy |
| --- | --- | --- | --- | --- |
| Q5 | 100% | **exact authored** | **exact authored** | full authored production pipeline |
| Q4 | 100% | min(authored, 2×) | min(authored, 4) | retain graph and effects |
| Q3 | 100% | min(authored, 1×) | min(authored, 2) | only supported, verified variants |
| Q2 | 75% | 1× | 1 | conservative, declared level of detail |
| Q1 | 50% | 1× | 1 | reduced, declared level of detail |
| Q0 | 25% | 1× | 1 | minimum supported LOD, no silent omissions |

`requested_target_fps()` accepts explicit 15–120 FPS. Without an override it
returns the authored FPS capped by the preview ceiling (60 FPS initially).
A Q5 label is **never** proof of equality to a final encoded frame.

## Performance instrumentation

`PreviewTelemetry` provides a thread-safe, fixed-size rolling window (8–512
measurements, default 120), p50/p95 *fresh render* latency, queue wait and
optional presentation lateness. Counters distinguish new renders, cache hits,
presentation events, repeats, rejected requests, errors and stale generations.
Generation changes reset only the latency window, never claim old results as
new throughput. Invalid times, NaN and stale generation samples cannot affect
current latency decisions. No raw content or filenames enter the receipt.

The legacy local authoring server records actual per-request composition/PNG
render time, failed renders and busy-slot rejections. A read-only, same-origin
`GET /api/preview-telemetry` returns the aggregate snapshot. The one-shot
`POST /api/preview` response remains PNG and unchanged. Because S0 does not
have a presentation acknowledgement or an active cache, its report explicitly
shows `presentation_lag_ms_p95: null`, `fresh_render_fps: null`,
`presented_fps: null`, `cache_active: false`, and
`adaptive_rendering_active: false`. Request-semaphore acquisition is
nonblocking, so its current queue delay is **zero**, not a fabricated backlog.

The preview endpoint remains bound to `127.0.0.1`, and its existing Origin and
request size checks are preserved. No streaming session endpoints or new
network listeners are enabled in S0.

## Acceptance and next stage

Run focused tests using the repository's managed wrapper:

```powershell
pwsh -File scripts/Invoke-Tests.ps1 tests/test_preview_stream_contract.py tests/test_authoring_preview.py -q
```

`tests/test_preview_stream_contract.py` covers random-access repeat/variable
beat delegation, deterministic fingerprint changes, cue boundary off-by-one,
all profile ranks and authored limits, generation-separated proposed cache keys,
invalid input rejection and concurrent bounded metrics. Existing authoring
integration tests verify the one-shot path remains local and immutable.

**S1 now has an implementation candidate:** [persistent monotonic frame
cache](preview-frame-cache-s1.md) with generation-checked compare-and-swap,
verified PNG blobs, quotas and recovery. Conservative whole-project invalidation
remains in place until S5 proves segment-local reverse dependencies.
