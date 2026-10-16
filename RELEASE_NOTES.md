FrameRudiments 0.3.0 adds pinned upstream preset-library installation and a
repeatable input-response profiling workflow for Butterchurn and projectM.

- 14,680 distinct presets from all six stable Butterchurn bundles and the four
  official projectM packs; 67 shared textures; exact-byte deduplication with
  retained aliases, archive/source hashes and original notices. Libraries remain
  separate from the Python distribution and are not relicensed as MIT assets.
- Feed ordinary sample tune/cover/timed-lyric projects plus three static cards,
  with programme/silence controls. Replay every feedback frame and capture chosen
  intervals, retaining exact source/output times, hashes, PCM and cleanup receipts.
- Invoke released PrismMoves/PixelCue for measured per-frame profiles and separate
  source/output, temporal, audio-control and shared-card comparisons. Preserve
  condition families, response ranges, unavailable states and interpretation limits.
- Resume validates evidence and settings; retries retain earlier attempts. Sparse
  GPU readback and unchanged-input texture reuse reduce profiling overhead.
- The native engine remains the unmodified pinned 4.2 development source. Actual
  source composition (including lyrics and DanceRudiments sprites) is still
  injected before both engines' real warp/composite processing.

Retains the published 0.2.0 source-bound StemLab BPM/first-beat adapter. Lyrics,
cues and captured recordings keep original source time; procedural phase is
separate. No immutable earlier release is changed. Evidence JSON updates now
use atomic replacement, and profiling uses short owned browser-profile paths
to avoid Chromium Windows cache-path limits.
