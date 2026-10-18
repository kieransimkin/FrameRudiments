# Controlled preset audio responses

> The current launcher uses the hybrid report workflow, including contact sheets
> and bounded appearance inference. See [hybrid preset analysis](hybrid-preset-analysis.md).
> Use `-NoSemantics` for measurement-only descriptions with contact sheets retained.
> The reference-free capture protocol and numerical response controls below are unchanged.

The new `Run-ControlledPresetAnalysis.ps1` measures the preset with **zero
reference-image contribution**. It does not use artwork, lyrics, colour bars,
rings, test-card text or captions to write the preset summary. The ordinary
renderer, legacy profiling commands and stable analysis release lock are unchanged.

## Run a pilot

Install the supplied development PrismMoves wheel into the **separate** project-local
`models/.venv-audio-effects` environment using the update bundle's installer. It
uses the released PixelCue 0.9.1 sequence API. Do not overwrite or retag the existing
PrismMoves 0.2.0 release. After validating the pilot, release/pin a new PrismMoves
version through the usual tested process.

```powershell
.\scripts\Run-ControlledPresetAnalysis.ps1 -Limit 2
.\scripts\Run-ControlledPresetAnalysis.ps1 -Extended -TextureControls -Limit 2 `
    -OutputDirectory 'visualiser-analysis\audio-response-extended'
.\scripts\Run-ControlledPresetAnalysis.ps1 -AudioFile 'Z:\My Songs\track.wav' -AudioStart 30 -Limit 2 `
    -OutputDirectory 'visualiser-analysis\audio-response-music'
```

Changing a capture option requires a new `-OutputDirectory` under
`visualiser-analysis/`. For example, use a distinct folder for the extended pilot.
`-Limit 0` explicitly processes all remaining presets. Presets are shuffled and
alternate between projectM and Butterchurn. `-Engine` and `-PresetId` restrict a pilot.

The default output is `visualiser-analysis/audio-response-catalogue/index.html`.
Reports have a single preset-only paragraph, a table of sound effects, repeat-level
measurements, evidence references and an explicitly separate reference-transfer
section. Raw frames, PCM, condition receipts and logs stay in ignored `_work/`.
Nothing is committed, pushed or published by the analysis command.

## Protocol

Every condition uses a new renderer process, fixed 60-fps simulation, 15-fps
capture, 384-pixel output, two seconds of silent initialisation, and a 12-second
window including the final tail. Audio is supplied at every simulation step, not
only at captured frames. The 1024-stereo-sample history windows overlap at 44.1 kHz
and 60 fps: lowering capture rate does not leave holes in transient input.

Core probes are silence, 80 Hz pulses, 1 kHz pulses, 6 kHz pulses and an explicitly
synthetic rhythmic multiband fixture. Three independent trials give **15 clips and
2,715 output captures per preset**. No neural model is loaded: semantic calls are
zero. This costs more capture/measurement than the older 368-frame preset test,
but does not multiply expensive per-frame model inference.

The three tones share a click-free envelope and linear PCM RMS over the exposure
window. Irregular onset times vary between trials but match between frequencies
within a trial. The extended protocol adds half-amplitude low pulses (-6.02 dB),
a faster low-pulse sequence, sustained low-frequency sound, and broadband bursts.
The fast/sustained tests conserve exposure-window energy, not instantaneous peak
level: do not interpret them as a pure tempo-only or pure-transient perceptual test.
Probe frequencies identify supplied PCM, not the visualiser's internal band bins.
Equal linear RMS is not equal perceived loudness.

An optional supplied musical excerpt is decoded into a separate 44.1 kHz stereo
measurement signal, with silent lead-in and tail. Its original level is retained.
The source checksum and excerpt start are recorded. Artist audio files and their
lyric/cue/source timing are never modified. The test requires at least 8.5 seconds
of audio following the selected offset. It does not separate or identify stems.

## Input isolation

`engine-only` uses source mix **0**, not black pixels injected at 35% opacity.
The existing native bridges still own all preset processing. Optional
`-TextureControls` feeds two fixed achromatic, aperiodic texture fields at 15%.
These help examine image transformations, but all of their measurements and
interpretations are excluded from the main summary by construction.

No palette, ring, stripe, flower, object, preset filename, artist name or reference
caption can become a summary claim. If a preset produces no visible standalone
output, the report says so; it does not silently substitute a reference-fed result.

## Audio attribution

PrismMoves compares each sound with silence on the same clock, preset, input role,
resolution and renderer identity. It subtracts the pre-exposure difference and
requires independent-trial support above both a metric-specific minimum effect
and repeat variation. Short responses are also tested in onset-aligned windows.
Untracked flow, missing files, changed hashes and incomplete conditions are not
zero motion. Frequency, level and envelope contrasts remain separately identified.

Butterchurn uses paired seeds. projectM exposes no deterministic seed API here;
its repeated controls estimate variation instead. These are conservative
engineering screens, **not p-values, calibrated confidence, causal proof on all
GPUs or psychoacoustic/music-category claims**. A detected 80 Hz response does not
establish that a preset reacts specifically to bass instruments or kick drums.
An unresolved response is not proof of insensitivity. No flash-safety, beat-locking,
subjective quality, trail persistence or banding claim is invented.

## Resume and evidence

Rerun the same command to continue; complete conditions validate retained hashes
before reuse. `-RetryFailed` preserves an old partial attempt before trying a fresh
one. `-Reanalyse` reuses capture evidence and writes new analysis while retaining
previous reports under `history/`. Failed native processes have their own logs and
do not manufacture analysis frames. Changed protocol/code/runtime settings require
a new output directory. A run lock prevents concurrent writers to the same output.

Existing behaviour/legacy reports remain untouched. Their continuously injected
inputs and pooled prose cannot be retroactively converted into isolated audio
experiments. They are useful for choosing presets to retest, not for inventing
missing no-feed controls.
