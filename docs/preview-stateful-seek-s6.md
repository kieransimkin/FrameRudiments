# S6: verified temporal and feedback seeking

**Status: implementation candidate.** S6 extends the opt-in adaptive stream to
native temporal shutters and to feedback pipelines that pass bounded, exact
prefix reconstruction. Source audio, excerpt/repeat clocks, frame indices,
effect settings and production rendering remain authoritative. The existing
one-shot `/api/preview` still omits external feedback and keeps its existing
diagnostic; enable **Adaptive cache preview** for the S6 route.

## Supported paths

| Pipeline | Reconstruction | Quality profiles | Failure behavior |
| --- | --- | --- | --- |
| Existing independent frame sources | Existing native direct seek | Existing eligible Q0–Q5 subset | Existing explicit unsupported profiles |
| `post_effects.temporal` | Native `TemporalShutter` recomposes its bounded prior-frame reads | Full-canvas Q3–Q5; Q5 retains authored settings | Native validation errors stay visible |
| Butterchurn/projectM effect chain | Sequential frame-zero prefix, independently reset and compared | Authored Q5 only | `stateful_seek_unavailable`; no feedback-free replacement |
| Unqualified layer families | Remain explicitly unsupported | None | Admission refuses them |

Temporal shutter dependencies use the native engine's maximum eight-frame
lookback. The native sampler still excludes preceding repeat-cycle pixels.
The dependency index conservatively expands section reads by the full bound,
including when a modulation changes the shutter span. A layer edit near a
section end therefore invalidates the adjoining section when its shutter can
read that layer; independent sections retain their cached Q5 pixels.

There is no projectM or Butterchurn opaque checkpoint-export API in the current
native adapters. S6 does not invent one. Its alternative allowed by the plan is
verified prefix replay from the authoritative frame-zero reset. No cue, section,
pause, seek or repeated excerpt is treated as a feedback reset.

## Exact production pipeline and verification

The isolated native adapter uses the same ordering as the production loop:

1. `Compositor.frame(n, apply_quality=False)` at the authored frame and quality.
2. `EffectChain.process(...)`, including native PCM, preset timelines, routing,
   isolated surfaces, stage injections, input/feedback/output hooks and overlays.
3. One final `compositor.output_quality.apply(image)` after the effects.
4. Add opaque alpha to native RGB output for the lossless RGBA8 PNG transport.

The engine's sequential-frame check remains enabled. Feedback is never omitted
to make a frame appear complete, and output pixels are never resized to claim Q5.

The first request for frame `n` renders every frame from zero through `n`,
hashing actual RGBA8 pixels. The adapter closes that native pipeline, creates an
independent frame-zero pipeline, and compares **every** frame of a second prefix
before admitting the requested image. The declared tolerance is **zero RGBA8
channel difference**. A new forward extension is also independently verified
against its entire prefix. A backward seek recreates the pipeline and checks
every replayed frame against the verified prefix receipts. An already verified
live result can be reused for an identical request.

This qualifies the observed prefix on that runtime. It does not establish
universal determinism for future frames, other presets, devices or drivers.
In particular, projectM has no public seed/reset determinism guarantee; a
nonrepeatable preset is refused with `replay_mismatch`. A seeded Butterchurn
configuration must pass the same comparison.

## Separate state and frame caches

`VerifiedPrefixReplay` retains one owned native pipeline, one final image and a
bounded sequence of immutable pixel/prefix hash receipts. The receipt chain
includes the exact engine identity and last completed frame. This is a separate
in-memory verification cache; it contains **no serialized engine state** and
cannot restore a context without replay. Partial candidate receipts never
become valid when a render fails or a newer seek cancels verification.

Native identity covers the effective project, quality and frame clock, all
discovered source/preset/modulation hashes, exact PCM, adapter ABI, backend
evidence and external runtime manifest. Runtime admission pins executable,
locked npm bundle, bridge, native-library/receipt and texture identities.
Between frames it checks core inputs; request boundaries and fresh resets
verify the full runtime manifest before output can be published. Missing
prerequisites produce a content-free unavailable diagnostic.

The existing SQLite/PNG cache remains the only pixel store. The parent alone
publishes through the S1/S5 generation-fenced, monotonic Q5 admission path.
Each worker result carries the verified frame/prefix hashes and engine identity;
the parent decodes the PNG and compares its pixels to that receipt. Foreground
and refinement must bind to the same engine/device identity before either can
publish. Logical sidecar content belongs to that identity; its temporary
directory placement does not.
Feedback pixels are qualified for the **current stream**. Reopening a feedback
stream changes its qualification identity and revises the existing workspace
cache, with `qualification_reset: true` in the admission receipt. It does not
create a new unbounded cache directory per seek or draft. Native independent
and bounded-history sessions keep ordinary S5 section reuse.

A parity or runtime/input identity failure revokes previously cached feedback
pixels and stops both producers. The revocation applies even if a newer cursor
or stop epoch superseded the job that discovered it. Pending work cannot start
or publish after revocation. Transient frame/time-budget failures are local to
the requested position and permit a later, earlier-frame request.

## Budgets, scheduling and cancellation

Default feedback limits are 1,800 frames in an authoritative prefix, 3,600
actual frame renders per request (including verification), and 120 seconds
per isolated request. Frame numbers are zero-based: frame 1,799 is the last
eligible prefix target under the default. These are reconstruction bounds,
not promises of a particular target FPS. Native complete-programme stereo PCM
has a separate 256 MiB admission limit and requires its predicted byte size plus
a 16 MiB free-disk margin before FFmpeg starts. The preview refuses an oversized
programme; it does not truncate the native audio or change its clocks. These
checks do not impose an operating-system RSS/VRAM quota.

Foreground work has one active request and one replaceable latest request.
The parent polls cancellation and progress at short intervals; replay checks
between native frames. Foreground and background use separate spawned native
owners and never share a live GL context. A stopped-cursor Q5 job pays the same
replay/verification cost in its own worker and yields before foreground work.
Replay cost does not train the adaptive quality controller or lower feedback
to a cheaper, semantically different profile.

Cancellation closes native resources cooperatively, then terminates the owned
process scope if necessary. POSIX uses a fresh session/group with an unreaped
leader; Windows assigns a non-breakaway, kill-on-close Job Object before native
work begins. The S6 Butterchurn launch uses the pinned public browser-launch API
with `detached: false` so Chromium stays in the owned POSIX group. The normal
production launch path is unchanged. These are trusted-process ownership
controls, not a security sandbox for arbitrary native code.

The parent verifies scope termination before deleting result/sidecar/profile
directories or admitting a replacement. A cleanup failure blocks replacement.
No cleanup searches for or kills unrelated browsers or arbitrary process IDs.
These bounds cover explicit cancellation/shutdown. Abrupt termination of the
whole server is not a POSIX crash-recovery guarantee for a blocked native call;
external executables that deliberately escape the inherited session are outside
the supported ownership contract.

## Protocol and authoring display

Start/status/frame notifications include a `stateful` object. It reports mode,
phase, requested frame, live replay position, verified-prefix count, actual work
frames and limits. `stateful-progress` events distinguish replay from the
independent verification pass. `stateful-seek-unavailable` supplies a bounded
reason code for prefix/work/time limits, missing runtime, reset disagreement,
changed inputs or cleanup failure. Neither event exposes private paths or text.

The client accepts progress/failure only for its current cursor sequence and
frame. A current failure aborts fetch/decode, clears old visible pixels and
releases the playback request gate. A stale failure cannot remove a newer
image. Cached-ready status names the frame actually served. Feedback history
and Q5 progress are displayed separately from ordinary adaptive FPS metrics.
The audio/timeline cursor remains the sole playback clock.

## Verification and remaining qualification

Focused CPU checks exercise receipt/reset logic, cancellation, bounded work,
parity failure, native composition/effect ordering, actual temporal random
access, section propagation, process ownership and latest-intent cache races.
Synthetic feedback backends are identified as doubles; their pixels do not
qualify Butterchurn or projectM. Native external-engine cases belong in the
existing no-skip `integration` visualiser job, with its pinned runtime and
isolated GLX environment. UI tests cover replay summaries, event fencing and
actual packaged Node-worker launch/cleanup using explicit npm API doubles.

Run the affected checks through `scripts/Invoke-Tests.ps1`, then authoring tests,
typecheck/build and Ruff. The delivery receipt records which gates actually ran
and any host limitation. Exact-head Windows/Linux CI, real feedback-engine
parity, physical GPU qualification, long-project memory/latency profiling and
encoded-output/perceptual approval remain distinct. S7 owns the broader
profiling/gallery/release milestone. No source files, package versions, release
tags or external publishing destinations are changed by preview use.
