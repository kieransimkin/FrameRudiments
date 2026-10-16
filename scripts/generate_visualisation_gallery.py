"""Build a dependency-light, reproducible GitHub Pages visualisation gallery.

The synthetic calibration and motion curves exercise rendering only; they are
NOT audio derived from an actual StemLab recording or a replacement for its DSP.
"""
import argparse
from dataclasses import asdict
import html
import json
import math
from pathlib import Path
from types import SimpleNamespace

from framerudiments.rudiment_renderers import RudimentFrame, RudimentSample, default_renderers
from framerudiments.spectrum import SpectrumBand
from framerudiments.visualisation_catalogue import ALL_VIEWS

FRAMES = (35, 65, 95, 125)
BASE_VIEWS = (
    ("particles", "motion", "Seeded particle cloud driven by sampled rudiment axes", "native rudiment"),
    ("bars", "motion", "Signed instantaneous values across rudiment axes", "channel meter"),
    ("waveform", "motion", "Scrolling rudiment time-domain traces", "waveform monitor"),
    ("path", "motion", "First two axes plotted as an XY motion path", "XY oscilloscope"),
    ("spectrum", "spectrum", "Calibrated StemLab band meter with peak holds", "frequency band meter"),
)


def fixture(frame_number, *, spectral):
    """Return deterministic annotated DISPLAY test data, not measured PCM."""
    n = 48 if spectral else 3
    bands = tuple(SpectrumBand(f"test_{j:03d}", 40 + j * 160, 200 + j * 160,
                                1.0, 1.0, False, "0" * 64, 48000, 4096) for j in range(n)) if spectral else ()

    def channel_values(t):
        if not spectral:
            return tuple(.77 * math.sin(t / (9 + 4 * j) + j * 1.6)
                         + .13 * math.sin(t / (3 + j) + j) for j in range(n))
        centre = 4 + ((t * .41) % 37)
        return tuple(2 * min(.92, .009 + .68 * math.exp(-((j - centre) / 4.8) ** 2)
                       + .22 * math.exp(-((j - 19) / 2.8) ** 2)
                       + .14 * math.exp(-((j - 37) / 3.5) ** 2)
                       + .025 * (1 + math.sin(t / 11 + j * .74))) - 1 for j in range(n))

    history = tuple(RudimentSample(SimpleNamespace(frame=t, output_seconds=t / 60), channel_values(t))
                    for t in range(max(0, frame_number - 127), frame_number + 1))
    return RudimentFrame(SimpleNamespace(frame=frame_number, output_seconds=frame_number / 60),
                         tuple(f"test_{j:03d}" for j in range(n)) if spectral else ("x", "y", "z"),
                         history[-1].values, history, 128, 1, bands)


def build_site(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = [dict(name=a, family=b, description=c, origin=d) for a, b, c, d in BASE_VIEWS]
    rows.extend(asdict(row) for row in ALL_VIEWS)
    registry = default_renderers()
    expected = set(registry.names)
    if set(row["name"] for row in rows) != expected:
        raise RuntimeError(f"Gallery/catalogue does not cover registry: missing={expected - {r['name'] for r in rows}}")
    for row in rows:
        name = row["name"]
        image_directory = output / "images" / name
        image_directory.mkdir(parents=True, exist_ok=True)
        style = {"background": "#111d32", "padding": 10, "colours": ["#4fe5d0", "#ffbd69", "#c39dff", "#6b9dff"]}
        if name == "spectrum":
            style["labels"] = False
        renderer, _ = registry.create(name, (480, 270), style)
        for index, stamp in enumerate(FRAMES):
            frame = fixture(stamp, spectral=row["family"] != "motion")
            image = renderer.render(frame)
            if image.mode != "RGBA" or image.size != (480, 270):
                raise RuntimeError(f"Unexpected renderer output from {name}")
            image.save(image_directory / f"{index}.png", optimize=True)
    manifest = dict(schema="framerudiments.visualisation-gallery.v1", frames=list(FRAMES),
                    synthetic_test_data=True, not_audio_measurements=True,
                    summary="Synthetic motion and invented calibrated-band numeric test fixtures, not real measured audio",
                    views=rows)
    (output / "catalogue.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cards = []
    for row in rows:
        name = html.escape(row["name"], quote=True)
        family = html.escape(row["family"], quote=True)
        description = html.escape(row["description"])
        origin = html.escape(row["origin"])
        cards.append(f'''<article class="tile" data-group="{family}" data-search="{name} {description} {origin}">
  <figure><img src="images/{name}/3.png" alt="Synthetic preview of {name}" data-mode="{name}" loading="lazy" width="480" height="270"></figure>
  <div class="tile-body"><div class="meta">{family} <span>· {origin}</span></div>
  <h2>{name}</h2><p>{description}</p></div></article>''')
    html_text = '''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="FrameRudiments renderer catalogue and reproducible synthetic visual tests">
<title>Visualisation Atlas · FrameRudiments</title>
<style>
:root{color-scheme:dark;background:#0b1220;color:#e9f1ff;font-family:system-ui,-apple-system,Segoe UI,sans-serif}
*{box-sizing:border-box}body{margin:0}a{color:#71edda}header{max-width:1280px;margin:auto;padding:50px 24px 28px}
.eyebrow{font-size:.75rem;letter-spacing:.18em;color:#73e4d2;font-weight:700;text-transform:uppercase}
h1{font-size:clamp(2.3rem,5.5vw,4.8rem);margin:16px 0;letter-spacing:-.055em;line-height:1.07}
.intro{max-width:850px;color:#b8c5dc;line-height:1.65}.facts{display:flex;gap:12px;flex-wrap:wrap;margin:28px 0}
.facts span{border:1px solid #32445f;border-radius:30px;padding:8px 14px;color:#cad8ed;font-size:.85rem}
.controls{position:sticky;top:0;z-index:5;background:#0c1727eb;backdrop-filter:blur(14px);border-block:1px solid #203148;padding:16px 24px}
.controls-inner{max-width:1280px;margin:auto;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
input[type=search]{background:#13223a;color:#fff;border:1px solid #405473;border-radius:9px;padding:12px;font:inherit;min-width:200px;flex:1}
select,button{background:#172c46;color:#e9f1ff;border:1px solid #45627e;border-radius:9px;padding:11px 13px;font:inherit;cursor:pointer}
input[type=range]{accent-color:#5ae6d4;width:175px}label{display:flex;gap:12px;align-items:center;color:#c8d8eb}
main{max-width:1280px;margin:32px auto 70px;padding:0 24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr));gap:20px}
.tile{min-width:0;overflow:hidden;border:1px solid #2a405d;border-radius:14px;background:#12213a;box-shadow:0 8px 24px #0002}
.tile[hidden]{display:none}.tile figure{margin:0;aspect-ratio:16/9;overflow:hidden;background:#111d32}
.tile img{width:100%;height:100%;object-fit:contain;display:block}.tile-body{padding:16px 19px 20px}
.meta{font-size:.68rem;font-weight:800;text-transform:uppercase;letter-spacing:.11em;color:#62e8d3}
.meta span{color:#91a7ca;font-weight:500}h2{font-size:1.08rem;margin:11px 0 7px;overflow-wrap:anywhere}
.tile p{margin:0;color:#aec1db;font-size:.87rem;line-height:1.5}footer{border-top:1px solid #26384c;color:#aebed3;padding:30px 24px;line-height:1.7}
footer>div{max-width:1280px;margin:auto} .notice{border-left:3px solid #f8c477;padding-left:14px;color:#d4ddec}
@media(max-width:550px){header{padding-top:30px}.controls{position:static}input[type=range]{width:115px}}
</style></head><body>
<header><div class="eyebrow">FrameRudiments / Public Visual Test Atlas</div><h1>Signals into<br>pictures.</h1>
<p class="intro">A generated gallery of every built-in Rudiment renderer, covering classic scopes, spectrum analysers, historical time-frequency displays, polar plots and modern creative variants. All previews use deterministic synthetic test inputs.</p>
<div class="facts"><span>''' + str(len(rows)) + ''' renderer options</span><span>''' + str(sum(x["family"] == "spectrogram" for x in rows)) + ''' spectrogram views</span><span>4 timeline samples</span><span>CI-generated, no private audio</span></div>
<p class="notice"><strong>Scientific accuracy:</strong> Spectral cards use <em>invented, calibrated-format Fourier-band values</em> for renderer tests. They do not depict measured music. Mel/Bark/ERB/log options remap existing band positions, not recalculate those transforms. Waveform cards show Rudiment motion channels, not PCM or stereo phase.</p>
<p><a href="https://github.com/kieransimkin/FrameRudiments">Source</a> · <a href="https://github.com/kieransimkin/FrameRudiments/blob/main/docs/visualisation-atlas.md">Methods and limitations</a> · <a href="catalogue.json">Machine-readable catalogue</a></p></header>
<div class="controls"><div class="controls-inner"><input type="search" id="search" aria-label="Find a visualiser" placeholder="Find a renderer or technique...">
<select id="family" aria-label="Filter display family"><option value="all">All families</option><option value="motion">Rudiment motion</option><option value="spectrum">Instant spectrum</option><option value="spectrogram">History spectrograms</option></select>
<label for="frame">Time <input id="frame" type="range" min="0" max="3" value="3" step="1"><output id="frame-label">4 / 4</output></label>
<button id="play" type="button" aria-pressed="false">Play samples</button><small id="count" aria-live="polite"></small>
</div></div><main><div class="grid" id="gallery">''' + "\n".join(cards) + '''</div></main>
<footer><div>FrameRudiments is a deterministic rendering toolkit. Catalogue previews are structural display tests, not claims about sound or perceptual quality. Exact analyses should come from pinned StemLab recordings. <a href="https://github.com/kieransimkin/FrameRudiments">Contribute on GitHub</a>.</div></footer>
<script>
const images=[...document.querySelectorAll('img[data-mode]')],cards=[...document.querySelectorAll('.tile')];
const slider=document.getElementById('frame'),label=document.getElementById('frame-label');
const search=document.getElementById('search'),family=document.getElementById('family'),count=document.getElementById('count');
const play=document.getElementById('play');let timer=null;
function update(){const t=Number(slider.value);for(const img of images){img.src='images/'+img.dataset.mode+'/'+t+'.png'}label.textContent=(t+1)+' / 4'}
function filter(){const q=search.value.toLowerCase().trim(),g=family.value;let n=0;for(const card of cards){
const ok=(g==='all'||g===card.dataset.group)&&card.dataset.search.toLowerCase().includes(q);card.hidden=!ok;if(ok)n++}count.textContent=n+' visible'}
function toggle(){if(timer){clearInterval(timer);timer=null;play.textContent='Play samples';play.setAttribute('aria-pressed','false')}else{
timer=setInterval(()=>{slider.value=(Number(slider.value)+1)%4;update()},800);play.textContent='Pause';play.setAttribute('aria-pressed','true')}}
slider.addEventListener('input',update);search.addEventListener('input',filter);family.addEventListener('change',filter);
play.addEventListener('click',toggle);document.addEventListener('visibilitychange',()=>{if(document.hidden&&timer)toggle()});filter();
</script></body></html>'''
    (output / "index.html").write_text(html_text, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    built = build_site(args.output)
    print(f"Built {len(built['views'])} views x {len(built['frames'])} test times at {args.output}")


if __name__ == "__main__":
    main()
