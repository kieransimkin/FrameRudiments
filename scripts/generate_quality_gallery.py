"""CI-generated spatial-AA, directional-streak and real-frame shutter comparisons.

All spectral/motion content is synthetic. Actual source FFT and DanceRudiment
clock contracts are demonstrated separately by the production compositor.
"""
import argparse
import json
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from framerudiments.quality_effects import TemporalShutter, linear_shutter_composite
from framerudiments.output_quality import OutputQuality
from framerudiments.rudiment_renderers import default_renderers
from framerudiments.visual_style import VisualStyle
from framerudiments.modulation import RudimentModulations
from generate_visualisation_gallery import fixture

MOMENTS = (36, 66, 96, 126)
DISPLAY_VIEWS = (
    ("spectrum-annular-bars", True, "Annular spectrum"),
    ("waveform-phosphor", False, "Phosphor scope"),
    ("spectrum-led-matrix", True, "LED analyser"),
    ("waveform-ribbon", False, "Motion ribbon"),
)
SCENES = (
    ("default-aa", "Highest default spatial and temporal quality", {}, None,
     "4x source geometry + 8x true time samples + final edge treatment"),
    ("default-off", "Explicit reduced-quality pixel-art path", {}, None,
     "Disable the default supersampling and raster AA"),
    ("raw", "Original output", {}, None, "Unfiltered vector/raster samples"),
    ("edge-aa", "Edge-aware AA", {"antialias": .85, "antialias_radius": .85}, None,
     "Alpha-aware edge smoothing, preserving interior textures"),
    ("fine-aa", "High-quality AA", {"antialias": 1, "antialias_radius": 1.2,
                                     "antialias_passes": 2, "antialias_threshold": .04}, None,
     "Two edge-aware antialias passes; not true source supersampling"),
    ("trail-blur", "Directional streaks", {"motion_blur": .85, "motion_blur_distance": 18,
                                          "motion_blur_angle": 18, "motion_blur_samples": 12}, None,
     "Premultiplied-alpha subpixel directional shutter"),
    ("radial-blur", "Diagonal motion blur", {"motion_blur": .75, "motion_blur_distance": 12,
                                            "motion_blur_angle": -62, "motion_blur_samples": 16,
                                            "motion_blur_trail": .35}, None,
     "Arbitrary angle and trailing-to-centred blur control"),
    ("temporal-soft", "Observed-frame blur", {},
     {"samples": 3, "shutter_frames": 2, "strength": .65, "falloff": 1.2},
     "Linear-light accumulation of prior encoded frames"),
    ("temporal-long", "Long shutter", {},
     {"samples": 6, "shutter_frames": 5, "strength": .9, "falloff": .5},
     "History-based blur; no invented future/subframe motion"),
    ("both-blurs", "AA + both blur types", {"antialias": .8, "motion_blur": .45,
                                            "motion_blur_distance": 9, "motion_blur_angle": 28},
     {"samples": 4, "shutter_frames": 3, "strength": .5, "falloff": 1},
     "Stack of edge-aware AA, directional approximation and temporal integration"),
    ("dancerudiment", "DanceRudiment-driven", {"antialias": .7, "motion_blur": .6,
                                               "motion_blur_distance": 10,
                                               "motion_blur_angle": 0}, None,
     "Independent circle/bounce modulation of blur angle, strength and AA"),
)


def _context(n):
    return SimpleNamespace(frame=n, output_seconds=n / 60,
                           source_seconds=n / 60, beat=n / 30, lyric=None, cue=None)


def _project():
    return SimpleNamespace(base=Path("."), inputs=[], clock=SimpleNamespace(beat_at=lambda t: t * 2))


@lru_cache(maxsize=1)
def _registry():
    return default_renderers()


@lru_cache(maxsize=64)
def _base(n, element_grade=False):
    size = (480, 296)
    canvas = Image.new("RGBA", size, "#0b1424")
    draw = ImageDraw.Draw(canvas)
    for i, (name, spectral, title) in enumerate(DISPLAY_VIEWS):
        w, h = 226, 124
        renderer, _ = _registry().create(name, (w, h), {"background": "#11283e"})
        part = renderer.render(fixture(n, spectral=spectral))
        if element_grade:
            part = VisualStyle({"antialias": .9, "motion_blur": .5,
                                "motion_blur_distance": 9, "motion_blur_angle": 25}).apply(part)
        x = 12 + (i % 2) * 240
        y = 10 + (i // 2) * 143
        canvas.alpha_composite(part, (x, y))
        draw.text((x + 5, y + h + 2), title, fill="#b5cbe4")
    return canvas.convert("RGB")


def _run_temporal(n, config):
    if config is None:
        return _base(n)
    temporal = TemporalShutter(_project(), config, _context)
    indices, weights, settings, _ = temporal.plan(_context(n))
    return linear_shutter_composite([_base(f) for f in indices], weights, settings["strength"])


def _run(n, config, temporal, *, modulator=None):
    image = _run_temporal(n, temporal)
    visual = dict(config)
    if modulator is not None:
        _, visual, _ = modulator.apply(_context(n), {}, visual)
    return VisualStyle(visual).apply(image.convert("RGBA")).convert("RGB")


def build_site(output):
    output = Path(output)
    root = output / "quality"
    root.mkdir(parents=True, exist_ok=False)
    mods = RudimentModulations(_project(), [
        {"id": "direction", "motion": {"kind": "named", "pattern": "circle"},
         "axis": "x", "target": "visual.motion_blur_angle", "range": [-130, 130]},
        {"id": "exposure", "motion": {"kind": "named", "pattern": "bounce"},
         "axis": "y", "target": "visual.motion_blur_distance", "range": [3, 22]},
        {"id": "smooth", "motion": {"kind": "named", "pattern": "circle"},
         "axis": "z", "target": "visual.antialias", "range": [.2, 1]},
    ], _context)
    rows = []
    for name, label, config, temporal, description in SCENES:
        folder = root / name
        folder.mkdir()
        for k, n in enumerate(MOMENTS):
            image = _run(n, config, temporal, modulator=mods if name == "dancerudiment" else None)
            if name == "default-aa":
                image = OutputQuality().apply(image)
            elif name == "default-off":
                image = OutputQuality({"enabled": False}).apply(image)
            image.save(folder / f"{k}.png")
        rows.append(dict(name=name, label=label, visual=config, temporal=temporal,
                         description=description, frames=len(MOMENTS)))
    catalogue = dict(schema="framerudiments.synthetic-quality-gallery.v1", synthetic=True,
                     default_render_quality=OutputQuality().evidence,
                     measured_audio=False, frames=list(MOMENTS),
                     visualisers=[row[0] for row in DISPLAY_VIEWS], scenes=rows)
    (root / "catalogue.json").write_text(json.dumps(catalogue, indent=2), encoding="utf-8")
    cards = "\n".join(f'''<article><h2>{row["label"]}</h2><p>{row["description"]}</p>
      <div class="compare"><img data-name="{row["name"]}" src="{row["name"]}/0.png"
      alt="{row["label"]} preview of four synthetic visualisations"></div>
      <code>{row["name"]}</code></article>''' for row in rows)
    html = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FrameRudiments · Anti-aliasing &amp; Motion Blur Atlas</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{background:#080f1c;color:#e5f5ff;font:16px system-ui;margin:0}
header{max-width:1300px;margin:auto;padding:45px 24px 22px}a{color:#5ee9dc}h1{font-size:clamp(2rem,5vw,3.8rem);line-height:1.1}
p{color:#adc0d5;line-height:1.6}.tools{position:sticky;top:0;background:#142438f2;border-block:1px solid #33506d;z-index:4;padding:16px}
.tools>div{max-width:1300px;margin:auto;display:flex;flex-wrap:wrap;gap:14px;align-items:center}
main{max-width:1300px;margin:24px auto;padding:0 24px;display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,380px),1fr));gap:20px}
article{background:#12233a;border:1px solid #2c4c67;border-radius:14px;padding:16px}article h2{margin:0 0 5px;font-size:1.15rem}
article p{min-height:55px;font-size:.88rem;margin:0 0 12px}.compare{aspect-ratio:480/296;background:#0b1424;overflow:hidden}
.compare img{display:block;width:100%;height:100%;object-fit:contain}code{color:#6de3d4;display:block;margin-top:12px}
input{accent-color:#5ee9dc}footer{max-width:1300px;margin:30px auto;padding:20px 24px 60px;color:#a6b8cd}
</style></head><body><header><a href="../index.html">← Full visualisation atlas</a>
<h1>Anti-aliasing &amp; motion-blur laboratory</h1>
<p>Side-by-side methods from the production effect pipeline: edge-aware final-raster AA, subpixel directional shutter, real observed-frame temporal accumulation, combined effects and DanceRudiment-modulated parameters. The four displays in every panel represent four different visualiser geometries.</p>
<p><strong>Accuracy:</strong> All spectrum values and motion examples are synthetic; antialiasing does not invent missing vector subpixels; directional blur approximates a motion vector and historical blur integrates observed encoded frames, not optical flow or future subframes.</p>
<a href="catalogue.json">Machine-readable presets and methods</a></header>
<div class="tools"><div><label for="frame">Timeline <input id="frame" type="range" min="0" max="3" value="0" step="1"></label>
<output id="count">1 / 4</output> <button type="button" id="play">Play sampled moments</button></div></div>
<main>''' + cards + '''</main><footer>Offline, reproducible demonstrations · <a href="../scopes/">Element and frame scope examples</a></footer>
<script>const slider=document.querySelector('#frame'),count=document.querySelector('#count'),play=document.querySelector('#play');let timer;
function update(){document.querySelectorAll('img[data-name]').forEach(img=>img.src=img.dataset.name+'/'+slider.value+'.png');count.textContent=(Number(slider.value)+1)+' / 4'}
slider.addEventListener('input',update);play.addEventListener('click',()=>{if(timer){clearInterval(timer);timer=null;play.textContent='Play sampled moments'}else{timer=setInterval(()=>{slider.value=(Number(slider.value)+1)%4;update()},900);play.textContent='Pause'}});</script></body></html>'''
    (root / "index.html").write_text(html, encoding="utf-8")
    home = output / "index.html"
    content = home.read_text(encoding="utf-8")
    link = '<p><a href="quality/index.html">Anti-aliasing and motion blur demonstrations →</a></p>'
    if link not in content:
        home.write_text(content.replace("</header>", link + "</header>"), encoding="utf-8")
    return catalogue


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    site = build_site(args.output)
    print(f"Generated {len(site['scenes'])} quality modes × {len(site['frames'])} frames")
