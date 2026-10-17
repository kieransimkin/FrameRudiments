"""Build the public vector-vs-sprite and atlas-theme gallery from synthetic test data."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from framerudiments.rudiment_renderers import default_renderers
from framerudiments.sprite_catalogue import SPRITE_SPECS
from framerudiments.sprite_renderers import SPRITE_DIRECTORY, SpriteModulations
from generate_visualisation_gallery import fixture

THEME_MODES = (
    ("sprite-spectrum-lcd", "spectrum", "LCD spectrum"),
    ("sprite-spectrum-led-radial", "spectrum", "LED ring"),
    ("sprite-spectrogram-pixels", "spectrogram", "Sprite spectrogram"),
    ("sprite-waveform-icons", "motion", "Waveform icons"),
)
MOMENTS = (45, 105)
MODULATION_MOMENTS = (24, 55, 86, 117)


def signal(name, target, lower, upper, *, pattern="circle", axis="x"):
    return dict(id=name, target=target, range=[lower, upper],
                motion=dict(kind="named", pattern=pattern), axis=axis)


def choices(name, target, values, *, pattern="bounce", axis="y"):
    return dict(id=name, target=target, choices=list(values),
                motion=dict(kind="named", pattern=pattern), axis=axis)


MODULATION_LAB = (
    ("spin-and-size", "Glyph rotation and size", "sprite-spectrum-lcd", "spectrum", "lcd",
     (signal("spin", "style.sprite_rotation", -180, 180),
      signal("scale", "style.sprite_scale", .5, 2, pattern="bounce"))),
    ("colour-temperature", "RGB balance and temperature", "sprite-spectrum-crystal", "spectrum", "crystal",
     (signal("warmth", "style.sprite_temperature", -.85, .85),
      signal("blue", "style.sprite_blue", .4, 1.8, pattern="bounce"))),
    ("hue-and-contrast", "Hue, contrast and saturation", "sprite-waveform-neon", "motion", "neon",
     (signal("hue", "style.sprite_hue_shift", -160, 160),
      signal("contrast", "style.sprite_contrast", .5, 2, pattern="bounce"),
      signal("sat", "style.sprite_saturation", .3, 1.9, axis="z"))),
    ("glow-and-shadow", "Independent glow and shadow", "sprite-waveform-crystal", "motion", "crystal",
     (signal("glow", "style.sprite_glow", 0, 8),
      signal("shadow", "style.sprite_shadow", 0, 5, pattern="bounce"))),
    ("jitter-and-spread", "Positions, jitter and spread", "sprite-motion-cloud", "motion", "plasma",
     (signal("jitter", "style.sprite_jitter", 0, 18),
      signal("spread", "style.sprite_spread", .35, 1.8, pattern="bounce"))),
    ("density-and-opacity", "Particle density and transparency", "sprite-motion-particles", "motion", "generated-neon",
     (signal("density", "style.sprite_visible_fraction", .3, 1),
      signal("alpha", "style.sprite_opacity", .25, 1, pattern="bounce"))),
    ("animated-atlas", "Sprite animation speed and phase", "sprite-spectrum-icons", "spectrum", "generated-neon",
     (signal("rate", "style.sprite_animation_rate", 0, 6),
      signal("phase", "style.sprite_animation_phase", -12, 24, pattern="bounce"))),
    ("lcd-bar-gaps", "LCD layout and gap sizes", "sprite-spectrum-lcd", "spectrum", "lcd-green",
     (signal("segments", "style.segment_count", 6, 25),
      signal("gap", "style.segment_gap", .02, .65, pattern="bounce"))),
    ("radial-modulation", "Spinning rings and radius", "sprite-spectrum-led-radial", "spectrum", "led",
     (signal("angle", "style.start_angle", -180, 180),
      signal("radius", "style.inner_radius", .12, .45, pattern="bounce"))),
    ("sprite-choice", "Switching atlas themes and glyphs", "sprite-waveform-icons", "motion", "lcd",
     (choices("theme", "style.sprite_theme", ("lcd", "vfd", "generated-neon")),
      choices("shape", "style.shape", ("dot", "icon", "crystal", "flame"), pattern="circle"))),
    ("blend-choice", "Switching sprite blend modes", "sprite-spectrum-led", "spectrum", "led",
     (choices("blend", "style.sprite_blend", ("normal", "screen", "add", "multiply")),
      signal("brightness", "style.sprite_brightness", .6, 1.7, pattern="circle"))),
    ("palette-choice", "Native-switched colour palettes", "sprite-waveform-led", "motion", "led",
     (choices("palette", "visual.palette", ("aurora", "fire", "ice", "synthwave")),
      choices("tint", "style.sprite_colour", ("original", "tint", "level"), pattern="circle"))),
    ("pixel-art", "Pixel size and edge outlines", "sprite-spectrogram-pixels", "spectrogram", "pixel",
     (signal("pixel", "style.sprite_pixel_size", 1, 7),
      signal("outline", "style.sprite_outline", 0, 4, pattern="bounce"))),
    ("geometric-shear", "Per-glyph shearing and offset", "sprite-waveform-dots", "motion", "arcade",
     (signal("shear", "style.sprite_shear_x", -.6, .6),
      signal("offset", "style.sprite_offset_y", -38, 38, pattern="bounce"))),
    ("sprite-grain", "Per-cell grain and phosphor lines", "sprite-spectrogram-neon", "spectrogram", "neon",
     (signal("grain", "style.sprite_grain", 0, .8),
      signal("lines", "style.sprite_scanlines", 0, .8, pattern="bounce"))),
    ("sprite-ink", "Tint colour and additive blending", "sprite-waveform-sparks", "motion", "generated-neon",
     (choices("colour", "style.sprite_tint_colour", ("#00ffff", "#ff7040", "#f34cf0", "#b3ffa0")),
      signal("opacity", "style.sprite_opacity", .45, 1, pattern="circle"))),
)


COMPARISONS = (
    ("spectrum", "sprite-spectrum-lcd", "Spectrum bars"),
    ("spectrogram-linear", "sprite-spectrogram-pixels", "Time-frequency history"),
    ("waveform", "sprite-waveform-dots", "Rudiment waveform"),
)


def build_site(output):
    output = Path(output)
    root = output / "sprites"
    (root / "themes").mkdir(parents=True, exist_ok=False)
    (root / "comparison").mkdir()
    (root / "atlases").mkdir()
    asset_manifest = json.loads((SPRITE_DIRECTORY / "manifest.json").read_text(encoding="utf-8"))
    themes = sorted(asset_manifest["themes"])
    for theme, meta in asset_manifest["themes"].items():
        shutil.copy2(SPRITE_DIRECTORY / meta["file"], root / "atlases" / meta["file"])
    shutil.copy2(SPRITE_DIRECTORY / "manifest.json", root / "atlases" / "manifest.json")
    registry = default_renderers()
    frames = []
    for theme in themes:
        folder = root / "themes" / theme
        folder.mkdir()
        for label, (mode, family, description) in enumerate(THEME_MODES):
            view, _ = registry.create(mode, (480, 270), {
                "background": "#0d1930", "sprite_theme": theme, "sprite_size": 14,
                "segment_count": 16, "history_limit": 48, "bin_stride": 2,
            })
            for step, moment in enumerate(MOMENTS):
                view.render(fixture(moment, spectral=family != "motion")).save(folder / f"{label}-{step}.png")
            frames.append(dict(name=theme, mode=mode, label=description, image=f"themes/{theme}/{label}-0.png"))

    comparisons = []
    for index, (vector, sprite, caption) in enumerate(COMPARISONS):
        for label, name in (("vector", vector), ("sprite", sprite)):
            style = {"background": "#101b2e"}
            if name == "spectrum":
                style["labels"] = False
            renderer, _ = registry.create(name, (480, 270), style)
            renderer.render(fixture(95, spectral=not name.startswith(("waveform", "sprite-waveform")))).save(
                root / "comparison" / f"{index}-{label}.png")
        comparisons.append(dict(vector=vector, sprite=sprite, caption=caption))

    # Every modulation preset is rendered using actual native DanceRudiment
    # drivers. The fixture is explicitly synthetic visual-display test data.
    modulation_rows = []
    for name, label, mode, family, theme, drivers in MODULATION_LAB:
        subdir = root / "modulation" / name
        subdir.mkdir(parents=True)
        project = SimpleNamespace(base=Path("."), inputs=[],
                                  clock=SimpleNamespace(beat_at=lambda t: t * 2))
        style = dict(sprite_theme=theme, sprite_size=15, background="#10213a",
                     animation_frames=6, segment_count=14)
        visual = {}
        def context_at(n):
            return SimpleNamespace(frame=n, output_seconds=n / 60,
                                   source_seconds=n / 60, beat=n / 30, lyric=None, cue=None)
        mods = SpriteModulations(project, list(drivers), context_at, base_style=style, base_visual=visual)
        project.inputs.extend(mods.files)
        samples = []
        for moment_index, moment in enumerate(MODULATION_MOMENTS):
            dynamic_style, dynamic_visual, report = mods.apply(context_at(moment), style, visual)
            view, _ = registry.create(mode, (480, 270), dynamic_style)
            view.prepare(project)
            rendered = view.render(fixture(moment, spectral=family != "motion"))
            if dynamic_visual:
                from framerudiments.visual_style import VisualStyle
                rendered = VisualStyle(dynamic_visual).apply(rendered)
            rendered.save(subdir / f"{moment_index}.png")
            samples.append(dict(frame=moment, modulations=report))
        modulation_rows.append(dict(name=name, label=label, renderer=mode, family=family,
                                    theme=theme, native_drivers=[row["motion"]["pattern"] for row in drivers],
                                    samples=samples, style=style, controls=list(drivers)))

    result = dict(sprite_renderer_count=len(SPRITE_SPECS), themes=len(themes), theme_names=themes,
                  images=len(frames)*len(MOMENTS), moments=MOMENTS, comparisons=comparisons,
                  modulation_scenes=modulation_rows, modulation_moments=MODULATION_MOMENTS,
                  modulation_images=len(modulation_rows)*len(MODULATION_MOMENTS),
                  procedural_sprite_cells=16*64, generated_sprite_cells=64,
                  source="public synthetic calibration and native Rudiment fixtures; not song measurements")
    (root / "catalogue.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    cards = "\n".join(
        f'<article class="card" data-theme="{html.escape(theme)}"><img loading="lazy" '
        f'data-path="themes/{html.escape(theme)}" data-i="{k}" src="themes/{html.escape(theme)}/{k}-0.png" '
        f'alt="{html.escape(desc)} — {html.escape(theme)}"><h3>{html.escape(desc)}</h3>'
        f'<p>{html.escape(theme)}</p></article>'
        for theme in themes for k, (_, _, desc) in enumerate(THEME_MODES)
    )
    compare = "\n".join(
        f'<section class="pair"><h3>{html.escape(item["caption"])}</h3>'
        f'<div><figure><img src="comparison/{i}-vector.png" alt="Vector rendering"><figcaption>Existing vector</figcaption></figure>'
        f'<figure><img src="comparison/{i}-sprite.png" alt="Sprite rendering"><figcaption>New sprite mode</figcaption></figure></div></section>'
        for i, item in enumerate(comparisons)
    )
    mod_cards = "\n".join(
        f'<article class="card modcard"><img loading="lazy" data-mode="modulation/{html.escape(item["name"])}" '
        f'src="modulation/{html.escape(item["name"])}/0.png" alt="{html.escape(item["label"])}">'
        f'<h3>{html.escape(item["label"])}</h3><p>{html.escape(item["renderer"])}</p></article>'
        for item in modulation_rows
    )
    options = "".join(f'<option value="{html.escape(theme)}">{html.escape(theme)}</option>' for theme in themes)
    site = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FrameRudiments · Sprite Visualisation Library</title><style>
:root{background:#091326;color:#effaff;color-scheme:dark;font-family:system-ui,-apple-system,Segoe UI,sans-serif}
*{box-sizing:border-box}body{margin:0}header,main,footer{max-width:1340px;margin:auto;padding:26px}
header{padding-top:52px}a{color:#82e7e9}h1{font-size:clamp(2rem,5vw,4.6rem);letter-spacing:-.04em;margin:15px 0}
.eyebrow{font-weight:800;color:#6bf0e2;letter-spacing:.12em;font-size:.78rem}.lede{max-width:820px;color:#bacbdb;line-height:1.6}
.top{position:sticky;top:0;z-index:5;background:#0d1930e8;backdrop-filter:blur(13px);padding:15px 26px;display:flex;gap:20px;flex-wrap:wrap;align-items:center;border-block:1px solid #2b4867}
.top select,.top input{accent-color:#55dfd5;background:#142640;color:white;border:1px solid #3b6383;border-radius:8px;padding:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:15px}.card{border:1px solid #35506c;background:#152743;border-radius:10px;overflow:hidden}
.card img,.pair img{display:block;width:100%;aspect-ratio:16/9;object-fit:contain;background:#061020}.card h3{margin:12px 13px 0;font-size:1.04rem}.card p{margin:3px 13px 14px;color:#90b1c7;font-size:.8rem}
.pair>div{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}.pair img{border:1px solid #385772;border-radius:8px}.pair{margin:18px 0 28px}.pair figcaption{padding:8px;color:#9bc8d7}.pair figure{margin:0}
h2{margin-top:38px}.note{border-left:3px solid #edb56d;padding:10px 17px;color:#d4dfea}
</style></head><body><header><p class="eyebrow">FRAME RUDIMENTS / IMAGE-GENERATED + PIXEL-PERFECT ASSETS</p>
<h1>Sprite Visualisation Atlas</h1><p class="lede">The original vector renderer catalogue remains available and unchanged. The sprite system adds 52 image-stamped alternatives, 17 selectable PNG atlas themes, 64 image-generated motifs and 1,024 procedural sprite cells; all use the same native source clock and frame-addressed Rudiment data.</p>
<p class="note">Demonstrations use deterministic synthetic test signals, not measured musical performances. Spectrum and spectrogram modes in real projects require pinned, calibrated StemLab Fourier recordings.</p>
<p><a href="../index.html">← Main visualisation atlas</a> · <a href="atlases/manifest.json">Atlas manifest &amp; checksums</a> · <a href="catalogue.json">Machine-readable gallery</a></p></header>
<div class="top"><label>Theme <select id="theme"><option value="all">All 17 themes</option>''' + options + '''</select></label>
<label>Moment <input id="moment" type="range" min="0" max="3" value="0" step="1"></label><output id="momentText">1 / 4</output></div>
<main><h2>Vector versus sprite</h2>''' + compare + '''<h2>Native DanceRudiment modulation lab</h2><p>16 independent modulation scenes; every slider position is source-clocked and repeatable.</p><div class="grid">''' + mod_cards + '''</div><h2>Theme and shape combinations</h2><div class="grid" id="grid">''' + cards + '''</div></main>
<footer>All packaged sprite sheets are included with their SHA-256 digests and no external runtime downloads are required. User-supplied atlases are permitted only as pinned project assets.</footer>
<script>const chooser=document.getElementById('theme'),range=document.getElementById('moment'),cards=[...document.querySelectorAll('.card')];
function show(){let n=Number(range.value);for(const card of cards){card.hidden=chooser.value!=='all'&&chooser.value!==card.dataset.theme;
let im=card.querySelector('img');im.src=im.dataset.path+'/'+im.dataset.i+'-'+(n%2)+'.png'}
for(const im of document.querySelectorAll('.modcard img')){im.src=im.dataset.mode+'/'+n+'.png'}
document.getElementById('momentText').textContent=(n+1)+' / 4'}
chooser.addEventListener('change',show);range.addEventListener('input',show);</script></body></html>'''
    (root / "index.html").write_text(site, encoding="utf-8")
    home = output / "index.html"
    main = home.read_text(encoding="utf-8")
    if 'href="sprites/index.html"' not in main:
        main = main.replace("</header>", '<p><a href="sprites/index.html">Explore sprite renderers and image atlases →</a></p></header>')
        home.write_text(main, encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_site(args.output)
    print(f"Built {result['themes']} themes and {result['images']} themed sprite previews")


if __name__ == "__main__":
    main()
