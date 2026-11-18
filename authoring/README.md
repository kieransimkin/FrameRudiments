# FrameRudiments Visual Authoring

The local-only Phase 5 editor uses the official `react-timeline-sequence@0.4.0`
React control. Run `npm install && npm run build`, then launch
`python ../scripts/authoring_preview.py /path/to/project.json` from this folder.

Full documentation: [docs/authoring-workspace.md](../docs/authoring-workspace.md).

Source assets stay hash-pinned and are never written from the browser. Use
**Export project & sidecars** to create a new portable project bundle, then
validate the result with the existing FrameRudiments `plan` / `preflight`
commands. The one-shot preview omits GPU feedback; the opt-in adaptive stream
uses the verified S6 replay route below.

## M10.2 graph connections

Open **Effect DAG** to connect an output to an input by clicking either port
first or dragging from an output. The selected node's ordered input controls
provide the same operation without dragging. Escape cancels a pending wire.
Fork from a selected node, register an available project source, or create a
copy, opacity, blur, composite, matte, dissolve transition or matte transition.
Multi-input templates require complete ordered source choices before creation;
composites and mattes also support bounded input addition, removal and reordering.

Every connection carries RGBA8 pixels. Mask, displacement and texture labels
describe their native interpretation. Missing sources, self-connections,
cycles and invalid input counts leave the previous draft intact. Existing
specialised effect parameters, native controls and source clocks are retained.
Projects still opt in to `effect_graph` explicitly.

A graph draft needs a current successful check from the native Python validator
before preview or project export. Editing, undoing or redoing invalidates the
previous check; an unavailable check can be retried. The check covers canonical
graph parameters, all branches, provider names and existing control bindings,
without rendering a frame or opening a GPU. Read the [M10.2 connection and
validation contract](../docs/authoring-graph-m10-2.md) for limits and qualification.

## Adaptive preview and paused Q5 refinement

Enable **Adaptive cache preview** in the renderer workspace. Its **Refine paused
frames at authored Q5** checkbox enables a separately spawned native renderer,
supervised by a dedicated thread. After pause or a settled scrub it starts at
the stopped frame, then works through neighbouring frames. Resuming playback
cancels background work before foreground drawing resumes. The timeline/audio
clock is never driven by this worker. See the [S4 worker contract and tests](../docs/preview-stream-s4.md).

Q5 uses the authored quality settings, but remains preview evidence rather than
production or perceptual approval.

## S7 measurements and report export

The streaming panel distinguishes the requested frame from the currently shown
image and its cached quality. Set any whole-number target from **15 to 120 FPS**;
the accepted target appears separately while a change is pending. This target
controls preview effort and does not change the source timeline or project FPS.

Inspect fresh-render and decoded-image rates, p50/p95 service and queue latency,
current Q5 coverage, PNG cache usage and refinement progress. Expand measurement
details for seek processing, first-ready/decode timings and server-process CPU
and current memory. Unavailable measurements remain explicit; a decoded-image
acknowledgement does not certify physical display refresh.

**Download measurement report** saves a bounded, content-free JSON receipt with
source/implementation hashes and scoped observations. It excludes the session
token, source paths, private text and frame pixels, and leaves production and
perceptual approval separate. Polls and exports are fenced to the current
session and invalidation epoch. See the [S7 measurement and native profiling
contract](../docs/preview-qualification-s7.md).

## Verified temporal and feedback seeking

S6 uses native bounded history for temporal shutters and an isolated authored-Q5
prefix replay for Butterchurn/projectM. Each new feedback prefix is independently
reset and compared pixel-for-pixel before presentation. The panel shows replay
and verification progress separately from adaptive FPS. Missing runtimes,
exceeded frame/time budgets or unequal reset output show **Stateful seek
unavailable** and clear old pixels. A newer seek cancels the owned worker tree.
Feedback qualification is scoped to the current stream; reopening revises its
cache. See [S6 capabilities, budgets and qualification](../docs/preview-stateful-seek-s6.md).

## Section-aware draft edits

S5 keeps a stable preview-cache workspace across edited drafts. A change to a
time-bounded layer, or its scoped post-effects/animation/matte/displacement,
invalidates **every cached frame in each dependent section**, while independent
sections keep their best retained pixels. Shared graph, source-clock, quality,
implementation and unknown dependencies invalidate conservatively. The preview
shows an invalidation receipt; an obsolete edit request cannot replace a newer
one. No authored files are rewritten. See the [S5 scope and concurrency contract](../docs/preview-stream-s5.md).
