# FrameRudiments Visual Authoring

The local-only Phase 5 editor uses the official `react-timeline-sequence@0.4.0`
React control. Run `npm install && npm run build`, then launch
`python ../scripts/authoring_preview.py /path/to/project.json` from this folder.

Full documentation: [docs/authoring-workspace.md](../docs/authoring-workspace.md).

Source assets stay hash-pinned and are never written from the browser. Use
**Export project & sidecars** to create a new portable project bundle, then
validate the result with the existing FrameRudiments `plan` / `preflight`
commands. The preview intentionally does not replay GPU feedback engines.

## Adaptive preview and paused Q5 refinement

Enable **Adaptive cache preview** in the renderer workspace. Its **Refine paused
frames at authored Q5** checkbox enables a separately spawned native renderer,
supervised by a dedicated thread. After pause or a settled scrub it starts at
the stopped frame, then works through neighbouring frames. Resuming playback
cancels background work before foreground drawing resumes. The timeline/audio
clock is never driven by this worker. See the [S4 worker contract and tests](../docs/preview-stream-s4.md).

Q5 uses the authored quality settings, but remains preview evidence rather than
production or perceptual approval. Sequential feedback still needs S6/M11.

## Section-aware draft edits

S5 keeps a stable preview-cache workspace across edited drafts. A change to a
time-bounded layer, or its scoped post-effects/animation/matte/displacement,
invalidates **every cached frame in each dependent section**, while independent
sections keep their best retained pixels. Shared graph, source-clock, quality,
implementation and unknown dependencies invalidate conservatively. The preview
shows an invalidation receipt; an obsolete edit request cannot replace a newer
one. No authored files are rewritten. See the [S5 scope and concurrency contract](../docs/preview-stream-s5.md).
