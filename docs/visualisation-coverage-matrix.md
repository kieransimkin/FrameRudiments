# Visualisation coverage: Phase 1 baseline

The renderer registry is the authoritative list of built-in modes. Existing
`tests/test_advanced_renderers.py` asserts the generated gallery includes
**every** registered renderer; the gallery's `catalogue.json` is the public
machine-readable index. Do not hardcode or silently inflate view counts.

## Contracts currently covered

| Contract | Main evidence |
| --- | --- |
| Registry/gallery one-to-one correspondence | `tests/test_advanced_renderers.py` |
| Built-in deterministic synthetic frames | `tests/test_advanced_renderers.py` |
| Native spectrum provenance and rejected uncalibrated inputs | `tests/test_advanced_renderers.py` |
| Cross-family native and 4x vector rendering | `tests/test_coverage_matrix.py` |
| Encoded-frame scene hooks under subframe sampling and seeking | `tests/test_coverage_matrix.py` |
| Full compositor integration, variable BPM and word timing | `tests/test_variable_bpm_words.py`, `tests/test_pipeline.py` |
| Per-element GPU input and overlay routing | `tests/test_visualiser_routing.py` |
| Emitter determinism, cue/excerpt semantics | `tests/test_particle_engine.py` |
| Actual GPU engine and encoded integration | `integration/` (dedicated Linux runner) |

## Still needed for full confidence

1. **Golden images:** platform-tolerant pixel similarity on a small, pinned
   selection of combined scenes; record exact baselines and provenance.
2. **Compound effects:** caption + particles + supersampling + visualiser routing
   in one deterministic scene. Use small synthetic fixtures, not proprietary music.
3. **Performance:** bounded CI bench with per-frame time, peak memory and cache
   growth for 4x/8x rendering, reported as observations rather than unstable
   pass/fail wall-clock thresholds.
4. **GPU evidence:** keep the separate actual-engine integration suite and
   verify both frame content and decoded outputs; never treat mocked backends
   as proof that projectM or Butterchurn ran.
5. **Published gallery validation:** ensure `catalogue.json`, card images,
   gallery HTML, Pages artifact and deployment all agree and are accessible.
6. **Upstream DSP evidence:** do not label movement traces as PCM, or axis-only
   remappings as STFT/CQT/chroma calculations. New representations must arrive
   as versioned, pinned StemLab analyses.

New test modules under `tests/` are discovered by the existing CI matrix via
`python -m pytest -q`, and the gallery workflow's focused suite should include
`tests/test_coverage_matrix.py` when its workflow list is next edited.
