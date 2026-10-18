# FrameRudiments

## Before every new workflow

Check the latest stable official release and the actual installed/imported version before starting; do not infer currency from a directory name or a development checkout. Read the current release notes, tagged capability documentation and relevant Potential problems. Use the [DanceFlow release preflight](https://github.com/kieransimkin/DanceFlow/blob/main/docs/workflows/release-preflight.md) and [complete capability references](https://github.com/kieransimkin/DanceFlow/blob/main/docs/capabilities/index.md).

People and agents should consider the full ecosystem, use all capabilities that materially serve the task, compose supported files/APIs, and improve a demonstrated reusable gap in its canonical tool. Record why no worthwhile change is justified when that is the result. Keep independent tool identities, compatibility, source clocks, privacy, model/download costs and publication/deployment permissions intact. Store weights and model-specific caches in the owning project's ignored `models/`.

## Install and run

Python 3.11+ and FFmpeg with libx264/AAC are required. Install the package into
its own virtual environment. Published packages use `python -m pip install framerudiments`;
from a source checkout use `python -m pip install .`:

```sh
python -m pip install .
framerudiments --version
framerudiments demo demo --font /path/to/a/font.ttf
framerudiments plan demo/project.json --output demo-plan.json
framerudiments preflight demo/project.json --output preflight --ffmpeg /path/to/ffmpeg
framerudiments render demo/project.json --output render --ffmpeg /path/to/ffmpeg
```

Use a new output directory each time. `--ffprobe` overrides FFmpeg's sibling
ffprobe. Environment alternatives are `FRAMERUDIMENTS_FFMPEG` and
`FRAMERUDIMENTS_FFPROBE`. Fonts are explicit hashed inputs; no platform-specific
font or FFmpeg location is hardcoded into the engine. The synthetic demo creates
its own tone, sprite, LRC, CUE and four-dimensional recording.

## Capabilities

* Portrait, landscape and square frame-addressed Pillow composition, at native
  constant 60 fps by default. Other frame rates require a recorded reason.
* Sample-addressed source excerpts and exact repetitions; no time-stretch,
  resampling, normalization or remix. The master is never changed. Short
  derivatives default to 120 ms quarter-sine endpoint fades; set
  `audio.fade_seconds: 0` to preserve an approved raw seam. Visualisers and
  Canvas-style projects default to no fades. AAC encoding is lossy.
* One caption/cue ledger for actual raster transitions, JSON, SRT and ASS.
  Retains signed frame error, same-frame ordering, explicit clears, carry-in
  lyrics and repeat boundaries. Accepts UTF-8 LRC with offsets/multiple stamps
  and CD CUE INDEX 01 at 75 subframes/s. Section labels never become lyrics.
* Canonical lyric text comparison, exact input hashes, inert text, bounded text
  wrapping and fit-or-fail behavior. Optional previous/current/next captions
  and exact-text-associated lyric artwork (planes, tablets, signs, etc.).
* Native DanceRudiments catalogue sampling at integer pips; optional explicit
  interpolation of adjacent native samples for legacy compositor parity.
  Preserves the distinction between 64 pips/beat and 16 DanceMoves ticks/beat.
* Saved StemLab beat grids, accepted `stemlab.loops.v1` selections, explicitly
  selected `stemlab.dancerudiments.candidates.v1` matches, and native
  `dancerudiments.recording` files. No model execution or copied DSP.
  Match-window extrapolation needs an explicit choice. N-dimensional
  recordings retain their own source clock and never wrap outside capture.
* Transparent sprites, atlas cycles in musical ticks, cue/time-gated layers,
  position/rotation/scale mappings with selectable axes, same-pose directional
  trails, masks, background contain/cover and N-dimensional signed bars.
  Independent composite motion channels preserve separate bounce/sway/tilt.
  All transformed sprite alpha extents are checked against declared bounds
  on every frame. Custom scene drawing has its own review obligations.
* Modular rudiment views: seeded moving particles and trails, signed bar graphs,
  scrolling multi-dimensional waveform traces, XY paths and calibrated Fourier
  spectrum views with local peak analysis and peak holds. Any view can use
  the existing native motion sources; a trusted Python registry supports more
  renderers without another frame loop or clock. See
  [docs/rudiment-renderers.md](docs/rudiment-renderers.md) and run
  `framerudiments demo demo --rudiments --font /path/to/font.ttf`.
* The existing seeded paper-glider flight model and connected-alpha sprite
  cleaner, plus a general frame-addressed lyric-art entrance/hold/exit helper.
  The flight model is stylized animation; it does not simulate real aerodynamics
  or establish musical synchronization by itself.
* H.264/yuv420p + AAC MP4 with fast start, full decoding, exact decoded frame
  count and every decoded timestamp checked for CFR. Audio length, sample
  rate and channel count are checked against the source. Includes encoded QA
  stills, a poster, phone preview, input/output hashes, commands and logs.

The [spectrum guide](docs/spectrum.md) covers saved StemLab Fourier calibration,
frequency labels, peak controls and per-frame JSONL effect signals. Run
`framerudiments renderers` for the current built-in names. The optional
`demo --spectrum` generator uses StemLab 1.6+; rendering its saved recording
keeps the core package lightweight.

## Project format

Start from `framerudiments demo` and replace its synthetic assets. Every file
reference has `path` and `sha256`; paths are relative to the project JSON unless
absolute. See [docs/project-format.md](docs/project-format.md) for all fields and
[docs/extraction.md](docs/extraction.md) for source mechanisms and boundaries.

```python
from framerudiments import Project, render

project = Project.load("release/project.json")

def scene(image, context):
    # Existing custom drawing consumes frame, source_seconds, output_seconds,
    # beat, lyric and cue. Return a frame-sized RGBA image.
    return image

render(project, "release/new-render", scene=scene, ffmpeg="/path/to/ffmpeg")
```

Pin any assets used by a custom scene with `project.ref({"path": ..., "sha256": ...})`
before rendering. The hook is supplied by trusted Python code, never imported
or executed from a project JSON. Do not reproduce clock, sampling, caption,
encode or verification loops in release adapters.

## Variable-tempo synchronisation and word subtitles

FrameRudiments accepts a checksum-pinned `stemlab.beat-timeline.v1` in
`clock.beat_timeline` for true piecewise BPM/beat phase at sample-frame anchors,
matching StemLab and the opt-in DanceMoves variable-tempo API. Nominal/fixed
BPM, beat grids, and fixed-tempo `beat_alignment` remain supported independently.
Lyrics, cue timing and recorded Rudiments retain their source audio clock.
A separate pinned `word_lyrics` LRC supports enhanced timestamps, per-word
karaoke highlighting, progressive word colour and a distinct `word_captions`
lane; line captions remain available through `lyrics`. Both per-line and
per-word subtitle exports are generated with the same encoded frame ledger.
See [variable-tempo and word LRC guide](docs/variable-bpm-word-subtitles.md)
and the [synthetic public demo](https://kieransimkin.github.io/FrameRudiments/timing/).

## Rich lyric typography and word-following particle sprites

Caption blocks and identity titles optionally accept checksum-pinned `font` overrides and
`effects` for stroked, bevelled, embossed, multi-stop gradient, inner/outer glow, and shadowed
text. Existing plain text remains untouched unless configured. Set `word_indicator` for a
native 1-dimensional DanceRudiment-driven bouncing sprite that tracks the active word of
the full line. Its particle field can be continuous, word/cue-triggered, or hybrid;
trigger windows independently change colour, density, size, glow and burst count.
See [rich lyric reference](docs/rich-lyric-effects.md) and the
[CI-generated demo](https://kieransimkin.github.io/FrameRudiments/rich-lyrics/).

## Particle and smoke emitters

A new deterministic `type: "emitter"` layer provides continuous smoke,
sparks, embers, snow, rain, confetti and cue-triggered bursts, driven by
independent DanceRudiments. Pinned DanceMoves/StemLab `.lrc`, `.cue`, or
versioned `.json` timing files can trigger one-shot explosions; `hybrid`
mode combines these with constant emission. Every parameter in the engine's
numeric style table supports native `style.*` modulation. Pinned transparent
PNG particles are optional, and the existing element- and frame-level post
passes remain available. See [particle and smoke reference](docs/particle-smoke.md)
and the [CI-generated synthetic gallery](https://kieransimkin.github.io/FrameRudiments/particles/).

## Image-backed Rudiment renderers: 52 additional options

FrameRudiments now offers **213 built-in Rudiment renderers**: the existing 161 vector
options remain registered and unchanged, and 52 sprite-rendered spectrum,
spectrogram, waveform and movement alternatives can be selected independently.
The package bundles **17 PNG sprite atlas themes** (1,088 tiles), including 64
transparent image-generated neon motifs and 1,024 procedurally generated LCD,
LED, VFD, crystal, glass, retro-pixel, flame and other tiles. Pinned custom
PNG atlases, animated cell selection and the existing DanceRudiment geometry/
colour modulation and per-element/full-frame post effects are supported.

[Explore vector and sprite comparisons](https://kieransimkin.github.io/FrameRudiments/sprites/)
and see the [full sprite renderer reference](docs/sprite-visualisations.md).
There are also 40 new per-sprite numeric controls (including size, rotation,
colour channels, jitter, glow and animation speed) and validated native-driven
categorical theme, shape and palette choices. The 16 new modulation lab
scenes show these controls at four encoded-frame moments.

The demo images are synthetic, not measured audio.

Enable Settings → Pages → GitHub Actions as the repository publication source.

## Solid 3D meshes and atmospheric rendering

Three additional `spatial-solid-*` viewport modes (torus, sphere and terrain)
provide per-pixel Z-buffered geometry, roughness/metallic direct lighting and
depth-terminated procedural fog. They use the normal StemLab/DanceRudiments
motion paths and can feed existing Butterchurn/projectM compositing chains.
They are CPU rasterisers, not GPU mesh engines.
See the [3D rendering guide](docs/solid-spatial-renderers.md).

Three opt-in `spatial-music-*` modes add music-reactive mesh deformation,
procedural rock/ice materials, erosion, normal/bump effects, and an optional
ModernGL offscreen rasteriser with depth-tested shadows and single-scattering
volumetric lighting. `spatial-music-terrain` can generate new rows from a
checksum-pinned calibrated StemLab Fourier recording's history with
`style.terrain_source: "fft"`. Existing DanceRudiments drivers can modulate
mesh, camera, material, lighting and atmospheric properties. The CPU path
remains deterministic and works without GPU dependencies.
See [music-reactive 3D](docs/music-reactive-3d.md) for integration and limits.

## Layer-selective Butterchurn/projectM feedback

Each composited element can feed the GPU visualisers continuously, only
during a bounded window measured in **64-pip source-clock beats**, or not at
all. Independently, an element can render cleanly over the last stage.
Word captions, lyric sprites, particle emitters, backgrounds and normal
Rudiment layers use the same routing policy. Existing projects remain
unchanged. [Configuration and examples](docs/visualiser-routing.md) ·
[CI comparisons](https://kieransimkin.github.io/FrameRudiments/routing/).

## Per-element and full-frame post-render effects

The new `post_effects` project field supports isolated effects for **each**
background, scene, sprite, legacy bars, Rudiment layer, identity caption or
lyric-art element, and separate passes over the **finished frame**. All 161
renderers and the shared gradients/CRT/colour post effects are supported. Numeric
parameters use native DanceRudiment modulation; palettes, colour-stop sets and
gradient modes can switch between validated alternatives. Independent layer
passes cannot recolour already composited neighbours, while a frame pass can
act on everything. These stages run before the existing optional
Butterchurn/projectM feedback chain. See the [compositor effects reference](docs/scoped-post-effects.md)
and the [public scopes gallery](https://kieransimkin.github.io/FrameRudiments/scopes/).

## Maximum-quality rendering enabled by default

FrameRudiments now redraws pinned text outlines and procedural vector Rudiment
views at **4× linear pixel resolution** before Lanczos reduction. It also
renders **eight different source-clock subframes per output frame**,
averaging them in linear light for true temporal supersampling. A final
three-pass edge-aware AA filter runs after the optional GPU effect chain.
These are the maximum supported preset settings and require no project changes.
Explicitly lower them with `render_quality.spatial_scale` (1–4),
`render_quality.temporal_samples` (1–8), or
`render_quality.enabled: false` for deliberate pixel art or legacy output.
Full-quality rendering can be much slower; read the
[spatiotemporal sampling guide](docs/spatiotemporal-supersampling.md).

## High-quality antialiasing and motion blur

Opt-in edge-aware antialiasing and premultiplied-alpha directional blur now work in Rudiment `visual` settings and independent per-element or whole-frame `post_effects` stages. Both accept native DanceRudiment modulation of all numeric settings, including direction, shutter distance, samples, strength, thresholds, radius and falloff. A separate `post_effects.temporal` shutter recomposes actual observed encoded frames in linear-light RGB, bounded to the current source repeat. It does not invent subframes or optical flow.

The [CI-generated AA and motion-blur comparison gallery](https://kieransimkin.github.io/FrameRudiments/quality/) combines four existing visualiser modes with nine quality-effect configurations, four observed moments each, and machine-readable parameters. See [the quality-effects guide](docs/quality-effects.md).

## Full-frame MilkDrop effects, including lyrics and animated sprites

Use optional ordered `effects` stages with **Butterchurn 2.6.7** and **projectM
4.2-dev pinned to 349b7cc2db312d47cef6be24c6856ced6643a385**. They process the
complete image after captions, lyric artwork and DanceRudiments-driven sprites/
atlases. Original feedback, kaleidoscope and Julia-distortion presets work in
either engine, and stages can be chained. Offline clocks and soundtrack-derived
PCM stay tied to the encoded programme. See [setup, format, provenance, GPU
acceptance and limits](docs/visualisers.md).

```sh
framerudiments visualisers
framerudiments demo ./effect-demo --font /path/to/font.ttf --effect butterchurn --effect projectm
framerudiments render ./effect-demo/project.json --output ./effect-render
```

Optional runtime setup is explicit; the base renderer still works without Node,
a browser or projectM. Feedback is sequential, and GPU output can vary by driver.
Inspect processed/encoded glyphs and safe zones; pre-effect text bounds cannot
prove final legibility.

Version 0.1.4 preserves portrait geometry in pinned projectM legacy presets through a recorded square overscan/crop mapping; the upstream engine remains unmodified. Neutral portrait/landscape/square mapping is tested for both backends.

![Actual synthetic frame before and after both visualisers](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.1.4/docs/media/full-frame-comparison.png)

Actual FrameRudiments 0.1.4 candidate output: timed captions and native moving
sprites/atlas before composition effects (left) and after both real engines
(right). Synthetic oscillator audio and example words; this is a software demo.
[Play the four-second demonstration](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.1.4/docs/media/full-frame-demo.mp4)
and [read its transcript](https://github.com/kieransimkin/FrameRudiments/blob/v0.1.4/docs/media/demo-transcript.md).
Playback is user-controlled; no automatic media playback is required.

![Generated processing-order illustration; not a screenshot](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.1.4/docs/media/effects-pipeline.svg)

Generated vector illustration of the processing order. The comparison above
and linked video are captured renderer output; the diagram is explanatory.

## Explore and profile the preset library

Version 0.3.0 adds a separately installed, checksum-pinned library of **14,680
distinct upstream presets** (1,738 Butterchurn and 12,942 projectM), with source
notices, duplicate aliases and 67 shared textures. Real-engine acceptance is
recorded per preset; available catalogue entries are not claimed as tested.

Use `framerudiments presets-install`, then `framerudiments presets-profile` or
`scripts/profile_visualisers.py` to feed ordinary cover/lyric/audio projects and
three static calibration cards through each engine, replay every feedback frame,
capture chosen intervals and invoke released PrismMoves/PixelCue. Reports keep
input/output, temporal, tune/silence and same-card/different-tune comparisons
separate, with measured response ranges and explicit unknowns.

![Generated static input cards for controlled preset experiments](https://raw.githubusercontent.com/kieransimkin/FrameRudiments/v0.3.0/docs/media/preset-test-cards.png)

Generated calibration illustrations; not preset-output evidence. The maintained
My Songs suite uses Arcadians and The Truth is a Virus with their canonical
masters, covers and timed lyrics; those private assets are not bundled.
See [the complete capture, analysis and synthesis workflow](https://github.com/kieransimkin/FrameRudiments/blob/v0.3.0/docs/preset-profiling.md)
for pinned packs, licences, runtime setup, resume and interpretation limits.

## Visualisation Atlas and public gallery

FrameRudiments now exposes **161 deterministic built-in Rudiment renderers**,
including spectral waterfall, spectrogram heatmap (linear/log/mel/Bark/ERB
**axis remappings**, not newly computed filterbanks), radial FFT-band views,
and oscilloscope-inspired motion traces. All spectrum renderers require pinned,
calibrated StemLab Fourier evidence; motion traces are not raw PCM audio.

[Browse the CI-generated public visualisation gallery](https://kieransimkin.github.io/FrameRudiments/)
(available after the Pages workflow is enabled and deployed), and read the
[comprehensive method catalogue](docs/visualisation-atlas.md). Each PR checks
and generates every mode; main deploys an interactive searchable gallery with
four reproducible **synthetic** frames per mode. It now also publishes
**20 annotated native DanceRudiment colour/geometry modulation scenes**.

New options include continuous/inward/bipolar/segmented circular spectrum bars,
polar time-frequency heatmaps, annular and spiral waveform plots, custom gradient
stops, 16 built-in colour palettes, glow/hue/rotation/scale effects and up to
32 independent source-clocked Rudiment modulators targeting colour *or* any
validated numeric renderer style parameter. All modulation evidence keeps its
pinned/native input identity. See [all 60 new modes, gradient controls and
modulation examples](docs/annular-modulation.md).

The next generation adds **60 additional display families**: spectrum LED matrices,
cityscapes, ridgetrails and half-circle analysers; time-frequency voxel terrain,
hexagonal mosaics and slit scans; and native-value CRT, triggered, braided and
polar oscilloscope modes. Opt-in deterministic image treatments include scanlines,
pixelation, grain, vignette, edge highlights, halftone, chromatic separation,
solarisation and glitches. All these visual controls can be driven by
source-clocked DanceRudiments. See [expanded effects reference](docs/expanded-effects.md).

## Pinned visual-analysis releases

PixelCue **0.9.1** and PrismMoves **0.2.0** are pinned by final release URL,
source commit and wheel SHA-256 in `analysis-releases.json`. Both analysis
launchers install these coordinated wheels into the project-local analysis
environment; neither can silently restore the old development/API pair.
Run `scripts/install_analysis_releases.py --python PATH_TO_ANALYSIS_PYTHON
--with-sequence` to reinstall the pinned pair without downloading model weights.
The native CI job tests the actual released sequence/description APIs with
synthetic frames and no neural inference. OpenCV and other runtime dependencies
remain separately constrained; this is not a whole-environment lock.

## Efficient preset descriptions (new recommended analysis entry point)

Run `./scripts/Run-PresetBehaviourAnalysis.ps1 -Limit 2` after installing the
coordinated PixelCue/PrismMoves update. It captures 15 fps over 3 seconds (46
frames per condition; 368 for the default four inputs and two audio controls),
measures motion **before** semantic inference, and selects at most 24 unique
non-blank images for CLIP and BLIP. Generated fixtures render at 384 x 384
instead of the legacy 160-pixel pilot. The native render/audio clock is unchanged.

One paragraph per preset describes supported movement, appearance and artefacts.
The searchable gallery rebuilds from completed per-preset records at each job,
not from an occasionally checkpointed index. Raw dense frames and attempts stay
in ignored `_work/`; the compact reports and compressed evidence are under
`visualiser-analysis/behaviour-catalogue/`. The old full-catalogue is preserved.

`./scripts/Run-PresetBehaviourAnalysis.ps1 -DescribeExisting` produces a separate
atlas from the saved analysis receipts without rerunning models. Sparse legacy
stills never acquire invented temporal measurements. An authored `-Suite` or an
explicit `-ArtworkDirectory` supplies real material; defaults use synthetic audio.
See [the efficient workflow and its limitations](docs/preset-behaviour.md).

Windows setup also gains checked project-local MinGW runtime bundling. Missing
compiler DLLs come from the recorded, matching compiler installation and are
verified in a fresh process; arbitrary DLL downloads and global PATH changes
are not used. See [projectM local runtime bundle](docs/projectm-local-runtime.md).

FrameRudiments renders Shorts, Canvas-style clips and full lyric visualisers
from one traceable timeline. It is the offline Python renderer in the
[DanceFlow ecosystem](https://kieransimkin.co.uk/danceflow/).

## Full visualiser preset analysis — PrismMoves and PixelCue

Run `./scripts/Run-FullPresetAnalysis.ps1` in Windows PowerShell for a **single
resumable entry point** that installs the optional visual-analysis dependencies,
captures every available Butterchurn/projectM preset, and produces searchable
per-preset metrics, caption and tag hypotheses, summaries and previews.
A quick pilot is `./scripts/Run-FullPresetAnalysis.ps1 -Limit 5`; rerun without
`-Limit` to continue all remaining presets. The complete catalogue includes
explicit partial/failure receipts for presets that cannot render.

The full analysis evaluates all four image families (album/lyric input, colour
bars, grid/rings and text card), music and silence, and frames at 0, 0.5, 1.5
and 3 seconds. PixelCue and PrismMoves examine every captured output by
default, and each preset gets labelled contact sheets plus an evidence-linked
summary across the input conditions and time points. The processing queue
alternates randomly selected projectM and Butterchurn presets, using a fresh
shuffle of unprocessed entries each invocation. `-OrderSeed 20261018` optionally
reproduces the order; receipts are saved under ignored `_work/schedules/`.
Existing five-preset pilots using the older, narrower protocol need an explicit
`-UpgradeCoverage` to archive earlier results before rerunning the fuller test.

All model weights, runtimes and caches live in **project-local `models/`**
(Git ignored). All generated results live under
**`visualiser-analysis/full-catalogue/`** (commit the curated reports when ready;
raw intermediate images in `_work/` stay local and ignored). No private music
is required: a synthetic, checksum-pinned fixture is generated by default.
See the [complete setup, resume, model, verification and Git guide](docs/full-preset-analysis.md).

**0.2.0** accepts source-bound StemLab BPM/first-beat readings through
`clock.beat_alignment`. Procedural motion follows the fractional beat phase
after excerpt/repeat mapping. Audio, lyric/cue timestamps and captured
recordings retain their original source clock. See [beat alignment](docs/beat-alignment.md).

![Actual encoded FrameRudiments 0.2.0 synthetic example with original and aligned movement side by side, source-timed captions and recorded bands](docs/media/beat-alignment.png)

[Play the four-second demonstration](docs/media/beat-alignment.mp4): original
120 BPM clicks start at 0.125 seconds; the right movement uses the -16 pip
reading. Text and recorded bands keep source time. This synthetic example
demonstrates clock composition; it does not establish song-analysis accuracy.
[Encoded-frame verification and hashes](docs/media/beat-alignment-evidence.json).

It consolidates the shared Python/Pillow/FFmpeg mechanisms developed across
the existing Gilt Trip, Blame Putin, Clay, Paper Dreams, Free Danny Tommo,
Silly Sausage Britain and Moderate or Good renderers. Release-specific artwork,
scene layouts and creative decisions belong in project files or Python scene
hooks. Historical render scripts and approved outputs remain preserved.

## Controlled sound-response analysis

The new `scripts/Run-ControlledPresetAnalysis.ps1` tests **zero image feed**, silence,
level-matched low/mid/high probes and rhythmic test music across independent render
trials. Temporal measurements run without neural models. Summaries exclude all
reference-fed conditions; optional texture-transfer results stay separate.
See [controlled audio responses](docs/controlled-audio-response.md) for setup,
protocol limits, supplied-music tests, repeat controls and safe resume.
The pilot uses a separate project-local analysis environment and does not alter the
stable releases pinned below or previous catalogue results.

## Compositing and the authoring workspace

Use named [stem sources and alpha/luminance mattes](docs/stem-sources-and-masks.md), [weighted cross-stem motion](docs/multi-stem-modulation.md), [keyframed matte opacity](docs/keyframed-mattes.md), and [layer animation, polygon clips, displacement and intermediate GPU-stage injection](docs/phase3-compositing.md) within the existing source-clock compositor. These are presentation operations; calibrated spectral displays still require one verified Fourier source.

The [local authoring workspace](docs/authoring-workspace.md) uses released React Timeline Sequence for real transport, editable LRC/word/cue/layer lanes and keyframes, routing graphs, presets, undo/redo and export-only drafts. Its PNG preview never edits canonical files and does not replay sequential feedback. Use encoded render/preflight for final approval.

For one feature-to-configuration map, see the [full compositor workflow](https://github.com/kieransimkin/DanceFlow/blob/main/docs/workflows/framerudiments-complete.md). The registry returned by `framerudiments renderers` is authoritative; older family counts in historical examples are contextual baselines.

## Native rangoli drawing and clean projectM backgrounds

Stack independent `rangoli` Bezier-petal rings over retained native bass/treble
recordings. Smooth floating-point geometry uses the existing high-quality font
and vector rasterizer; the established projectM routing keeps petals and lyrics
clean above an audio-reactive background. No private song assets are bundled.
See [configuration, limits and actual video](docs/rangoli.md).

![Actual synthetic FrameRudiments rangoli and font example](docs/media/rangoli.png)

## Evidence and limits

`plan` is preparation, `preflight` checks all composed frames without encoding,
and `render` produces **technical-QA passed; creative and attended listening
review pending**. No command uploads, schedules or publishes anything.
Automatic checks cannot establish sung-word accuracy, pleasant motion, a
musical loop seam, platform preview acceptance, rights or public eligibility.
Caption boxes are composition constraints, not current official platform
safe-zone verification. Review the exact encoded file and destination preview.

Constant-BPM and saved variable-grid clocks record their authority/status;
an estimate never becomes artist confirmation. Beat grids extrapolate from
their first/last interval outside their covered extent; constrain projects to
supported intervals where that is unsuitable. A ranked match does not prove
whole-song alignment. Recording source/axis provenance is retained; the author
must select the correct master/stem association. No live WordPress timing,
sensor or plugin configuration is changed by offline rendering.

All text in `captions.ass` is a portable timing sidecar with neutral styling;
the raster compositor remains the authority for the approved layout.

## Development

```sh
python -m pip install -e '.[dev]'
ruff check .
pytest
python -m build
```

Set `FRAMERUDIMENTS_TEST_FONT` and `FRAMERUDIMENTS_FFMPEG` to run all integration
tests. Without them, corresponding checks report skipped. Tests cover native
sampling, non-looping recordings, input identity, lyric clears, excerpt/repeat
boundaries, text/alpha overflow and actual encoded portrait/landscape/square
outputs. Use synthetic fixtures; never put songs, personal data or private
analysis outputs into the source distribution.

## Source, contribution and releases

[Canonical repository](https://github.com/kieransimkin/FrameRudiments) ·
[Python package](https://pypi.org/project/framerudiments/) ·
[Issues and improvements](https://github.com/kieransimkin/FrameRudiments/issues)

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing the engine. Improvements
belong in focused pull requests to the canonical repository, with current README,
meaningful tests and reproducible synthetic examples.

The GitHub Actions CI runs the complete render suite on Linux and Windows with
Python 3.11 and 3.13. The release workflow validates the immutable `v<version>` tag,
re-runs tests, builds/checks wheel and source distribution, performs a clean wheel
render and publishes exact checksum-labelled assets. Pushing a new `v*` tag publishes
to GitHub and PyPI automatically after validation; no human reviewer or manual
publication approval is required. The `pypi` environment has no reviewer gate.
An existing release is reused only after its source commit and downloaded checksums
match; assets are never overwritten. Manual dispatch can resume a registry upload
from an existing immutable tag, preserving its exact GitHub bytes. The PyPI job
uses a separately configured trusted publisher; no API token is stored in this project.
A GitHub release and a PyPI listing are separately verified outcomes.
