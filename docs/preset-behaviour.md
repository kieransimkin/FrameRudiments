# Dense captures, selective semantics, one preset description

Install the coordinated PixelCue 0.9.1.dev1 and PrismMoves 0.2.0.dev1 source
updates/development wheels into `models/.venv-analysis`; retain the existing
render environment and all checkpoints. Add OpenCV there when missing. These
are development builds, not an assertion that public releases already exist.

```powershell
.\scripts\Run-PresetBehaviourAnalysis.ps1 -DescribeExisting
.\scripts\Run-PresetBehaviourAnalysis.ps1 -Limit 2
.\scripts\Run-PresetBehaviourAnalysis.ps1 -ArtworkDirectory 'Z:\My Songs\Covers' -Limit 2
.\scripts\Run-PresetBehaviourAnalysis.ps1 -RetryFailed
```

The first command scans every legacy per-preset report and writes a new timestamped
atlas. It reads existing compressed model receipts rather than paying to rerun
them. Old four-frame studies still report unknown motion. An incomplete old
index cannot hide a newer committed per-preset report.

For new measurements the renderer advances on its original timeline, capturing
15 fps through 3 seconds inclusive. Each of four image families is tested with
programme audio and silence: 368 rasters for one input project. Source and output
hashes bind every observation. The synthetic fixture uses 384-pixel square
output; real authored suites keep their own dimensions/audio. The default demo
has four seconds of synthetic audio, so a longer requested window needs a longer
authored suite rather than silent source looping.

PixelCue streams adjacent rasters for inexpensive motion and image statistics.
PrismMoves then selects at most 24 unique, non-blank semantic images, balancing
conditions and appearance novelty. CLIP/BLIP are default; DINO/OCR/tagging are
not mandatory. Exact duplicates share model output, near-duplicates do not.
There is no requirement to caption a frame before tracking its motion.

One API call loads each chosen semantic model once for the selected batch. It
writes temporal evidence and a provisional description first. Cached complete
semantic receipts reuse exact hashes, checkpoint manifests, prompts, settings
and code identities. The output has separate motion/semantic/analysis states:
'limited' does not mean that the renderer crashed. White/black intervals remain
findings even when excluded from captioning. Static-source controls take
precedence over moving artwork for movement descriptions.

`-CaptureFps`, `-Seconds`, `-RenderEdge`, `-SemanticBudget`, `-Device`, `-Limit`,
`-OutputDirectory` and `-RetryFailed` are explicit. Semantic budget 0 requests
measurements only. Existing matching capture runs are resumed when only semantic
settings change, while separate analysis attempts and the shared semantic cache
avoid overwriting prior evidence. New capture conditions require a new output
directory. At most 512 captures per condition and 32,768 per analysis are accepted;
split larger suites. No private folders are scanned without `-ArtworkDirectory`.

Generated results stay in `visualiser-analysis/behaviour-catalogue/` by default.
The old `full-catalogue/` is unchanged. `_work/` holds raw frames, source paths,
logs and interrupted attempts; curated folders contain a short report, selected
contact sheet, claim references and compressed complete evidence. Review image
rights and raw metadata before committing. No weights or runtime DLLs belong
in the universal Python wheel, and this update does not publish to GitHub/PyPI.

Guardrails: poor/sparse tracking is unknown; still-image spiral motifs do not
prove rotation; one fade is not a pulse train; irrelevant NSFW/booru and generic
'ominous/outer space' tags are not style claims. The current measurements cannot
certify aliasing, banding, feedback trails, musical causality, artistic intent,
quality, flash safety or comfortable motion. GPU-native renderer compatibility
and pretrained-model accuracy still require real-runtime testing.
