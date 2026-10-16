# Preset libraries and input-response profiles

FrameRudiments installs **14,680 distinct upstream presets** separately from the
Python package: **1,738 Butterchurn** and **12,942 projectM** entries. Original
names remain searchable aliases after exact-byte deduplication. The five preset
packs contain 16,334 files before deduplication; 67 shared textures accompany them.
The six existing built-in presets (three per engine) remain available through
the ordinary project format.

![Generated calibration cards used as static inputs](media/preset-test-cards.png)

Generated calibration illustrations: colour bars with grayscale ramp,
asymmetric grid/rings, and light/dark text. These are input cards, not evidence
of a preset's appearance. Their PNG hashes are recorded with each experiment.

The [actual four-second full-frame demonstration](https://kieransimkin.github.io/DanceFlow/framerudiments.html)
shows the composition/effect pipeline, with user-controlled video playback and
an accompanying transcript. It uses the earlier synthetic software demo;
profiling inputs are supplied separately and are never silently substituted.

## Install the complete pinned library

```powershell
framerudiments presets-install local/preset-library
```

Installation explicitly downloads upstream executable equations/shaders. No
download happens during ordinary rendering. Every archive is SHA-256 pinned;
the catalogue retains source commits, notices, aliases, preset hashes and texture
hashes. Packages and caches remain outside the wheel/sdist. The installer reads
bounded ordinary archive members and writes content-addressed preset filenames,
avoiding original-path traversal and Windows filename/length problems.

| Pack | Source pin | Files before deduplication |
|---|---|---:|
| Butterchurn presets, all six stable bundles | npm 2.4.7 | 1,754 |
| Cream of the Crop | 0180df21f5e0bd39b9060cc5de420ed2f1f9e509 | 9,795 |
| Classic projectM | 14a6244a7d32eb7e114e1a92d1cb93358cdcc54a | 4,193 |
| Original MilkDrop | e03b83e3338d8f1ed6cbcf908c719f249ef24288 | 552 |
| En D | fff71ea81223109f3558351667eef851f2781c96 | 40 |
| Shared textures | 6368812f27bc747b517218fbf89d21d59afce4d9 | 67 images |

Butterchurn 2.6.7 accepts the stable pack's JavaScript-equation format. The
upstream 3.0 beta pack uses EEL fields and is not represented as compatible with
this engine. projectM remains pinned to unmodified development commit
`349b7cc2db312d47cef6be24c6856ced6643a385` (4.2.0-dev).

Butterchurn's pack supplies an MIT notice. Some projectM packs supply no explicit
licence grant; Cream/En D describe a historical public-domain assumption, which
does not establish author permission. Keep their exact notices and provenance.
FrameRudiments does not relicense or redistribute these packs as its own MIT
assets. Source availability, shader compatibility and public-use eligibility are
different states. Additional community packs require their own provenance and
format review; raw pack size alone does not establish useful coverage.

## Supply the two tunes, covers and lyrics

Create an ordinary untreated FrameRudiments project for each sample, with pinned
audio, cover background, font and timed LRC. Select explicit source sample bounds
covering the desired excerpt. Use saved StemLab evidence when choosing musical
sections or motion; the profiling runner does not estimate tempo or retime lyrics.
Native DanceRudiments sprite/atlas layers can remain in these projects and enter
the same full-frame processing path. The static-card conditions remove authored
layers and lyrics to isolate the calibration imagery.

```json
{
  "schema": "framerudiments.preset-suite.v1",
  "projects": [
    {"path": "arcadians-project.json", "sha256": "EXACT_PROJECT_SHA256"},
    {"path": "truth-project.json", "sha256": "EXACT_PROJECT_SHA256"}
  ],
  "input_mix": 0.35,
  "seed": 123
}
```

For the maintained My Songs experiment the selected tunes are **Arcadians** and
**The Truth is a Virus**, with their canonical masters, covers and manually timed
lyrics. Their private suite and evidence stay outside the public repository.
An optional `assets` list pins additional canonical/provenance files. Preserve
the original LRC if an export needs a documented parser-input adapter; never
silently alter its words or timings. An estimated clock used by a static test
does not become an approved musical grid.

## Capture, analyse and resume

Set up the two optional engines as described in [visualisers.md](visualisers.md).
Use a separate Python 3.13 interpreter with released **PrismMoves 0.1.0** and
**PixelCue 0.9.0**, plus NumPy/Pillow, for analysis. This keeps heavy optional
analysis dependencies out of the renderer's base installation. Existing local
model-free APIs are sufficient; no model download is needed.

```powershell
framerudiments presets-profile local/suite.json local/preset-library/catalogue.json `
  --output local/response-run --intervals 0 1 2 4 `
  --prism-python path/to/prism-python

# Equivalent script entrypoint in the source distribution:
python scripts/profile_visualisers.py local/suite.json local/preset-library/catalogue.json `
  --output local/response-run --intervals 0 1 2 4 --prism-python path/to/prism-python
```

Intervals are output seconds, rounded up to the next available frame and rejected
outside the programme. Every preceding frame is replayed sequentially. Sparse
captures skip unnecessary GPU readback; they do not skip feedback simulation.
Each preset/condition starts a clean engine with the same mix, frame cadence,
resolution and authored seed. projectM has no public seed API, so its random
state remains an explicit confound. GPU/driver output is not cross-platform
bit-identical.

Default conditions cross each sample with cover/timed-lyrics, colour bars,
grid/rings and text card, then programme audio and all-zero PCM controls: **16
conditions for two samples**. Programme PCM uses the encoder's exact native-rate
trim/repeat/fade filters and causal windows. Album imagery and captions undergo
the same effects as the rest of the composition. Static cards do not inherit
album colours or captions. The catalogue's verified textures are attached to the
actual engine APIs, with runtime receipts.

Use `--id EXACT_CATALOGUE_ID` repeatedly for a selected batch, or `--limit N`
for a pilot; omitted limits select the full catalogue. `--image cover-lyrics`
and `--audio-mode programme` narrow a declared experiment. Changing conditions
requires a new output directory. A full sweep of thousands of presets is an
explicit substantial workload: benchmark a representative batch first, retain
elapsed time and storage evidence, then use the chosen settings consistently.
Lowering frame rate changes feedback behaviour and must remain a labelled
preview, with `fps_reason`, rather than a production-appearance claim.

Re-run the identical command with `--resume` to reuse verified captures. Add
`--retry-failed` for fresh attempts while retaining failures and older profiles.
Changed inputs, runtime binaries, host bridges, textures, settings or capture
bytes prevent stale reuse. Failed shaders are retained explicitly; native shader
fallback errors are not accepted as a successful preset render.

## Assemble one coherent response profile

1. Read `experiment.json` and `index.json` for exact scope and coverage. Available
   catalogue entries are not completed profiles. Every preset has `captures.json`
   with per-condition times, hashes, PCM and engine/cleanup receipts.
2. Open `identity/report.html` to inspect matching input/output frames and the
   original condition families. `identity/prism-*/` contains the actual PrismMoves
   reports, measured palette graphics, PixelCue evidence and checksums. Statistics
   remain observations; optional `--model clip`, `dinov2`, `blip` or `ocr` outputs
   remain interpretations, with unavailable receipts. Models load local weights
   only under the experiment's `models/`; this runner never enables downloads.
3. Use `identity/response.json` for source-to-output, successive-capture,
   programme-versus-silence and same-card/different-tune comparisons. PixelCue's
   `compare_profiles` supplies separate hue, texture and saturation distances and
   supported embedding similarities. Named PixelCue luminance, edge/mirror,
   texture/co-occurrence and spatial-frequency values also retain signed
   input/output changes and observed ranges. No private analysis engine or invented
   overall identity score is introduced.
4. Review palette, geometry, texture, text treatment and temporal behaviour for
   each family before synthesising. Describe a visual thesis supported by named
   conditions; retain controlled variation and outliers. Frame grabs from one
   run share a lineage and are not independent artistic authorities. Keep a
   response range when tunes or cards differ rather than averaging incompatible
   looks into one claimed invariant.
5. Record remaining unknowns and production review: glyph legibility, safe zones,
   sustained motion, flash safety, exact final frame rate, audio listening,
   resource warnings and public-use rights. Measurements cannot establish these.
   A failed preset receives a failure/unknown record, not an invented identity.

## Potential problems

- Thousands of available entries have not all been GPU accepted. Inspect each
  capture state and runtime warnings; missing custom textures and old shader
  dialects can produce failures or an altered appearance.
- Native GPU/driver stalls may still require operator recovery; a completed
  Python report is not evidence that an interrupted job cleaned up. The normal
  Butterchurn route closes its owned headless browser and removes only its
  freshly allocated profile; projectM destroys its native instance/window.
- A report with unavailable neural/OCR models still has useful statistics.
  Keep it partial; installing random/untrained checkpoints would not establish
  visual understanding. No paid inference or automatic model sweep is implied.
- Comparison settings (matte, ICC handling, analysis edge, frame rate, source
  offset, input mix) influence results. Preserve them across a comparison batch.
- Source PNGs and processed PNGs are renderer evidence, not decoded-video QA.
  Encode and inspect the selected production preset using the normal render
  pipeline before publishing a campaign video.

Evidence JSON writes use same-directory temporary files and atomic replacement,
so a failed update preserves the previous valid receipt. Disk exhaustion can
still leave incomplete PNGs or reports: retain the interrupted attempt and
resume only after hash validation, or start a fresh output directory.

On Windows, Chromium's internal cache filenames can exceed its path limit even
when Python can write the evidence folder. Profiling creates fresh profiles in
the short experiment-level `.browser/` directory, with normal verified cleanup.
For a deeply nested output root set `FRAMERUDIMENTS_BROWSER_PROFILE_ROOT` to a
short writable project-local folder. Existing user browser profiles are never
opened, read or reused.

To use a catalogue entry in an ordinary render, copy its verified `preset`
`{path, sha256}` reference into an effect stage with `engine` and
`trusted_preset: true` after the explicit library review. Set
`FRAMERUDIMENTS_TEXTURES_DIR` to the installed library's `textures/` directory
when the preset needs it; the runtime records image hashes and native warnings.
The profiler does this texture attachment automatically from the verified
catalogue. Keep public-use eligibility separate from rendering acceptance.
