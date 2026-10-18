# Hybrid preset portraits and contact sheets

The current workflow is **capture -> numerical temporal/audio analysis -> contact
sheets and original keyframes -> selected appearance analysis -> one paragraph**.
Captions do not gate motion measurement and cannot establish audio causation.

## Entry points

`Run-FullPresetAnalysis.ps1` now defaults to this workflow. `-Legacy` explicitly
selects the old reference-fed analysis; setup-only calls remain setup-only and do
not recursively start the hybrid sweep. `Run-ControlledPresetAnalysis.ps1` uses
the same new implementation. The full command defaults to the remaining catalogue;
use `-Limit 2` for a pilot. Presets still alternate between projectM and Butterchurn,
with a fresh random order unless `-OrderSeed` is supplied.

New captures default to `visualiser-analysis/full-catalogue/hybrid/`. This nested
workspace avoids overwriting the legacy full-catalogue experiment. Old outputs
are neither deleted nor silently reclassified as reference-free captures.

```powershell
.\scripts\Run-FullPresetAnalysis.ps1 -Limit 2
.\scripts\Run-FullPresetAnalysis.ps1 -Status
.\scripts\Run-FullPresetAnalysis.ps1 -Legacy -Limit 2
```

## Restore sheets and enrich existing controlled captures WITHOUT rendering again

Use the original local catalogue with its `_work/<preset>/captures.json` and PNGs:

```powershell
.\scripts\Run-FullPresetAnalysis.ps1 -ReanalyseExisting `
    -OutputDirectory '.\visualiser-analysis\audio-response-catalogue'
```

This needs no native renderer or FFmpeg preflight. It verifies retained captures,
recomputes motion from their original pixels, restores contact sheets, and adds
appearance observations to the existing reports. Previous exports are copied to
`history/` before replacement. Full raw captures and PCM remain in `_work/`.
Committed numerical exports alone cannot reconstruct missing rasters. A missing
or changed raw capture stops reanalysis rather than silently rendering a replacement.

## One paragraph and an auditable visual comparison

Each summary covers available evidence for palette and general visual character,
shapes/structure, characteristic motion in silence and music, and measured sound
responses. Model interpretations are introduced as observations, never facts about
physical objects, artistic intention or audio causation. Preset names, reference
filenames and neutral-image controls cannot contribute claims.

Every condition has paged contact sheets including several times, a bright frame,
and representative pre-onset/response/recovery times. A separate sheet contains
EVERY image selected for semantic analysis. `previews.json` maps tiles to frame
IDs, times and hashes. `keyframes/` retains exact original PNG bytes; contact
sheets are resized, labelled previews, not replacements for numerical evidence.
Reports and the searchable catalogue link to the sheets.

`evidence.json.gz` retains every decoded frame record, adjacent transition,
rejection reason, long-baseline estimate, condition and stimulus metadata.
Per-capture `pcm_rms` is now carried into the temporal export. The public capture
timeline also records source/output hashes and PCM sample bounds derived from
the recorded render-clock/history-window contract. Original PCM is not committed.

Separate completion states distinguish capture completion, global/local motion
coverage, appearance inference, contact-sheet integrity, and description limits.
An unavailable model cannot be presented as a completed appearance analysis.

## Appearance inference is bounded and offline by default

Up to 24 distinct, non-uniform, checksum-verified **zero-reference-feed output**
images are selected, balancing silence, musical input, tone probes, independent
trials and visual novelty. CLIP/BLIP are loaded once per batch/model, not once per
captured frame. Exact model/image/prompt identities support cache reuse when
checkpoints are pinned; near-duplicate captions are never propagated.

CLIP's abstract shape, surface, symmetry and aesthetic vocabulary supplies guarded
appearance terms after support from at least two distinct images. BLIP captions
are retained as additional inspectable observations, not passed through verbatim
as trusted preset prose. Negative phrases such as 'without glow' cannot create a
glow claim. Morphology and material descriptions remain hypotheses.

All model weights and caches use the owning FrameRudiments `models/` directory.
No model weights are included in this bundle or downloaded by its installer.
Existing local models are used by default. Missing models leave the numerical
results and contact sheets intact and label appearance as unavailable/partial.
`-AllowModelDownloads` explicitly permits missing weights to be downloaded.
`-NoSemantics` retains the measurement-only mode without losing contact sheets.
`-SemanticBudget 12` reduces the cap; it is not a requirement to fill every slot.

## Motion and sound-response changes

PixelCue retains its guarded whole-frame similarity estimate and independently
checks dense local flow when global motion is unresolved. Exposure normalisation,
forward/backward consistency and photometric checks reject unsupported local
motion. A local result never fills in missing global rotation or scale values.

Non-overlapping longer-baseline comparisons test slow contraction/expansion and
slow rotation. Several consistent measurements over time are required; this does
not merely lower the adjacent-frame threshold. Missing rasters and large gaps
break the longer-baseline history. These are engineering estimates, not perceptual
quality or flash-safety certificates.

The summary groups shared responses across all tested frequencies rather than
selecting the two highest-scoring tones. Changes during sustained exposure are
worded differently from onset-associated responses. Frequency preference needs
a direct matched comparison, not a larger score against silence. Tone response
is not proof of response to a particular instrument, drum, vocal or separated stem.

## Development distribution and validation scope

This update supplies PixelCue 0.9.2.dev1 and PrismMoves 0.3.0.dev2 development wheels.
The installer puts them in `models/.venv-hybrid-analysis/` and reuses dependencies
from the existing project-local Python 3.13 analysis environments read-only. It
does not replace those environments or modify `analysis-releases.json`. Source
patches are included for both packages; specify their exact checkout paths to
apply those patches as well. No repository is automatically committed or pushed.

Stop an active capture/report job before applying source changes. Real Windows
PowerShell, native projectM/Butterchurn rendering and pretrained inference still
need the local pilot; the automated fixtures do not certify all visualisers.
