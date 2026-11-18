# S3 — Adaptive native preview rendering

**Status:** implementation candidate. This increment follows the merged S0/S1
cache and S2 transport. Exact-head CI must verify the real native integration;
synthetic cost tests do not establish hardware FPS or production approval.

## What changes

The authoring workspace's **Adaptive cache preview (Q0–Q5)** control opens an
adaptive stream. Set a target of 15, 24, 30, 60 or 120 FPS; the API accepts every
integer from 15 through 120. The default is the authored FPS, capped at 60 and
with a minimum target of 15. This does not change the authored FPS. A display,
source clock, CPU, GPU or transport bottleneck can prevent target attainment.

The stream starts at Q5. A single foreground worker owns at most one compositor
and its native contexts, plus that compositor's temporal sampling instance.
It retains the compositor while a profile is unchanged and closes/recreates it
on its owning thread when fidelity changes. Arbitrary seeks keep absolute frame
numbers and the existing Project source-time, repeat and beat APIs.

Unlike S2, adaptive Q0–Q2 change the **render canvas before composition**. The
worker refuses incorrectly sized output instead of resizing it to impersonate
another quality rank. Native setup, PNG encoding and cache admission are
measured; generated frames are stored as lossless straight RGBA8 PNGs. The
native final-frame API can return RGB after its own black-matte composition;
transport adds opaque alpha without changing its colour bytes or resizing.

| Tier | Canvas | Native spatial / temporal ceiling | Further work reduction |
| --- | --- | --- | --- |
| Q5 | Authored dimensions | Actual authored settings | No quality override |
| Q4 | Authored dimensions | 2× / 4 samples | Full graph and authored AA passes |
| Q3 | Authored dimensions | 1× / 2 samples | Up to two final AA passes |
| Q2 | 75% per axis | 1× / 1 sample | One AA pass; audited pixel-unit scaling |
| Q1 | 50% per axis | 1× / 1 sample | One AA pass; audited pixel-unit scaling |
| Q0 | 25% per axis | 1× / 1 sample | Smallest admitted native canvas |

Ceilings never increase an author's lower sample counts. Explicitly disabled AA
stays disabled. Q5 retains the exact authored configuration; this is **not**
a blanket production-equivalence certification. That requires the applicable
native/GPU tests and separate perceptual acceptance.

## Supported paths and explicit limits

Stateless sprite/background composition, simple text and the basic particle,
bar, waveform and path Rudiment viewports have audited low-resolution pixel
adapters. Source references, seeds, motion amplitudes, histories, axes, channel
counts, cue times and artist-approved effect strengths are not reduced. Bars
and waveform adapters require enough space for explicitly selected axes.

The reduced-canvas graph subset includes source/copy/opacity/composite, basic
normalized dissolve/wipe/radial/matte transitions, blur/bloom, matte morphology,
chroma edges and channel displacement. Physical pixel distances are converted
to the derived canvas; normalized progress, colour thresholds and alpha meaning
are preserved. No required effect is silently removed.

Unknown renderer units, rich text effects, sprite trails, pixel-valued control
bindings, several spatial/spectral/procedural graph nodes and external GLSL
are **not** guessed at smaller resolutions. They retain the full-canvas Q3–Q5
path. The controller uses only the contiguous supported ranks ending at Q5;
capability receipts explain the resulting floor. Minimum-size geometry can
further restrict lower ranks. A native validation failure remains a failure,
not permission to replace the scene with an unrelated simplified frame.

[S6 temporal/feedback admission](preview-stateful-seek-s6.md) now adds native
bounded-history temporal shutters at Q3–Q5 and independently verified feedback
prefix replay at Q5. The direct S3 helper remains deliberately stateless unless
the server selects the S6 dispatcher. Emitter/material layers remain unqualified
for adaptive seeking. A missing temporal prefix is never labelled Q5.

## Controller and measurements

Render budget is `900 / target_fps` milliseconds, retaining ten percent of the
nominal frame period for delivery/decode. Bounded samples provide p50, p95 and
an exponential mean. Only current-epoch, fresh foreground completions train it.

A downgrade is **one rank**, after p95 pressure in at least 8 of the latest
12 observations, persistent queued delay of 250 ms, or a guarded catastrophic
stall/allocation failure. Changes have a minimum one-second cooldown. Recovery
requires at least 90 good fresh frames and two seconds with p95 below 75% of
budget. A seek or target change resets the window without assuming a cheaper
scene or resetting fidelity to maximum. Sequential `advance` commands do not
reset the measurement window.

Explicit native project/context setup time is reported separately and excluded
from the steady-state controller sample. Otherwise a costly context rebuild
could cause an endless rebuild/downgrade cycle. `total_service_ms_p95` and
`setup_ms_last` retain that cost; fresh FPS uses real completion timestamps,
so cold setup never masquerades as achieved throughput. Queue wait is separate.
Encoding, pixel verification and cache admission remain in service time.

`fresh_fps` counts new foreground render completions. `acknowledged_fps` counts
non-repeated client decode/presentation acknowledgements. Hits, duplicates and
stale epochs have separate counters. The browser acknowledgement follows image
decode and an animation callback; it is **not** a physical-display measurement.
When the admitted quality floor exceeds render budget, the UI reports that the
target cannot be sustained and retains the last frame with its actual frame
identity. It does not stall or stretch audio to make a misleading FPS claim.

## Delivery, identity and resource lifetime

The client uses the shared timeline's current time directly, without S2's extra
33 ms sampling cap. Target pacing regulates requests only; it does not create
another media clock. Latest-intent coalescing still bounds foreground work to
one active plus one replaceable pending job. Sequence checks also run after
asynchronous image download/decode so a late seek result cannot reappear.

`GET /api/stream/events?...&continuous=1` keeps an SSE connection open for a
bounded 20-second lease with heartbeats, up to two subscribers per session and
a five-second write timeout. Old finite SSE clients remain compatible. This
removes the old per-frame reconnect delay without adding a network dependency.
`POST /api/stream/presented` is idempotent and never changes the cursor.

The new renderer ABI gives adaptive frames a distinct cache identity from S2's
post-render Q0 downsample. Cache admissions include the actual derived profile's
settings hash. A lower-rank completion cannot replace a retained higher rank;
cache hits do not train the controller. Invalidated or mismatched frame URLs
still fail. A stopped frame may upgrade on the foreground worker, but there is
**no S4 background refinement thread** in this increment.

Pins are fully hashed at stream admission. Per-frame checks fence changes to
file identity, size, mtime, ctime and symlink resolution before cache delivery
and publication. Reopening rehashes inputs. Owned, source-timed edited sidecars
are created only in a private cache temporary tree, are separately pinned, and
are cleaned when the native renderer closes. Source project/media files are
never rewritten. This local change detector is not a hostile-filesystem or
untrusted-GPU sandbox.

Native calls cannot safely be interrupted mid-draw. A superseding session
closes the old worker and refuses admission while it is still retiring; it
cannot accumulate one native context per rapid draft edit. The UI retries that
explicit retirement state. Hard process termination/timeouts and independent
background scheduling remain later work. Invalidation is still conservative
whole-project/revision invalidation, not S5's precise section dependency graph.

## API compatibility

`POST /api/stream/start` gains `"adaptive": true`. Omitted or false retains the
legacy Q0 renderer and cache identity. Same-origin, loopback-only controls,
pinned frame URLs, bounded requests, the one-shot `/api/preview` endpoint and
production rendering are unchanged. The new native adapter is imported lazily
and requires no additional dependencies on the base preview path.

Status and frame-ready receipts add capability/floor information, actual
profile dimensions, current controller state and separate throughput counters.
`advance` is an absolute-frame playback request; explicit `seek`, `pause`,
`stop` and `target_fps` commands retain their separate meaning.

## Focused acceptance

```powershell
pwsh -File scripts/Invoke-Tests.ps1 tests/test_preview_stream_contract.py tests/test_preview_frame_cache.py tests/test_preview_stream_s2.py tests/test_preview_adaptive.py tests/test_preview_adaptive_stream.py tests/test_preview_native_renderer.py tests/test_preview_native_project.py -q
npm --prefix authoring test
npm --prefix authoring run typecheck
npm --prefix authoring run build
```

Controller tests inject 5/12/30/70 ms costs, recovery, cooldowns, stale epochs,
setup costs and capability floors. Worker tests exercise real PNG/SQLite
admission, 1,000 coalesced seeks, cached quality preservation, changed pins,
wrong dimensions, continuous loopback SSE, duplicate acknowledgements and
worker retirement. Native lifecycle unit tests explicitly use test doubles.
`test_preview_native_project.py` separately uses the **real** Project/Compositor
for all six canvas profiles, repeated excerpts, arbitrary frame order, temporary
sidecars and Q5 byte parity. The existing CPU CI matrix collects it without
skip-based success. Local synthetic checks are not a substitute for that gate,
the complete React build, cross-platform tests or physical-GPU qualification.

Later candidates add [S4 independent Q5 refinement](preview-stream-s4.md),
[S5 whole-section invalidation](preview-stream-s5.md) and
[S6 temporal reconstruction](preview-stateful-seek-s6.md). S7 profiling and
release qualification remain in the implementation plan.
