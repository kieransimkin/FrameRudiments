# Full visualiser preset analysis: one resumable command

The FrameRudiments preset sweep orchestrates the existing **FrameRudiments →
PrismMoves → PixelCue** pipeline. It does not reimplement Butterchurn, projectM,
PrismMoves' report generation, PixelCue's image measurements or neural inference.

## Quick start (Windows PowerShell)

In the **FrameRudiments repository**, run:

```powershell
.\scripts\Run-FullPresetAnalysis.ps1
```

This is one command to bootstrap the project-local Python environments,
install the pinned Butterchurn/projectM libraries, download the full catalogue,
install PrismMoves 0.1.0 / PixelCue 0.9.0 and their optional visual-analysis
modules, download CLIP/DINOv2/BLIP, prepare Tesseract OCR and analyse every
catalogue entry. The official released wheels are SHA-256 verified against the
checksums in FrameRudiments' integration CI. Source runtime projects must have
supported build tools, a browser with WebGL2 and a C++ compiler (see below).

**Every rerun uses the existing experiment and skips completed presets.** It
preserves failures and partial attempts. A GPU/driver crash affects only the
current preset subprocess rather than invalidating earlier evidence.

Preview a small initial sample without changing the final experiment settings:

```powershell
.\scripts\Run-FullPresetAnalysis.ps1 -Limit 5
.\scripts\Run-FullPresetAnalysis.ps1 -Status
.\scripts\Run-FullPresetAnalysis.ps1
```

The first command creates the same experiment and processes only five pending
presets. The final command continues with all remaining presets. For failed
captures or model inferences, run:

```powershell
.\scripts\Run-FullPresetAnalysis.ps1 -SkipSetup -RetryFailed
```

Inspect the logs and correct missing prerequisites before retrying.

The **inventory** is fully processed once every installed preset has a report,
including explicit `partial` failure receipts for incompatible shaders or
unavailable models. A **fully successful analysis** additionally requires no
partial entries. These are separate statuses; a failed preset is never silently
accepted as a successful visualisation.

### Sample inputs and scope

By default, the script generates an original, **synthetic** FrameRudiments
project with an audio tone, a cover, native movement and timed lyrics. No
private songs are downloaded or included in the repository. To profile your
actual music and covers instead, supply a checksum-pinned
`framerudiments.preset-suite.v1` as documented in
[preset-profiling.md](preset-profiling.md):

```powershell
.\scripts\Run-FullPresetAnalysis.ps1 -Suite .\local\preset-suite.json
```

Use the same suite and conditions on every resume. To change the suite, capture
times, image conditions or model set, choose a **new output folder** via the
Python runner (or archive the old run before restarting). The script refuses
to overwrite an existing run with incompatible settings.

The default tests each preset under **four controlled conditions**: cover and
lyrics versus a geometric calibration card, each with programme or silence
PCM. It captures at 0, 0.5 and 1.5 output seconds; every preceding feedback
frame is rendered sequentially. `-FullConditions` uses all four image families
for eight conditions per sample. With more than one project in your supplied
suite the condition count scales proportionally.

The sweep covers **every SHA-256-deduplicated Butterchurn/projectM preset** in
all six pinned upstream packs, plus the six built-in Butterchurn/projectM
examples. The current library documents roughly 14,680 upstream presets, but
the authoritative number is the checksum-pinned installed catalogue. This
command profiles executable visualiser *presets*, not the separate
FrameRudiments procedural renderer registry (which has its own CI atlas).

### Optional neural and OCR coverage

The default semantic analysis requests **every PixelCue visual-analysis
adapter**: CLIP, DINOv2, BLIP and Tesseract OCR, plus the lightweight
`smolvlm-256m` tagger. PrismMoves saves measured colour/texture/composition
statistics, an evidence-labelled BLIP caption hypothesis, ranked CLIP prompt
hypotheses, optional OCR text, and model-dependent keywords. DINOv2 embeddings
are retained in compressed evidence for future comparison, not converted into
invented labels.

The pretrained neural weights are downloaded explicitly into `models/` with
PixelCue revision/asset checksums, then reused by later invocations.
Tesseract's executable is installed as a system utility if absent; its English
trained-data is **copied into project-local `models/tesseract/`**. All Hugging
Face, Torch, pip and uv caches are redirected to the project-local model tree.

`-AllVLMs` additionally asks PixelCue to run all five supported VLM taggers,
including the much larger JoyCaption model. This can require substantial disk
space, RAM/VRAM and time; it is **not** the default. Use `-NoVLM` to omit the
VLM tagger while retaining CLIP/DINOv2/BLIP/OCR. `-Device cuda` selects CUDA
for models whose framework supports it. The normal default is CPU so it does
not falsely assume a CUDA-enabled PyTorch wheel is present.

Model receipts that are missing, incomplete or unavailable make a preset
**partial**, never "completed". Neural descriptions and captions are
hypotheses, not verified artist intent, object inventories or licence grants.

## Where everything goes

```text
FrameRudiments/
  models/                              # project-owned, Git ignored
    .venv-render/ .venv-analysis/       # isolated Python versions and dependencies
    clip/ dinov2/ blip/                 # pretrained checked model snapshots
    smolvlm-256m/ ...                   # VLM weights, when requested
    tesseract/eng.traineddata           # OCR trained data
    .cache/                             # HF/Torch/pip/uv caches
    release-wheels/ preset-archives/    # checksum-pinned downloads
    preset-library/ runtimes/           # executable presets, textures, native libraries
  visualiser-analysis/full-catalogue/   # ALL analysis output within one folder
    experiment.json                    # immutable settings + source SHA-256
    index.json index.html SUMMARY.md   # complete searchable catalogue and summary
    reports/<stable-id>.json           # per-preset measured/narrative evidence
    reports/<stable-id>.md             # readable visual description/summary
    reports/<stable-id>.pixelcue.json.gz  # full PrismMoves/PixelCue semantic receipts
    previews/<stable-id>.webp          # one representative renderer capture
    _work/                             # ignored: raw PNGs, intermediate reports, logs
    .gitignore                         # protects raw/incomplete work from accidental Git add
```

The **analysis output** is entirely within one folder inside the repository.
Model and executable dependency downloads are separate under the repository's
ignored `models/` folder. Source presets and model weights are **never**
committed or re-licensed; only the user's generated observations, text
summaries, reduced previews and evidence JSON become commit candidates. Raw
PNG captures remain locally in `_work/` for audit and retry but are ignored by
Git to avoid a multi-terabyte commit. The committed evidence includes
condition-specific measurements and SHA-256 identities rather than claiming
raw captures are version-controlled.

After finishing, inspect `index.html`, then run:

```powershell
.\scripts\Run-FullPresetAnalysis.ps1 -Verify
# Manual review before committing:
git add visualiser-analysis/full-catalogue
git diff --cached --check
git commit -m "docs: publish complete preset analysis atlas"
```

Or opt in to the fully gated commit at the end:

```powershell
.\scripts\Run-FullPresetAnalysis.ps1 -CommitWhenComplete
```

The `-CommitWhenComplete` option refuses to commit while **any** catalogue
entry remains pending, if report/model-receipt hashes or preview hashes have
changed, or if unrelated files are staged. It **does** permit committing
truthfully labelled partial/error receipts after every preset has been attempted;
otherwise unsupported legacy shaders could prevent a complete inventory forever.
The `-Verify` command checks the full attempted inventory; add
`-RequireAllSuccessful` to demand that every model and shader completed.
The normal run's non-zero exit status still flags partial analyses for review. It does **not** push or publish the result.
For a large atlas, consider Git LFS for thumbnails and review repository size
before pushing. Model weights must never be tracked by Git LFS either.

## Resume guarantees and limitations

- A lock held by the operating system prevents two simultaneous sweeps. A crash
  releases the lock; it does not require deleting a PID file.
- All results have stable, filesystem-safe IDs. State/configuration is hashed and each preset JSON/Markdown is atomically
  saved immediately. The global HTML/JSON index is regenerated every 25 presets
  by default (adjust with `-CheckpointEvery`); an interrupted run reconstructs
  the index from individual saved receipts. Fully successful entries are reused.
- Each preset gets its own process, browser/native renderer and evidence path.
  Errors, timeouts or driver crashes produce **partial** receipts so other
  presets continue. Logs are retained inside ignored `_work/`.
- Interrupted PrismMoves batch directories are archived and retried without
  deleting previous evidence. Retries never overwrite earlier semantic runs.
- GPU output may differ across drivers. Text prompts, captions and tags are
  model interpretations; no aesthetic/flash-safety claim is inferred.
- Fresh downloads require network access and licences must be reviewed. The
  first run requires free disk space for weights, full executable preset packs
  and raw captures. The total workload can take days or longer.
- On Windows projectM is compiled from its exact pinned development commit.
  The script detects Visual Studio 2022 C++ Build Tools and uses its CMake
  generator. A supported compiler and the relevant projectM dependencies must
  be present. A failed setup leaves caches intact; rerun after fixing the
  prerequisite. A Tesseract installation may need a new PowerShell session to
  update PATH.
- `-SkipSetup` is a fast retry option **after** the full setup has completed.
  Normal reruns also reuse existing model snapshots and runtimes.

## Testing the orchestration

```powershell
python -m pytest -q tests/test_full_preset_analysis.py
```

Use `-Limit 1` to test actual GPU/PixelCue inference on your machine before
committing to a 14,000-plus-preset sweep. A complete numerical/semantic sweep
is not suitable for routine CI; the unit tests use synthetic fixtures and
mocked subprocesses, while the actual renderer integration is an explicit
local workload.

## Release and Git readiness

The installed executable packs and model weights are stored in ignored
`FrameRudiments/models/`, not inside the resulting Git commit. The single
analysis output directory is `visualiser-analysis/full-catalogue/` by default.
The nested `.gitignore` excludes only `_work/` and temporary files so the
curated atlas, text descriptions, compact preview images, measurements, source
hashes and compressed semantic model receipts can all be committed together.

A truthful result can have `total == processed` while `partial > 0`; this
means **every** preset was attempted but not every renderer/model worked.
`-Verify` checks coverage and file hashes. The stricter
`-Verify -RequireAllSuccessful` demands zero partial results.

Git commits should be reviewed for size before pushing. A complete 14,000+
entry catalogue can be large even with compressed receipts and thumbnails.
Do not commit private source songs, system paths embedded in raw logs, model
weights, model caches, browser runtime binaries, or executable preset packs.
