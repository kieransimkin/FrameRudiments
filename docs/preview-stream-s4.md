# S4 — Stopped-cursor Q5 background refinement

**Status:** implementation candidate on the S3 native adaptive renderer. Focused
unit/integration qualification is separate from exact-head CI and native-device
acceptance. No new published package, deployment or production approval is implied.

## Behaviour

Enable **Adaptive cache preview**, then keep **Refine paused frames at authored
Q5** checked. Streaming itself remains opt-in. The refinement checkbox defaults
to on within the explicitly enabled adaptive preview; disabling it closes the
old session and cancels its work. The HTTP API requires `adaptive: true` and
`refine: true` explicitly; existing S2/S3 clients retain their earlier behaviour.

The released timeline still owns playback and the audio clock. FrameRudiments
observes standard media events on its timeline wrapper; it does not change the
shared timeline dependency, invoke private component functions, replace the
transport or infer a pause merely because time callbacks stop arriving. The
current wrapper supplies no loop regions. Future independent WebAudio-loop
transport integrations must expose their actual playback state before enabling
this observer for those paths.

A paused cursor settles for 120 milliseconds before sending a pause intent.
Scrubbing sends seek intents immediately and supersedes obsolete refinement;
settling sends one new pause. Actual playback sends play/advance intents even
when the previous position has not changed. A connected stream initially paused
at frame `n` follows the same settle rule.

For an accepted pause/stop at `n`, the coordinator records an increasing stop
epoch and schedules:

1. `n`, then forwards to the active section's exclusive end;
2. `n-1` backwards to that section's inclusive start;
3. later sections in forward order, then earlier sections in reverse order.

The iterator is lazy and does not allocate a list for a multi-million-frame
programme. Existing valid Q5 frames are skipped. If the foreground has already
produced Q5 at the anchor, that frame is reused rather than rendered twice.
Each position is visited at most once per sweep; an eviction does not create an
endless background rerender loop. A newer stop replaces the sweep immediately.

## Ownership, preemption and resource boundaries

`RefinementSupervisor` has one dedicated thread. `SpawnQ5Renderer` starts at most
one child using the explicit cross-platform multiprocessing **spawn** context.
The child creates and owns its own `NativePreviewRenderer`, compositor and native
contexts on its main thread. No parent GPU context, mutable compositor, SQLite
connection or cache object crosses the process boundary.

The implementation adopts a conservative **paused-only** resource policy:
background work is not attempted during playback, even when spare capacity is
suspected. One foreground job plus one coalesced pending request remain the
foreground bounds. A foreground request fences background work, cancels/reaps
the child, and waits for background resource release before native drawing.
When pausing, the foreground owner releases its retained compositor first,
including when the requested frame is already cached. Immutable project and
sidecar preparation can remain for subsequent foreground reconstruction.

The child accepts absolute frame numbers only and always uses the actual authored
`Q5` plan. Reduced foreground quality never affects its settings. It reuses its
Q5 compositor between completed background frames. No new clock or audio
analysis is introduced. Native source, repeat and beat-time APIs remain the
same as the foreground and production compositor.

Parent/child IPC carries small result metadata. Image bytes are written into a
unique, preflighted, project-cache-owned temporary directory and verified before
cache admission. A frame has a 120-second worker deadline (internal maximum
600 seconds), a bounded PNG payload, and the existing native rendering limits.
Cancellation polls every 25 milliseconds; cooperative shutdown is followed by
bounded terminate/kill/join attempts on **only the owned child**. These polling
and join periods are not a promise about GPU-driver latency or a hard process
RSS/VRAM cap. Driver/compiler allocations remain outside the pixel-cache quota.

The parent removes the owned tree only after the child has stopped. Cleanup
failure is surfaced and prevents stacking a replacement worker. Session close,
replacement, UI unmount and server shutdown all request teardown. An already
running foreground native call is not forcibly killed; the existing retiring
session fence prevents starting another session until ownership is released.
No background process exists before a stop requests uncached Q5 work.

## Cache admission and failure handling

Only the parent publishes pixels. Before publication it checks the stop epoch,
foreground eligibility, pinned inputs, exact RGBA dimensions and the S1 cache
ticket's section generation. Foreground and background Q5 use **the same**
renderer/pipeline/settings identity, preventing a false equal-rank conflict.
The isolated child also checks the original on-disk project JSON before and
after each render and keeps edited sidecars in its owned temporary directory.

Publication uses the existing atomic, generation-fenced quality compare-and-swap.
A stale generation is rejected; Q5 cannot be replaced by later Q0–Q4 completion.
An equal-quality disagreement is an error, not an automatic overwrite. Cancelled
or superseded results are discarded even if rendering finished. No worker can
publish after close. Cache quota rejection stops the sweep until a new explicit
stop/restart; it does not spin or silently downgrade a retained frame.

Only an upgrade of the current paused frame sends a visible `frame-ready` event.
Other frames can improve the cache but never move the playhead. Source pin
changes invalidate as required by the existing guard. S4 does **not** claim S5's
precise section-scoped edit analysis: current dependency fingerprints remain
conservative whole-project identities.

Errors expose their class, not source text, private paths or lyric contents.
A render failure stops the sweep; a new stop can retry a healthy supervisor.
A dead supervisor or cleanup failure requires a clean session restart. Stateful
[S6](preview-stateful-seek-s6.md) extends refinement to bounded-history temporal
paths and verified feedback prefix replay. Feedback uses a separate scoped
worker, pays the complete reconstruction cost and fails visibly when it cannot
qualify the requested prefix. The S4 stateless worker remains unchanged.

## API and UI evidence

`POST /api/stream/start` accepts the additional boolean `refine`. It is rejected
without `adaptive: true`. Existing control commands `seek`, `play`, `advance`,
`pause` and `stop` own scheduling; no arbitrary code, process name or file path
is accepted from a new endpoint. Session/origin checks remain unchanged.

`GET /api/stream/status` and SSE events expose a `refinement` receipt with:

- state, stop epoch, anchor/current frame and fixed `profile: Q5`;
- completed/skipped counts, cancellation count and content-free error class;
- active/worker ownership flags and `policy: paused_only_foreground_priority`.

`refinement-progress` events are coalesced to at most four per second except
state/anchor changes. Visible upgrades carry `origin: refinement`. The frontend
retains sequence/session and decoded-image checks, displays refinement state,
and releases replaced image URLs. The existing bounded SSE history and resync
protocol remain in use.

Background render cost, cache hits and current-frame refinement acknowledgements
are **not** fed into the foreground adaptive latency/FPS controller. Q5 is the
best authored preview profile, not a claim of perceptual approval, physical GPU
qualification, encoded export equivalence or a measured display refresh rate.
`production_equivalent` remains false.

## Regression and acceptance commands

```powershell
pwsh -File scripts/Invoke-Tests.ps1 tests/test_preview_refinement.py tests/test_preview_refinement_native.py tests/test_preview_native_project.py tests/test_preview_native_renderer.py tests/test_preview_adaptive_stream.py tests/test_preview_frame_cache.py -q
npm --prefix authoring test
npm --prefix authoring run typecheck
npm --prefix authoring run build
```

`test_preview_refinement.py` uses real PNGs, SQLite and genuine spawned processes
with an explicitly synthetic child target to verify lifetime/IPC/cancellation.
It covers 1,000 stop intents, anchor priority, generation races, pause on a cache
hit, quota refusal, restart, monotonic Q5 admission and no background controller
training. Synthetic process tests are **not** native rendering evidence.

`test_preview_refinement_native.py` uses the unmocked spawned native entrypoint,
Project and Compositor on generated fixtures, compares decoded Q5 pixels with
the authored compositor in arbitrary frame order and checks sidecar/source
preservation and project-edit rejection. It requires a complete checkout and
released native dependencies; it is collected by the normal CPU CI matrix,
without hardware skips. Native rendering and cross-platform CI must be verified
on the exact merged revision separately from the focused local test receipt.

The accompanying S3 regression correction asserts the complete cue ledger:
empty repeat-start clears at frames 0/60 and the edited cue at frames 30/90.
It changes the test expectation, **not** the source-clock implementation.
