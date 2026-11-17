# Seekable adaptive streaming preview and monotonic frame cache — implementation plan

**Status:** S0 and S1 implementation candidates; S2–S7 not implemented.
**Owner:** FrameRudiments authoring/runtime.
**Roadmap track:** M10.S (seekable, cached, quality-adaptive preview), alongside M10.2–M10.5;
M11.1–M11.2 are prerequisites for *stateful* feedback seeking.
**Baseline:** [`authoring/src/App.tsx`](../authoring/src/App.tsx),
[`scripts/authoring_preview.py`](../scripts/authoring_preview.py),
[`src/framerudiments/compositor.py`](../src/framerudiments/compositor.py),
[`src/framerudiments/output_quality.py`](../src/framerudiments/output_quality.py),
[`src/framerudiments/chunking.py`](../src/framerudiments/chunking.py),
[`src/framerudiments/work_cache.py`](../src/framerudiments/work_cache.py).

## Goal and hard requirements

Build an **opt-in local streaming preview** that follows the existing
`react-timeline-sequence` playback cursor, seeks immediately, and continuously
adjusts *rendered* fidelity to try to achieve a configurable target frame rate.
The authored project, source audio clock, beat map, word/line cues, frame indices,
and output dimensions remain authoritative. Do not stretch audio or silently
change the frame ledger to make preview look smoother.

1. Playback prioritises the newest requested frame over stale work; no seek may
   wait for earlier frames to finish. Target FPS is **best effort**, not a
   promise that a particular shader/CPU/GPU will deliver it.
2. As measured render latency approaches/exceeds the frame budget, step down
   effect fidelity, source/detail sampling, then preview output resolution through
   **named, ordered profiles**; step up slowly once there is sustained headroom.
   Do not change semantic effect settings, timing, alpha/key correctness, source
   identity, or the project's production configuration to gain speed.
3. A separate **full-resolution/full-quality refinement worker** begins at the
   most recently *stopped* playback cursor. It fills the cache with production
   fidelity frames in a predictable priority order without blocking playback.
4. Store at most one **best verified revision** for each frame under the current
   segment generation. A lower-quality result must never overwrite a higher one.
5. Any effect, graph, rendering-pipeline or relevant source change **for a
   segment** invalidates the **entire frame cache for that segment**, even if only
   one keyframe/parameter changed. Cross-boundary temporal or graph dependencies
   may require more segments to be invalidated.
6. Preview and production remain separate approval states: a cached frame is
   never proof that an encoded final, a feedback visualiser, or a hardware-specific
   shader is approved. Failure or unsupported mode must be visible, not hidden
   behind an unlabelled fallback.

## Existing components and boundaries

- `authoring/src/App.tsx` calls the local `/api/preview` endpoint for one PNG
  after a scrub change. The server currently constructs a fresh `Project` and
  `Compositor` in a temporary work directory and returns a `fast` or `full`
  image; the fast mode disables `render_quality` rather than maintaining tiers.
- `OutputQuality` already defines 1–4× spatial and 1–8 temporal native subframe
  settings plus final AA. The optional effect-graph GPU backend and ModernGL
  surface/target pool are **context-owned** and must not be shared across threads.
- `WorkingCache` stages immutable source inputs and handles resumable offline
  encode/promotion. `chunking.plan_chunks` divides output into cue-aware,
  half-open frame ranges. **Neither is a persistent per-frame preview cache.**
  Reuse the frame/segment ledger and source-pin patterns, but give preview its
  own cache namespace, manifest and eviction rules.
- `visualisers.EffectChain` requires sequential frames from zero. Current
  interactive preview omits Butterchurn/projectM feedback. Do **not** label an
  arbitrary-seek preview as complete when feedback or temporal history is
  missing. Stateful quality is gated by M11 checkpoint/replay support below.

## Runtime architecture

```text
Timeline transport / edits (authoring React)
       | play, seek, pause, changed segments, requested FPS
       v
Local preview stream controller (loopback; one active project session)
       | generation IDs, absolute frame clock, telemetry
       +--> Presentation/front-of-queue worker --> candidate frame --> cache CAS --> UI
       |      newest deadline first; adaptive quality; drop stale requests
       +--> Dedicated refinement supervisor thread
              -> isolated full-fidelity compositor worker (process if CPU/GIL-bound)
              -> start at last stopped frame; fill segment and adjacent ranges
              -> cache CAS (upgrade only) --> UI notifies refreshed frame
       v
Versioned segment manifests and content-addressed preview-frame storage
```

- Use a single session coordinator/scheduler to own playback deadlines,
  background priorities, invalidation generations and active writer leases.
  Provide two **independent** compositor instances and separate OpenGL contexts.
  A Python thread may supervise the refinement queue, but use a separate render
  process when a CPU-heavy operation would contend for the GIL or driver access;
  never transfer a live ModernGL context between threads.
- The foreground pool is intentionally small (initially one active render,
  one queued latest request). Coalesce repeated playhead moves and cancel a
  queued obsolete frame on seek. Running work can finish, but its generation
  token is checked before cache admission and it is not presented after a seek.
- The refinement worker has a distinct I/O and memory budget, yields/throttles
  while foreground deadlines are being missed, and never holds the preview
  compositor's state/locks. Opening a new project cancels both generations.
- Distinguish **requested source frame** (`n`) from a preview output profile.
  Source time, word/LRC timing, frames per second and beat phase are always
  calculated for the same absolute frame `n` independent of fidelity.

### Streaming protocol (proposed; no network service enabled by default)

Keep the existing one-shot `/api/preview` unchanged. Add versioned,
**loopback/same-origin-only** endpoints for session creation, `play`, `seek`,
`pause`/`stop`, target-FPS changes, and close. Stream small JSON events using
SSE (`frame-ready`, `quality-changed`, `refinement-progress`, `invalidated`,
`overload`, `error`) and fetch immutable cached PNG/WebP frame bytes by a
content/generation-qualified URL. Avoid embedding image bytes in JSON. Add a
bounded in-memory recent-frame ring to avoid disk roundtrips on the hot path.
Optional future binary WebSocket transport must not create a second frame
scheduler or clock. Reject out-of-order sequence numbers and old generation
IDs in both server and UI; limit request/event bodies, clients, queue size,
frame dimensions, and outstanding bytes. No remote bind, new trust permission,
asset download or user-authored code evaluation is authorised by this plan.

## Quality ladder and FPS controller

Default target: the project's encoded FPS, capped by a user-configured preview
ceiling (initial **60 FPS**); accept an explicit 15–120 FPS target. The display
may repeat or drop a preview *presentation* frame when rendering cannot keep
up, but never mislabel the repeated picture as a new source-frame rendering.
Audio playback stays source-clocked and never waits for the renderer.

Quality is a **fixed total order**, not an ad hoc score for incomparable
resolution/effect combinations. All ratios below are *preview* settings only.
Implement each profile as a validated derived render plan; neither modify
`project.data` on disk nor use an unverified shortcut for a required matte/key.

| Rank | Output scale | Native spatial / temporal | Effect/detail policy |
| --- | --- | --- | --- |
| Q5 (best) | 100% | Authored, up to 4× / 8 samples | Full effect graph, all enabled effects, final AA; eligible as production-quality only after equivalent-path verification |
| Q4 | 100% | 2× / 4 samples | Full graph, fewer quality samples; no semantic omissions |
| Q3 | 100% | 1× / 2 samples | Full graph, reduced shader/filter precision where an explicit validated preview variant exists |
| Q2 | 75% | 1× / 1 sample | Conservative fast variants, lower procedural tessellation/particle detail where supported |
| Q1 | 50% | 1× / 1 sample | Lower bounded detail and effect sampling; semantic keying/alpha preserved |
| Q0 (floor) | 25% | 1× / 1 sample | Smallest supported deterministic preview; never silently skip required nodes |

A project's true production configuration may exceed or differ from a proposed
profile. The highest rank must **resolve to the actual authored production
configuration**, not blindly assume 4×/8. If a node lacks an equivalent
quality-scalable implementation, retain it at full fidelity or mark that
profile unsupported; do not degrade by silently removing it. `effect_quality`
means declared shader/blur/motion sampling or supported LOD, **not** rewriting
creative effect settings or artist-approved parameter values.

- Instrument **render service time** (queue wait separately) and
  **presentation lateness** per completed, valid frame; maintain bounded
  sliding-window p50/p95 and exponentially smoothed frame time. Budget is
  `1000 / target_fps` milliseconds, minus a reserved presentation margin.
- Degrade **one rank at a time** if p95 exceeds the budget in at least 8 of
  the latest 12 valid frames, or the deadline backlog persists for 250 ms.
  Use an immediate guarded drop for catastrophic stalls/OOM, logging why.
- Upgrade only after at least 90 successfully rendered frames and 2 seconds
  with p95 below **75% of budget**. Require an explicit cooldown (>=1 second)
  between profile changes. Seek resets the measurement window but does not
  assume the new segment is cheap. Avoid oscillation and refuse profiling
  with a stale session generation.
- Limit the foreground to one job plus one coalesced pending frame. If Q0
  misses budget, show the last frame labelled *stale/degraded*, report the
  achieved FPS, and keep audio/playhead time truthful. **Never** claim FPS
  target attainment by counting duplicates or cache hits as newly rendered
  work. Track decoded/presented and freshly rendered rates separately.
- Background refinement uses a fixed best profile (Q5/actual production),
  never inherits temporary low foreground fidelity, and does not perturb the
  foreground controller's measured render-latency window.

## Seek, stop and refinement priority

- **Seek during playback:** immediately change transport time; cancel/coalesce
  old queued requests; display a valid best cached frame for the new index or
  the quickest supported candidate, then continue forward at the target FPS.
  A seek-to-n request cannot be blocked by rendering 0…n−1 for stateless nodes.
- **Pause/stop/scrub end:** record `last_stopped_frame`, session generation and
  monotonic stop sequence. Wake the refinement supervisor and **start at this
  exact frame**. By default render `n`, then `n+1`, `n+2` through the active
  segment, followed by `n-1`, `n-2` down to its start. Next prioritise the
  adjacent segment in the likely direction of travel, then remaining segments.
  Skip already-best valid frames; do not render the same generation twice.
- **New stop:** increment priority epoch; cancel/reorder pending refinement.
  Running high-quality work may finish and upgrade the cache only if its
  segment generation is still current. Reprioritise to the latest stop after
  that result; old positions must not starve the latest one.
- **Resume:** foreground wins compute/GPU/I/O resources. Continue background
  only while foreground deadlines have documented headroom; pause it on
  repeated missed deadlines. The last stop anchor persists until a newer stop.
- Respect boundaries `[start_frame, end_frame)`; derived cue boundaries and
  stable section IDs are calculated from the validated source/beat ledger.
  If no cues exist, use deterministic fixed-duration sections. A seek into a
  new section never changes its frame number or render identity.

## Persistent cache and generation model

Introduce a separate `framerudiments.preview-frame-cache.v1` index (not the
existing encoded `work-cache.v1`). Prefer a project-owned ignored fast-disk
root, with explicit quota and safe-path validation. Never mix private sources
or model weights into published docs, dist, packages or Git. A cache item has:

```json
{
  "schema": "framerudiments.preview-frame.v1",
  "project_fingerprint": "sha256-of-canonical-valid-input-and-project-identity",
  "segment_id": "cue-or-range-stable-id", "segment_generation": 4,
  "start_frame": 600, "end_frame_exclusive": 900, "frame": 712,
  "profile": "Q5", "quality_rank": 5,
  "renderer_abi": "release-and-implementation-identity",
  "pipeline_fingerprint": "sha256-of-effective-render-dependency-graph",
  "image_sha256": "sha256-of-verified-image-bytes", "state": "ready"
}
```

The exact on-disk schema can evolve before the first release. Include pixel
size, format, alpha, colour profile, effective native render settings, inputs
and source pins, renderer/device identity where relevant, temporal dependency
range, and provenance of shader/program versions. Cache **pixel results**, not
merely encoded transport quality; format choices must be lossless for canonical
pixel comparisons. If WebP is used for transport, distinguish it from the
canonical lossless cache image. A cache hit is valid only when *all* immutable
identities and the segment generation agree.

- **Monotonic replacement:** an atomic compare-and-swap transaction installs a
  result only if `candidate.quality_rank > stored.quality_rank`, or if no valid
  result exists. At equal rank, compare expected pixel/hash determinism and
  report a disagreement; don't silently overwrite. `Q5` is never replaced by
  `Q4…Q0`. Incomparable custom profiles require an explicit ordering contract
  or separate variants; never pretend a smaller/faster frame is *better*.
- **Concurrency:** write bytes to a unique temp file in the same cache volume,
  fsync/verify SHA-256, then atomically promote and commit the index with a
  generation-checked lease/transaction. Readers see only committed entries;
  crashes may leave cleanable orphan temps but not torn images/index records.
  The UI receives an upgrade notification only after promotion.
- **Retention:** RAM LRU + bounded disk cache by project, with full-resolution
  image byte accounting and configurable disk quota/free-space reserve. Evict
  nonactive, low-priority frames when necessary; distinguish **evicted/absent**
  from *downgraded*. "Highest-quality cached frame" applies to each **retained,
  valid** frame; a finite cache cannot guarantee infinite retention. Never
  replace Q5 with Q0 just to meet quota.
- **Recovery:** on restart, validate schema, blob digest and source/pipeline
  identities. Rebuild missing index entries or discard bad blobs; no reused
  frames from another project/segment generation. Clean up writes abandoned
  by terminated workers. Never claim an incomplete output is production-ready.

## Segment-scoped invalidation (mandatory)

Each segment has a stable half-open frame range and a monotonically increasing
**generation**. Compute a fingerprint of the *effective render dependency
closure* for the segment, not only the artist-visible graph JSON. Dependencies
include: source audio and beat/word/cue timestamps; layer/source assets and
SHA-256 refs; segment-scoped render layers/meshes/particles; graph structure,
input routing and node parameters; shader source digests and backend variants;
keyframe envelopes; compositor/AA/colour-space versions; profile definitions;
and other code/asset versions that affect final pixels.

On an edit affecting segment S:

1. Validate the new draft and calculate which half-open segments depend on the
   change. For an effect or pipeline edit **scoped to S**, bump S's generation
   and tombstone **every cached frame in S** (all profiles, even unchanged
   pixel hashes), then notify clients and schedule re-render. Do not attempt
   individual-frame reuse inside S for this milestone.
2. If a change is global, touches a shared upstream, moves cue boundaries,
   changes project FPS/clock/source duration, or changes the quality ladder,
   expand invalidation to **every affected section** (possibly the whole
   project). When section boundaries change, remap/regenerate all sections
   touched by both the old and new ranges, never retain orphan frame IDs.
3. Include dependency windows for temporal shutter and future/past samples:
   invalidate all sections whose output reads the edited frames. For feedback,
   invalidate the edited section and **all downstream sections until a proven
   equivalent checkpoint/reset**. If dependency reachability is unknown,
   invalidate conservatively to the end of the project.
4. Cancel old queued work and reject late worker commits carrying the obsolete
   generation. Old frames may be physically reclaimed asynchronously, but may
   never be served as valid. Other independent segments keep their best frames
   and continue rendering.
5. Report the edit cause, affected segment IDs/ranges, old/new pipeline hashes,
   cancelled jobs, evicted frames and invalidated cache bytes in a diagnostic
   event and test receipt. Never accidentally treat dynamic `TIME`/progress
   evaluation as an edit; it belongs to a frame's fixed clock inputs.

Maintain an explicit reverse dependency index (asset→nodes→segments and
upstream segment→downstream segment) for these decisions. No hidden global
cache flush for a genuinely isolated section, and no incomplete invalidation
for a cross-section dependency. Validate segment fingerprints *again* before
admitting background output to cover mid-render edits.

## Temporal and external-effect eligibility

A direct seek into a pure, frame-deterministic DAG is supported immediately.
For temporal effects, motion echoes, history shutters, external Butterchurn/
projectM feedback and similar stateful paths:

- Require an engine-reported dependency range plus deterministic reset and
  checkpoint/replay contract, or a verified prefix replay from an authoritative
  reset. The existing `EffectChain.process()` rejects nonsequential frames;
  do not bypass it or label a feedback-free frame as the full pipeline.
- An exact checkpoint contains the effect/clock/input hashes, engine ABI,
  state payload and the last completed frame. A seek can render from a verified
  checkpoint plus sequential replay to target, with separate budget/cancellation
  accounting. If no checkpoint exists, the system must visibly display
  `stateful_seek_unavailable` or an explicitly labelled stateless proxy while
  normal playback resumes; it must not silently substitute incorrect output.
- Background Q5 refinement of stateful segments waits for M11.1–M11.2
  scheduler/checkpoint implementation or pays the full bounded replay cost in
  its own worker. Checkpoint invalidation propagates downstream as above.
- The real-time target is a **presentation goal**; correctness and honest
  fidelity labels take precedence over a deceptive smooth but wrong render.

## Delivery milestones and exit gates

Each milestone should be a focused PR with tests, a machine-readable receipt,
updated docs, and exact-head CI. The full suite remains owned by CI as specified
in `AGENTS.md`; locally run only the focused affected suites through
`scripts/Invoke-Tests.ps1` and the authoring Node checks.

| Stage | Work | Acceptance gate |
| --- | --- | --- |
| **S0 — Contract/instrumentation** | Define segment IDs, dependency closure, profile ordering and canonical absolute-frame clock; collect latency, queue, presentation and cache telemetry | Pure tests prove frame/beat/source identity at arbitrary seeks, repeat excerpts, variable BPM and effects; no project mutations |
| **S1 — Monotonic cache** | Versioned per-frame immutable blobs, CAS upgrade, generations, quota, atomic recovery and metadata | Out-of-order Q0/Q5 completions always leave Q5; corruption/restarts/evictions detectable; no stale generation served |
| **S2 — Streaming transport & seek** | Add loopback stream control, SSE notifications, coalesced foreground queue and timeline hooks | 1000 random seeks never present obsolete frames; audio clock unaffected; one-shot endpoint unchanged |
| **S3 — Adaptive rendering** | Validated Q0…Q5 derived profiles, measured p95 controller, hysteresis, truthful FPS counters and fallback labels | Synthetic slow-render tests show one-rank downgrade, slower upgrade, no oscillation, bounded queue and no dropped source-clock identity |
| **S4 — Background Q5 refinement** | Independent thread/supervisor, worker process/context isolation, stopped-cursor queue and foreground resource yield | On pause at n, first successful refinement is n at Q5; newer stop wins; Q5 cannot be replaced by a later Q1; session shutdown cleans workers |
| **S5 — Segment invalidation** | Reverse dependency index, generation tombstones, edit events and temporal reach propagation | One local effect edit invalidates every frame of exactly its affected section; global and cross-segment changes invalidate all dependents; no late stale commit |
| **S6 — Stateful seeking (M11 prerequisite)** | Verified checkpoints/prefix replay, separate effect-state caches and eligible engine adapters | Sequential vs random-access frames agree under the declared per-engine tolerance; missing states fail with explicit diagnostics |
| **S7 — UI, profiling and release** | Quality/FPS/cache/refinement indicators, manual target control, CI load/seek gallery, disk/CPU/GPU profiling | UI shows current target/achieved FPS, actual cached rank and invalidations; production render baseline and privacy gates remain unchanged |

Recommended implementation order is S0 → S1 → S2 → S3 → S4 → S5, while
building invalidation generation tokens from S1 so every earlier stage is safe
under edits. S6 may land later with M11. S7 can incrementally land alongside
S2–S6; shipping opt-in stream playback with stateless sources is allowed only
when unsupported stateful modes are explicitly marked or refused.

### S0 implementation candidate — contracts and measurements

The first increment adds pure runtime identity and quality contracts, and
content-free timing instrumentation on the legacy preview endpoint. See
[S0 contract, API and regression tests](preview-stream-s0-contract.md).
No streaming jobs, per-frame disk cache, background worker, quality degradation,
or stateful seek are active. Every S0 segment conservatively depends on the
full project and renderer ABI until S5 provides proven narrower dependencies.

### S1 implementation candidate — persistent monotonic frame cache

The S1 increment adds an independent SQLite-indexed, content-addressed RGBA8
preview cache, quality-rank compare-and-swap, generation fencing, recoverable
atomic writes, RAM LRU and bounded disk quotas. Refer to the
[S1 storage contract, API and regression tests](preview-frame-cache-s1.md).
S0's fingerprints still conservatively bind **every** section to the whole
project. Do not claim isolated section invalidation before S5.

### S2 implementation candidate — seekable cache transport

An explicit authoring checkbox enables one Q0 stream session per local server,
backed by one foreground render thread and a latest-only pending queue. The
[S2 transport reference](preview-stream-s2.md) documents same-origin commands,
3-second SSE reconnects, content-pin image URLs and stale-cursor rejection.
Legacy `/api/preview` remains unchanged. Q0 is a downsample of existing fast
preview, not yet an adaptive low-cost renderer. S3 will add measured quality
control; S4 will add independent Q5 refinement.

## Test and performance matrix

- Pure unit tests: quality-rank ordering/CAS, generation races, edit fingerprint
  dependency closure, section boundary off-by-one, parameter-override stability,
  cache quota, disk corruption and crash recovery.
- Integration: play→seek→pause→seek while workers are mid-render; change one
  effect in the current section; seek to unaffected section; verify old results
  cannot reappear and unrelated Q5 frames survive.
- Golden fidelity: full-refinement output for pure paths must be byte-equal
  (or meet a separately documented GPU-driver tolerance) to the authoritative
  project full frame at identical pinned inputs, clocks and renderer revision.
  Never certify an incomplete feedback path as Q5.
- Controller stress: injected 5/12/30/70 ms renderer costs; targets 24, 30,
  60 and 120 FPS; exercise throttled CPU, low memory and slow disk. Report both
  achieved **new-render FPS** and presentation FPS, p50/p95 render/queue time,
  time-to-first-frame after seek, cache hits, tier changes and Q5 coverage.
  Initially target seek acknowledgement <=50 ms on loopback and first cached
  image <=100 ms where already resident; do not treat hardware-dependent render
  latency as a universal threshold.
- Workers: no simultaneous context ownership, no timebase drift, clean
  cancellation and bounded memory on 1080p and optional 4K, with GPU CI and
  separate physical adapter verification. Stress out-of-order completion and
  stop-before/after section boundary. CI must not pass on skipped GPU tests.
- Evidence: output a JSON `framerudiments.preview-stream-qualification.v1`
  receipt with exact project/segment fingerprints, renderer versions, profile
  transitions, cache hit/upgrade/eviction counts, invalidation causes and the
  observed FPS percentiles. Logs contain no private media or lyrics.

## Non-goals and release safety

S0/S1 implement metadata and a standalone preview-frame cache, but do **not**
enable streaming playback, background refinement, adaptive rendering, stateful
seeking, automatic publishing or network streaming. S1 can store synthetic or
verified caller-provided pixels; it cannot render frames itself. This feature
does not replace the final FFmpeg render or `WorkingCache` promotion pipeline and does
not make GPU/GL code safe to execute without prior trust and process isolation.
No new hard dependencies or services should load for projects that do not opt
into streaming. Production-quality frames require the complete effective
pipeline, verified dependencies and source/clock provenance. Cross-vendor,
perceptual and encoded-output sign-off remain separate release gates.
