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
its own virtual environment:

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
* The existing seeded paper-glider flight model and connected-alpha sprite
  cleaner, plus a general frame-addressed lyric-art entrance/hold/exit helper.
  The flight model is stylized animation; it does not simulate real aerodynamics
  or establish musical synchronization by itself.
* H.264/yuv420p + AAC MP4 with fast start, full decoding, exact decoded frame
  count and every decoded timestamp checked for CFR. Audio length, sample
  rate and channel count are checked against the source. Includes encoded QA
  stills, a poster, phone preview, input/output hashes, commands and logs.

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
