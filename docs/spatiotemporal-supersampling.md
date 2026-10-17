# Source-level spatial and temporal supersampling

This release enables the **maximum supported quality without requiring a preset**:

```json
{
  "render_quality": {
    "enabled": true,
    "spatial_scale": 4,
    "temporal_samples": 8,
    "antialias": 1,
    "antialias_radius": 0.75,
    "antialias_threshold": 0.04,
    "antialias_passes": 3
  }
}
```

To **explicitly reduce** the cost of rendering: `spatial_scale` allows integers 1–4,
`temporal_samples` allows integers 1–8; choose `2`/`4` for a balanced compromise,
or `1`/`1` to disable those two supersampling methods. To retain a pixel-exact
low-quality output, use `"render_quality": {"enabled": false}`; no inherited
high-quality settings apply when this explicit switch is false. The old opt-in
per-element `visual.antialias` remains independently configurable.

## What is supersampled

* **Fonts and text outlines:** Pinned TrueType/OpenType outlines are rasterized
  at four times the output pixel density with the same line breaks as the final
  subtitle box, including karaoke text and layered rich text effects (gradient,
  bevel, emboss, glow, stroke and shadow), then reduced with Lanczos. Rendering
  is cropped to a text box plus a halo to avoid materializing an entire 4K
  canvas for each caption.
* **Procedural vector Rudiment views:** Their source geometry is rendered into
  a temporary four-times-larger viewport, then reduced with Lanczos. Bitmap
  `sprite-*` renderers intentionally retain the original pinned PNG texels, so
  crisp LCD and pixel art assets are never falsely treated as vector geometry.
* **Legacy vector bars:** Retained as vector geometry; the finished-frame AA also
  smooths their edges. Custom Python scene hooks and third-party raster images
  are not automatically converted into high-resolution vector assets.
* **Time:** A temporary internal compositor samples the actual pinned source
  time, beat map and cue/LRC timeline eight times per encoded frame. The eight
  RGB images are composited in *linear-light* colour space before any optional
  Butterchurn/projectM stage. Native procedural DanceRudiments interpolate
  their fractional phase unless explicitly configured not to; recordings keep
  their original semantics. No optical flow or guessed missing music events.

These are deliberately different from the older **temporal history shutter**,
which combines *previous encoded frames* (and may be enabled independently).
When both are enabled, each source frame in the historical shutter is computed
from distinct subframes before the historical blend. At repeat boundaries, the
original shutter still prevents history leaking from a previous excerpt.

Word-indicator motion and particle timing use the same source timestamp ledger;
their authored frame durations are scaled with the internal sampling rate so
cue or lyric effects do not speed up by 8×. Unpinned assets and audio are never
opened to estimate additional motion. Custom `scene(image, ctx)` hooks receive
fractional `source_seconds` but their integer `frame` continues to refer to the
original output-frame ordinal. Frame-index-only custom animation cannot become
smoother without rewriting it to use continuous source time.

## Processing order and QA

For a conventional output frame:

1. Draw text/vector elements in genuine high-resolution geometry; reduce them
   to target pixels; draw pinned raster sprites at their authored resolution.
2. Repeat the composition at distinct subframe source-clock positions and
   integrate the samples in linear light.
3. Optionally apply original-frame temporal history blur (if requested).
4. Run any configured full-frame Butterchurn/projectM feedback **once** per
   encoded frame; these engines are not sampled at 8× subframe FPS.
5. Run final edge-aware raster AA (three passes), then emit QA stills/FFmpeg.

The codec frame rate, audio, source hashes and event timestamps do not change.
The plan/manifest records `render_quality` including its exact sample counts.

**Performance:** the highest setting can consume roughly 8× more compositor
work, plus up to 16× the pixel count for individual vector viewports and masked
text. Rich gradients, image filters, large fonts and temporal-history processing
can be considerably slower; total cost is workload dependent, not a guaranteed
fixed multiplier. Reduce quality explicitly if render time or memory are
unacceptable. High-sample settings are optional controls, but the maximum is
now the default.

## Generated gallery

The CI demonstration at `/supersampling/` renders deterministic synthetic
vector/text movement under eight quality combinations. It displays separate
spatial and temporal comparisons and makes no claims about actual audio data.
Native compositor regression tests also cover true variable-FPS sampling,
backward frame seeking and screenshot-sized quality options.
