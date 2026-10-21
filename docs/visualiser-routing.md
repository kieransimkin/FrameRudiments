# Layer-selective Butterchurn / projectM feedback routing

Every project may optionally configure **which isolated composited elements are
injected into the GPU visualiser's source texture** and which original elements
are drawn cleanly **after the last visualiser stage**. The default for projects
without `visualiser_routing` is the existing, unchanged full-frame GPU input.

The feature works with the existing ordered `effects` list containing
Butterchurn, projectM or both. Preset selection, stereo PCM, source audio,
frame time, temporal supersampling and the GPU feedback lifecycle are not
replaced. Each stage still processes the previous stage's result. Routing
selects the input to the **first** stage; material already processed by an
earlier stage remains part of the next stage's image.

## Example

```json
{
  "effects": [
    {"id": "web", "engine": "butterchurn", "preset": "feedback", "input_mix": 0.7},
    {"id": "native", "engine": "projectm", "preset": "julia", "input_mix": 0.7}
  ],
  "visualiser_routing": {
    "default": {"feed": "continuous", "overlay": false},
    "elements": {
      "layer:background-image": {
        "feed": "continuous", "overlay": false
      },
      "layer:spectrogram": {
        "feed": {"mode": "first_pips", "duration_pips": 64},
        "overlay": false,
        "retrigger": "reappearance"
      },
      "layer:dancer": {
        "feed": {"mode": "first_pips", "duration_pips": 32},
        "overlay": true
      },
      "caption": {"feed": "never", "overlay": true},
      "word-indicator": {"feed": "never", "overlay": true}
    }
  }
}
```

`layer:<id>` refers to a normal sprite, legacy bar, Rudiment renderer or
particle emitter in `layers`. Other supported element names are `background`,
`scene` when a scene hook is supplied, `identity:0`, `identity:1`, etc.,
`caption`, `word-caption`, `word-indicator`, and `lyric-art:<index>`.
The existence of every named element is checked during project preparation;
misspellings are errors rather than silent omissions.

### Feed modes

| Mode | What it means |
| --- | --- |
| `continuous` | Inject this element on every output frame where it has visible alpha pixels |
| `first_pips` | Inject from the first visible frame until `duration_pips` source-clock pips have elapsed, using the half-open interval `[appearance_pip, appearance_pip + duration_pips)` |
| `last_pips` | With `retrigger: lyric`, inject the visible lyric element for the final `duration_pips` before the next effective lyric state becomes blank. A same-frame clear followed by a new nonblank line is an immediate handoff and does not trigger this window. |
| `never` | No pixels from this element enter the first GPU stage; it can still be overlaid |

`duration_pips` must be a finite number between 0 and 65,536. One beat is
64 pips (DanceMoves' 16 ticks). This uses `FrameContext.beat`, including the
sample-bound **variable-BPM** integration from StemLab and the continuous
beat count over repeated audio excerpts. Therefore a 64-pip window may be
shorter or longer than half a second as the music accelerates or slows.
A zero-pip window never injects the element.

`last_pips` is designed for lyric tails that should enter feedback only before
a genuine silent display gap. It requires `retrigger: lyric`, an active nonblank
lyric, and a known next effective lyric event. The route report records
`remaining_pips` and `lyric_gap_exit` so the decision is auditable. With
`overlay: true`, the readable original remains visible while its copy enters the
feedback pipeline; at the blank event both disappear from the clean layer.

`retrigger` controls how the window starts:

- `reappearance` (default): reset the window when an element becomes visible
  after a frame with no visible pixels.
- `once`: only use the first appearance in the entire rendered programme.
- `lyric` or `cue`: additionally restart when the currently active source
  lyric/cue event changes (useful for constantly visible caption panels).

A source is considered visible when its isolated, post-element-effects RGBA
surface has at least one nontransparent pixel. Appearance is not inferred
from its mere existence in project JSON. The ordered feedback renderer already
requires frames in sequence from zero, so its appearance bookkeeping is
causal and repeatable. To seek into a feedback effect, recreate its process
and replay earlier output frames. Arbitrary random-access GPU feedback isn't
claimed.

### Final overlay

`overlay: true` draws the original composited element after the **last**
Butterchurn/projectM stage. This is independent from `feed`: an element can
appear both inside GPU feedback and cleanly on top, only inside feedback, or
only as a clean overlay. Overlays follow the original composition order, so
captions and word indicators remain above lower-level layers. Unspecified
elements default to `feed: continuous, overlay: false` so no old project loses
content when only one layer opts into routing.

Individual `post_effects.elements` and `post_effects.all_elements` run before
selection. `post_effects.frame` still applies to the normal full composition;
selectively injected pixels and clean overlays bypass that *whole-frame*
post-effect. Apply an effect at element scope when it must be baked into a
routed input or overlaid element. The final **render_quality** AA pass remains
after the last GPU stage and clean overlays, so its default quality is retained.

The renderer captures each element as its own transparent surface, including
high-fps subframes when default temporal supersampling is active. Isolated
surfaces are averaged using premultiplied alpha at output-frame resolution.
Custom `scene(image, context)` hooks participating in routing must work on
transparent RGBA and should not depend on opaque background pixels; they are
called once per **encoded** frame to preserve their existing stateful hook
contract.

Both pinned GPU host bridges respect **per-pixel source alpha**. Sending an
empty transparent input therefore does not paint black into the feedback
texture, while a legacy opaque composed frame still behaves as before.
The `input_mix` remains the relative source injection strength; it does not
change the feed window.

## Evidence and testing

`visualiser_routing` is emitted in `plan.json` and the composition manifest.
The per-frame `rudiment-analysis.jsonl` records selected feeds, overlays,
appearance pip and elapsed pip values. Route decisions cannot rewrite cue,
source-audio, lyric or native DanceRudiment metadata.

The [CI-generated routing gallery](https://kieransimkin.github.io/FrameRudiments/routing/)
includes 12 configurations sampled at five nonuniform-pip moments: continuous,
16/32-pip gated inputs, feed-only, overlay-only, cue layers, reappearance,
clean lyric overlays and a two-engine chain. The gallery's feedback is
**explicitly simulated** for portability: it does **not** claim to show actual
Butterchurn/projectM shader pixels. Full native engine acceptance still runs
separately in the repository's GPU CI job.

The new tests check exact pip cutoffs, on/off alpha handling, duration across
variable tempo, overlay order, replay, unknown/malformed policy rejection and
both stages' input order. Run:

```sh
python -m pytest -q tests/test_visualiser_routing.py
ruff check .
python scripts/generate_visualiser_routing_gallery.py --output _site
```

A routing frame renders isolated surfaces in addition to ordinary output;
4x spatial and 8x temporal supersampling remain deliberately expensive.
For high-resolution productions with many layers, measure working memory
before batch rendering. Nothing silently lowers your requested quality.
