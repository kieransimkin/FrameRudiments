# FrameRudiments

FrameRudiments renders Shorts, Canvas-style clips and full lyric visualisers
from one traceable timeline. It is the offline Python renderer in the
[DanceFlow ecosystem](https://kieransimkin.co.uk/danceflow/).

It consolidates the shared Python/Pillow/FFmpeg mechanisms developed across
the existing Gilt Trip, Blame Putin, Clay, Paper Dreams, Free Danny Tommo,
Silly Sausage Britain and Moderate or Good renderers. Release-specific artwork,
scene layouts and creative decisions belong in project files or Python scene
hooks. Historical render scripts and approved outputs remain preserved.

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

[Canonical repository](https://github.com/kieransimkin/FrameRudiments) Ã‚Â·
[Python package](https://pypi.org/project/framerudiments/) Ã‚Â·
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
