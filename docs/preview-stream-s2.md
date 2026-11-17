# S2 — Seekable cache-backed preview transport

**S3 update:** The authoring UI now requests [adaptive native rendering](preview-stream-s3.md).
The legacy S2 Q0 protocol remains available when `adaptive` is omitted or false.

**Status:** implementation candidate; full exact-head CI pending. This stage
builds on [S0 clock/quality contracts](preview-stream-s0-contract.md) and the
[S1 monotonic SQLite/PNG cache](preview-frame-cache-s1.md).

The existing preview server now offers a **disabled-by-default** local
streaming mode. `react-timeline-sequence` remains the authoritative audio and
playhead clock. One foreground thread owns one running render and one
replaceable pending request. Every cursor command has a monotonically
increasing sequence number: old seeks are rejected, and in-flight results are
never presented or admitted after a newer cursor takes ownership.

The S2 renderer intentionally uses the existing trusted, validated fast
compositor and **downsamples to Q0** using Lanczos. This is truthful quality
labelling, but does *not* speed up the compositor or claim achieved target FPS.
Adaptive quality is S3; independent full-resolution Q5 refinement is S4.
Sequential Butterchurn/projectM feedback and history remain unavailable in
random-seek preview until M11 checkpoint/replay. Production renders and the
original `/api/preview` route are unchanged.

## Loopback API

All POSTs require an exact trusted Origin and the server binds to 127.0.0.1.
No remote access, CORS grants, source downloads or shader execution are added.

- `POST /api/stream/start` with `{project,sidecars,frame,target_fps}` opens
  one new source-pinned, effective-draft-fingerprinted revision; it closes the
  prior session and returns an unpredictable token.
- `POST /api/stream/control` with `{session,command,sequence,frame,target_fps}`
  accepts `seek`, `play`, `pause`, `stop` or `target_fps` with strict JS-safe
  monotonic sequence numbers. `pause/stop` records the last stopped frame.
- `GET /api/stream/events?session=...&after=...` uses short-lived SSE with
  event IDs and retry support. Events include `cursor`, `frame-ready`,
  `overload`, `resync` and `render-error`. The event ring is capped at 128.
- `GET /api/stream/frame?session=...&frame=...&generation=...&sha256=...`
  serves an S1-verified content/generation-qualified RGBA PNG; a stale hash
  or invalidated generation returns 410.
- `POST /api/stream/close` ends the token without deleting useful cached frames.

The cache is stored under the user's `.cache/framerudiments/stream-s2` tree,
partitioned by complete project and draft fingerprints. Each revision has a
512 MiB disk quota, 16 MiB RAM quota and 16 MiB minimum free-space reserve.
Whole-project dependency invalidation applies until S5 verifies finer scopes.
Cross-revision global quota management is not implemented in S2.

Tests cover 1,000 random seeks while a renderer is blocked, cache hits and
invalidated image URIs, loopback Origin enforcement, SSE reconnects and
session shutdown. The full React UI build and exact-head CI are required before
release acceptance.
