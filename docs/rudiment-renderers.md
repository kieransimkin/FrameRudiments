# Modular rudiment renderers

Use one motion source with different visual renderers, or layer several views
of the same source alongside sprites and lyrics. There are now **161
built-ins**: the original five, the first 36 spectrum/spectrogram/oscilloscope
views, and 60 new annular/circular variants with shared gradient colour and
native DanceRudiment parameter modulation. See the complete
[annular/gradient/modulation guide](annular-modulation.md), and 60 extra
spectral displays, spectrograms and waveform effects documented in
[expanded effects](expanded-effects.md).
See the [Visualisation Atlas](visualisation-atlas.md) for the comprehensive
catalogue, input-contract limitations and public GitHub Pages test gallery.
List them with:

```sh
framerudiments renderers
framerudiments demo demo --rudiments --font /path/to/font.ttf
framerudiments render demo/project.json --output rendered --ffmpeg /path/to/ffmpeg
```

The demo is synthetic and contains all four views. Its bars and waveform
traces use a four-dimensional recording; the particles and XY path use native
catalogue motion. It runs without StemLab models or private song assets.

## Configure a layer

```json
{
  "id": "floating-dots",
  "type": "rudiment",
  "renderer": "particles",
  "bounds": [50, 400, 1030, 1100],
  "motion": {"kind": "named", "pattern": "circle"},
  "axes": ["x", "y"],
  "history_frames": 90,
  "history_stride": 1,
  "opacity": 0.8,
  "style": {
    "count": 32,
    "radius": 5,
    "amplitude": 0.7,
    "seed": 8,
    "trail": 4,
    "lag": 1,
    "colours": ["#76d4cf", "#edb56d"]
  }
}
```

Bounds are an integer-pixel rectangle inside the output frame. Layers draw in
project order, after the optional scene hook and before identity text/lyrics.
Every renderer receives a transparent viewport of this size and cannot paint
outside it. Built-in strokes, markers and padding fit inside the viewport.
Its actual alpha bounds and rendering configuration appear in the manifest.

Use any existing `motion` route: named, pinned native table, explicit StemLab
match, independent composite channels, or a pinned N-dimensional recording.
The adapters retain native 64-pip sampling, source offsets, match windows and
non-looping capture bounds. Recording renderers receive all original dimensions
by default, rather than the sprite adapter's three-axis projection. Select
`axes` by original dimension name or zero-based index, in the desired order;
named/table/composite/matched sources expose `x`, `y`, `z`. At most 256 axes may
be displayed by one layer. A renderer rejects a view too small for its content.

Optional source-time `start_seconds`, `end_seconds` and `cue_labels` gate a view.
History ends at the current encoded frame, contains only observed frames and
stops at the first inactive sampled frame. No future samples or pre-start poses
are invented. `history_frames` is the number of sampled frames (1–512, default
64); `history_stride` is their encoded-frame spacing (1–120, default 1).
The visible span is `(history_frames - 1) * history_stride / fps` seconds.
Each history sample maps back to its exact source time and native motion clock.
Repeated excerpts therefore retain their explicit source/continuous semantics.
At startup, only available history is shown. Random-access and sequential
composition produce the same built-in pixels.

## Appearance controls

All styles accept `colours` (a nonempty CSS colour-string list), `padding`
(pixels, default 4) and optional `background` (transparent by default).
Unknown built-in style fields fail validation.

| Renderer | Additional style | Behaviour |
| --- | --- | --- |
| `particles` | `count` 1–512 (24), `radius` .5–256 px (3), `amplitude` 0–1 (.7), nonnegative integer `seed` (0), `trail` 1–32 samples (4), `lag` 0–32 samples per particle (1) | Reproducible seeded anchors move with selected dimensions. Dimensional pairs cycle across particles; trailing points and particle delays read bounded recorded history. Earliest available samples are reused when requested lag precedes startup. There is no independent oscillator or integrated particle simulation. |
| `bars` | `orientation`: `vertical` or `horizontal`; `gap` px (2), `negative_colour`, `baseline_colour` | One signed -1..+1 bar per selected dimension around a zero baseline. Works with catalogue and composite sources as well as recordings. |
| `waveform` | `line_width` 1–64 px (2), `layout`: `stacked` or `overlay`, `baseline_colour` | Scrolling time-domain traces of selected rudiment values on a -1..+1 scale. These are motion/recording traces, not decoded PCM audio or a newly computed spectrum. StemLab supplies Fourier/envelope evidence where wanted. |
| `path` | `line_width` 1–64 px (2), `radius` .5–256 px (3) | XY history with a current-position marker, using the first two selected dimensions. Select at least two axes. |

## Add a renderer in trusted Python

Factories receive `(viewport_size, style)` and return an object implementing
`render(RudimentFrame) -> PIL.Image.Image`. The frame contains current
`context`, selected `dimensions`, native `values`, chronological `history`,
and the declared history count/stride. Each history item has its own context
and values. Context carries encoded frame, output/source seconds, beat and
lyric/cue state. Keep rendering a pure function of this data for repeatable
previews, seeks and parallel frame work.

```python
from PIL import Image, ImageDraw
from framerudiments import Project, default_renderers, render

class Dot:
    def __init__(self, size, style):
        self.size = size

    def render(self, frame):
        image = Image.new("RGBA", self.size)
        x = 5 + (frame.values[0] + 1) / 2 * (self.size[0] - 11)
        y = self.size[1] / 2
        ImageDraw.Draw(image).ellipse((x-4, y-4, x+4, y+4), fill="#76d4cf")
        return image

registry = default_renderers()
registry.register("dot", Dot)
# The project's rudiment layer uses renderer: "dot".
project = Project.load("project.json")
render(project, "new-output", renderers=registry, ffmpeg="/path/to/ffmpeg")
```

`preflight(..., renderers=registry)` and `Compositor(..., renderers=registry)`
accept the same registry. A registry is local to its caller; registration does
not modify other renders, and duplicate names fail. `RendererRegistry()` starts
empty if built-ins are unwanted. Output must be viewport-sized RGBA; another
mode/size fails the render. JSON selects only explicitly registered names and
never imports Python modules. Factories and scene hooks are trusted executable
code; review them before use. Pin external plugin assets with `project.ref`
before rendering. Available factory source files are hashed and checked with
other inputs; native source/clock provenance and renderer identity are retained.

The original sprite and recording-only `type: "bars"` layers remain supported.
Use `type: "rudiment", renderer: "bars"` for the new configurable bar renderer.
Keep technical checks, visual review, attended listening and publication states
separate as described in the main README.


## Calibrated Fourier spectrum

The `spectrum` renderer adds actual StemLab band levels, frequency labels,
local-peak analysis, dominant-band reporting and deterministic peak holds.
See [spectrum.md](spectrum.md) for calibration requirements, style controls,
the real Fourier demo and streamed per-frame effect signals. It rejects generic
motion or unrecoverable data rather than inventing frequency/RMS meaning.
