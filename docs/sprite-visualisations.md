# Sprite-rendered Rudiment visualisations

FrameRudiments supplies **52 image-backed Rudiment renderers alongside all 161 pre-existing vector renderers**. The vector implementations, their names, and their default pixels are not replaced. Select a `sprite-*` renderer in a `type: "rudiment"` layer to opt into bitmap/atlas rendering; use the original renderer identifier to opt into vectors. Multiple vector and sprite layers can be overlaid in normal composition order for hybrid displays.

## Assets — 1,088 bundled tile sprites

`src/framerudiments/sprite_assets/` contains **17 eight-by-eight RGBA sprite atlases**. Each cell is 96×96 pixels; each sheet is 768×768. There are **1,024 procedurally generated tiles** across 16 styles (`lcd`, `lcd-green`, `lcd-amber`, `led`, `vfd`, `arcade`, `neon`, `glass`, `metal`, `crystal`, `pixel`, `hud`, `plasma`, `fire`, `ice`, `mono`) and **64 image-generated tiles** in `generated-neon`. Each shipped sheet has its SHA-256 digest recorded in `manifest.json`; the renderer verifies it when first loaded. A source artwork sheet is included in the downloadable patch package but **is not recreated by the procedural builder**.

Eight row silhouettes comprise horizontal LCD segments, circular LED cells, vertical VFD tubes, gemstone facets, icon/stars, light bursts, chunky pixel blocks and organic flame/petals. The eight columns provide colour variants. `generated-neon` is a set of 64 individual rendered motifs: spectrum bars, rings, waveforms, fire/plasma, gaming glyphs, spheres and gems. The original image-generated source is not an accurate oscilloscope measurement: it is a **visual asset**, and `sprite_renderers.py` positions the cells according to real incoming Rudiment samples.

The 16 procedural atlases can be rebuilt without any external service:

```shell
python scripts/build_sprite_assets.py --output /tmp/sprite_assets
```

The script intentionally does not regenerate the image-generated sheet. The packaged assets are fixed and do **not** download at runtime.

## New renderer catalogue (52)

| Family | Available renderer identifiers |
| --- | --- |
| Spectrum (20) | `sprite-spectrum-lcd`, `-lcd-horizontal`, `-lcd-mirrored`, `-led`, `-led-radial`, `-vfd`, `-retro`, `-neon`, `-crystal`, `-fire`, `-ice`, `-icons`, `-petals`, `-ring`, `-halo`, `-mosaic`, `-city`, `-gauge`, `-spokes`, `-orbits` (prefix `sprite-spectrum` for all suffixed items) |
| Spectrogram (12) | `sprite-spectrogram-lcd`, `-led`, `-pixels`, `-heatmap`, `-icons`, `-neon`, `-crystal`, `-fire`, `-radial`, `-spiral`, `-rain`, `-blocks` (prefix `sprite-spectrogram`) |
| Waveform (12) | `sprite-waveform-lcd`, `-led`, `-dots`, `-icons`, `-neon`, `-ribbon`, `-bars`, `-mirrored`, `-annular`, `-spiral`, `-sparks`, `-crystal` (prefix `sprite-waveform`) |
| Motion (8) | `sprite-motion-path`, `-orbit`, `-cloud`, `-constellation`, `-trails`, `-particles`, `-xy`, `-icon-grid` (prefix `sprite-motion`) |

List all 213 registered names via `framerudiments renderers`, or just the 17 available sprite themes with `framerudiments sprites`.

### Example: calibrated LCD spectrum

```json
{
  "id": "pixel-display",
  "type": "rudiment",
  "renderer": "sprite-spectrum-lcd",
  "bounds": [40, 440, 1040, 860],
  "motion": {
    "kind": "recording",
    "file": {"path": "fourier.json", "sha256": "<actual SHA-256 of saved StemLab recording>"}
  },
  "history_frames": 256,
  "style": {
    "sprite_theme": "lcd-green",
    "sprite_size": 14,
    "segment_count": 18,
    "segment_gap": 0.16,
    "bar_gap": 0.2,
    "off_opacity": 0.08,
    "floor_db": -78,
    "ceiling_db": 0,
    "background": "#071923"
  },
  "visual": {"glow": 2.0, "gradient": {"mode": "vertical", "stops": ["#26c75c", "#e4e986", "#ff563f"]}},
  "modulations": [
    {
      "id": "lcd-segments",
      "motion": {"kind": "named", "pattern": "bounce"},
      "axis": "y",
      "target": "style.segment_count",
      "range": [8, 24]
    },
    {
      "id": "lcd-hue",
      "motion": {"kind": "named", "pattern": "circle"},
      "axis": "x",
      "target": "visual.hue_shift",
      "range": [-60, 60]
    }
  ]
}
```

This uses exactly the same pinned Fourier-band metadata, RMS calibration, dB display clipping, native clock, window bounds and `rudiment-analysis.jsonl` spectral reports as the original vector spectrum renderer. Neither a sprite nor an RGB gradient creates new audio data. In particular, motion-view sprites are **native Rudiment traces**, not PCM audio waveforms, and band maxima are not pitch or true-peak claims.

### Example: hybrid original vector and PNG-tile ring

Two layers may share the same recording and bounds. Place the sprite view after the vector view to overlay individual PNG art atop the vector's accurate frequency bars:

```json
[
  {"id": "accurate-spectrum", "type": "rudiment", "renderer": "spectrum", "bounds": [40, 440, 1040, 860],
   "motion": {"kind": "recording", "file": {"path": "fourier.json", "sha256": "<exact digest>"}},
   "style": {"labels": false}},
  {"id": "sprited-ring", "type": "rudiment", "renderer": "sprite-spectrum-led-radial", "bounds": [40, 440, 1040, 860],
   "motion": {"kind": "recording", "file": {"path": "fourier.json", "sha256": "<exact digest>"}},
   "opacity": 0.75, "style": {"sprite_theme": "generated-neon", "segment_count": 12}}
]
```

## Custom PNG sprite atlases

You can use a custom **checksum-pinned** PNG sprite sheet. Atlas dimensions must divide evenly by the declared column and row counts. There are no untrusted imports, disk searches, or network downloads. A mismatching digest or malformed atlas fails project preparation.

```json
"style": {
  "sprite_asset": {
    "path": "sprites/my-tiles.png",
    "sha256": "<sha256 of my-tiles.png>"
  },
  "sprite_columns": 8,
  "sprite_rows": 8,
  "sprite_index": 4,
  "animation_frames": 6,
  "animation_stride": 4,
  "pixelated": false,
  "sprite_size": 20
}
```

`animation_frames` chooses a bounded sequence of adjacent atlas cells beginning at `sprite_index`, according to the encoded frame number. It is deterministic when rendering individual frames out of order. All non-custom assets are bundled with the package and individually pinned. `sprite_asset` references are added to the project hash manifest and verified before and after rendering.

## Appearance controls

Common `colours`, `background`, `padding` and Rudiment layer `visual`/`modulations` continue to work. Sprite-specific style fields are:

| Field | Meaning / bounds |
| --- | --- |
| `sprite_theme` | One of 17 built-in themes; default selected by renderer name |
| `sprite_asset`, `sprite_columns`, `sprite_rows` | Optional pinned external sheet and 1..32 grid dimensions |
| `shape` | `segment`, `dot`, `tube`, `crystal`, `icon`, `spark`, `block` or `flame` |
| `sprite_index` | 0-based tile; required for a specific tile in a custom sheet |
| `sprite_size`, `sprite_opacity` | Tile size 2..256; alpha 0..1 |
| `sprite_colour`, `tint_strength` | `original`, `tint` or `level`; tint strength 0..1 |
| `segment_count`, `segment_gap`, `off_opacity`, `bar_gap` | LCD segments 2..64; individual gaps 0..0.8/0..0.9; off-state opacity 0..1 |
| `orientation`, `mirror` | Horizontal/vertical meter orientation; independent reflected copy |
| `inner_radius`, `outer_radius`, `start_angle`, `sweep_angle` | Radial geometry, validated 0..1, angles in degrees |
| `history_limit`, `bin_stride`, `sample_stride`, `spacing` | Visible observed samples, bands and stamp pitch; no interpolated/future data |
| `threshold`, `floor_db`, `ceiling_db` | Display-only visibility and clipping of calibrated dBFS |
| `animation_frames`, `animation_stride`, `pixelated` | Deterministic atlas frame selection and nearest-neighbour scaling |
| `axis`, `rings`, `twists`, `seed`, `max_stamps` | Native axis and bounded geometric controls; stamp guard 1..25,000 |

Every numeric control above **and all 40 additional per-cell controls below** can be driven by one or more independent native DanceRudiments using the existing `modulations` array. A control targets `style.FIELD`; existing `visual.*` grading/post effects can be modulated too. There is no replacement Python movement engine and no unpinned asset download.

### Native-controlled sprite animation and appearances

These options change **each bitmap stamp before compositing**, rather than rotating or colour-grading the entire Rudiment viewport. They work together with the existing source-time motion clock, seeded placement, and `post_effects` on individual composited elements or the whole frame.

| Family | Additional numeric style controls and ranges |
| --- | --- |
| Size & aspect | `sprite_scale`, `sprite_scale_x`, `sprite_scale_y` (.05–8); base `sprite_size` still controls nominal pixel size |
| Position & pivot | `sprite_offset_x/y` (-1024..1024 px), `sprite_anchor_x/y` (-1..1), `sprite_spread` (0..4) |
| Orientation & skew | `sprite_rotation` (-3600..3600°), `sprite_spin` (-90..90° **per encoded frame**), `sprite_layout_rotation` (-360..360°), `sprite_shear_x/y` (-.8..+.8) |
| Controlled variation | `sprite_jitter` (0..48px), `sprite_angle_jitter` (0..180°), `sprite_visible_fraction` (0..1), `sprite_palette_shift` (-64..64 cell colours) |
| Tile animation | `sprite_animation_rate` (0..16×), `sprite_animation_phase` (-10000..10000 frames), plus existing `animation_frames`, `animation_stride`, `sprite_index` |
| Per-cell colour | `sprite_hue_shift` (-3600..3600°), `sprite_saturation`, `sprite_brightness`, `sprite_contrast` (0..4), `sprite_gamma` (.1..5), `sprite_red/green/blue` (0..4), `sprite_temperature` (-1..1) |
| Pixel treatment | `sprite_posterize` (0..16 levels), `sprite_pixel_size` (1..16 px), `sprite_scanlines`, `sprite_grain` (0..1), `sprite_sharpen` (0..4) |
| Light & shadow | `sprite_glow` (0..20px), `sprite_blur` (0..16px), `sprite_outline` (0..10px), `sprite_shadow` (0..20px), `sprite_shadow_x/y` (-64..64px), `sprite_shadow_opacity` (0..1) |

Also modulate all useful existing numeric settings including LCD segment count/gaps, VFD bar gaps, inactive-segment alpha, circle start/sweep angles and radii, twists, selected native source axes, history/sample/bin strides, threshold, animation frame count/speed, and stamps limit. Integers such as `segment_count`, `sprite_pixel_size`, `sprite_outline`, and `sprite_palette_shift` are rounded before validation. Sprite cell processing is cached and clipped to the viewport; effective tile dimensions above 1024px fail closed.

**Discrete choices:** the sprite layer's `modulations` additionally accepts `choices: [...]` for `style.sprite_theme`, `style.shape`, `style.sprite_colour`, `style.sprite_blend`, `style.orientation`, `style.mirror`, `style.pixelated`, `style.sprite_tint_colour`, `style.sprite_outline_colour`, `visual.palette` and all validated `visual.gradient.*` fields. No numeric interpolation of strings is attempted. `sprite_blend` accepts `normal`, `add`, `screen`, `multiply`, `lighten`, `difference`; outline/tint colours are CSS colour strings. Every candidate built-in theme in a native-driven choice is **checksum-pinned at project preparation**, not silently introduced during rendering. A project's `sprite_asset` (external pinned PNG) cannot be switched dynamically; instead modulate `sprite_index`, shape, colour and the cell animation controls within the pinned atlas.

#### Example: seven independent modulation destinations

```json
{
  "id": "native-sprite-swarm",
  "type": "rudiment",
  "renderer": "sprite-motion-constellation",
  "bounds": [30, 310, 1050, 1300],
  "motion": {"kind": "named", "pattern": "circle"},
  "style": {"sprite_theme": "lcd", "sprite_size": 14, "sprite_colour": "original"},
  "modulations": [
    {"id": "rotate", "motion": {"kind": "named", "pattern": "circle"}, "axis": "x", "target": "style.sprite_rotation", "range": [-180, 180]},
    {"id": "size", "motion": {"kind": "named", "pattern": "bounce"}, "axis": "y", "target": "style.sprite_scale", "range": [0.5, 2.0]},
    {"id": "blue", "motion": {"kind": "named", "pattern": "circle"}, "axis": "z", "target": "style.sprite_blue", "range": [0.4, 1.8]},
    {"id": "warm", "motion": {"kind": "named", "pattern": "bounce"}, "axis": "x", "target": "style.sprite_temperature", "range": [-0.8, 0.8]},
    {"id": "glow", "motion": {"kind": "named", "pattern": "bounce"}, "axis": "z", "target": "style.sprite_glow", "range": [0, 7]},
    {"id": "atlas", "motion": {"kind": "named", "pattern": "circle"}, "axis": "y", "target": "style.sprite_theme", "choices": ["lcd", "vfd", "generated-neon"]},
    {"id": "glyph", "motion": {"kind": "named", "pattern": "bounce"}, "axis": "z", "target": "style.shape", "choices": ["segment", "dot", "crystal", "flame"]}
  ]
}
```

All `modulations` are evaluated from the driver's independent native DanceRudiment source on the current source/continuous beat clock. Past-frame smoothing, lag, quantisation, transfer curves and bounded combinations continue to work for numeric parameters. Categorical sources use the same native clock but choose one validated alternative per frame. Normal rendering uses the exact same saved calibration/motion values as the vector layer.

### Sprite modulation gallery

The generated `/sprites/` GitHub Pages site adds **16 documented native-modulated appearance examples**, each shown at **four exact encoded frames** (64 images). These cover rotation, per-cell RGB channels and hue, aspect/position jitter, glow and shadow, CRT effects, LCD segmentation, animation phase/rate, opacity/density, and theme/glyph/blend/palette choice. They accompany the original 136 themed images and vector-vs-sprite comparisons.


## Performance, reproducibility and CI

Tile scaling, limited-angle rotation and alpha variations are cached per renderer. A hard stamp ceiling avoids accidental million-sprite workloads. The output clips sprite halos to the layer viewport, and the renderer never reads unobserved future history. Custom atlases are validated and hashed; built-in atlas hashes are checked at load. All renderers return viewport-sized RGBA, so the existing per-element post-effects, full-frame effects, opacity, bounds evidence, and FFmpeg encoding remain compatible.

The GitHub Pages workflow now builds an extra `/sprites/` gallery with **17 themes × four display families × two timeline samples**, **16 native DanceRudiment modulation scenes × four moments**, side-by-side comparison of original vectors and new sprite views, the full palette sheet set and a JSON asset manifest. The main 213-renderer catalogue continues to show the existing vector views alongside sprite views. All example data is synthetic; no private artwork or audio is published.

```shell
python -m pytest -q tests/test_sprite_renderers.py
python scripts/generate_visualisation_gallery.py --output _site
python scripts/generate_sprite_gallery.py --output _site
```
