"""Public, dependency-light synthetic particle/smoke gallery for GitHub Pages.

No original songs or generated detector claims: timing examples use synthetic
DanceMoves-formatted cues, and movement is sampled from released DanceRudiments.
"""

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from framerudiments.io import file_ref, sha256
from framerudiments.particle_engine import ParticleEmitter

FRAMES = (20, 42, 70, 106)
SCENES = (
    ("ambient-smoke", "smoke", "continuous", "Soft rising smoke", {"colour": "#9ccbe8", "rate": 19}),
    (
        "dense-fog",
        "smoke",
        "continuous",
        "Dense drifting fog",
        {"colour": "#cfdbeb", "size": 22, "speed": 0.035, "rate": 24},
    ),
    ("ember-drift", "embers", "continuous", "Rising orange embers", {"rate": 33, "gravity": -0.07}),
    ("spark-fountain", "sparks", "continuous", "Radial spark fountain", {"rate": 42, "spread": 210}),
    ("snowfall", "snow", "continuous", "Snow with crosswind", {"wind": 0.04, "rate": 62}),
    ("rain", "rain", "continuous", "Directional rainfall", {"wind": -0.11, "rate": 65}),
    ("fireflies", "fireflies", "continuous", "Pulsating light dots", {"rate": 31}),
    ("confetti", "confetti", "continuous", "Floating square sprites", {"rate": 20}),
    ("cue-smoke", "smoke", "cue", "Drop-triggered smoke burst", {"rate": 0, "size": 13}),
    (
        "cue-sparks",
        "sparks",
        "cue",
        "Section-triggered spark burst",
        {"rate": 0, "direction": 0, "spread": 360},
    ),
    ("hybrid-embers", "embers", "hybrid", "Continuous embers and cue fireworks", {"rate": 12}),
    (
        "modulated-wind",
        "smoke",
        "continuous",
        "Native DanceRudiment modulates wind, hue and size",
        {"rate": 20},
    ),
)


class SyntheticProject:
    def __init__(self, directory):
        self.base = Path(directory)
        self.width, self.height = 480, 270
        self.fps = 60
        self.sample_rate = 48000
        self.start_sample = 0
        self.end_sample = 48000 * 3
        self.frame_count = 180
        self.repeats = 1
        self.audio_ref = {"sha256": "0" * 64}
        self.clock = SimpleNamespace(beat_at=lambda seconds: seconds * 2)
        self.inputs = []

    def ref(self, data):
        value = file_ref(data, self.base)
        self.inputs.append(value)
        return value


def context(frame):
    return SimpleNamespace(
        frame=frame,
        output_seconds=frame / 60,
        source_seconds=frame / 60,
        beat=frame / 30,
        lyric=None,
        cue=None,
    )


def make_layer(project, name, kind, emission, parameters, cue_ref):
    style = {
        "seed": 33,
        "emitter_x": 0.5,
        "emitter_y": 0.75,
        "opacity": 0.82,
        "lifetime": 2.2,
        "size": 8,
        **parameters,
    }
    if emission != "continuous":
        mode = {"mode": emission, "cue_file": cue_ref, "labels": ["DROP*"], "burst_count": 55}
    else:
        mode = {"mode": "continuous"}
    mods = []
    if name == "modulated-wind":
        for ident, pattern, axis, key, bounds in (
            ("breeze", "circle", "x", "wind", [-0.3, 0.3]),
            ("colour", "bounce", "y", "hue", [-150, 150]),
            ("size", "circle", "z", "size", [5, 18]),
        ):
            mods.append(
                {
                    "id": ident,
                    "motion": {"kind": "named", "pattern": pattern},
                    "axis": axis,
                    "target": "style." + key,
                    "range": bounds,
                }
            )
    return ParticleEmitter(
        project,
        {
            "id": name,
            "type": "emitter",
            "kind": kind,
            "bounds": [0, 0, project.width, project.height],
            "emission": mode,
            "style": style,
            "modulations": mods,
        },
        context,
    )


def draw_test_frame(layer, frame):
    canvas = Image.new("RGBA", (480, 270), "#071429")
    draw = ImageDraw.Draw(canvas)
    for x in range(24, 456, 12):
        strength = 0.5 + 0.5 * math.sin(x * 0.036 - frame * 0.083)
        high = 14 + round(strength * 70)
        draw.rounded_rectangle(
            (x, 256 - high, x + 7, 256), radius=3, fill=(22, round(110 + strength * 100), 190, 120)
        )
    for j in range(4):
        draw.arc(
            (90 + j * 27, 75 + j * 14, 390 - j * 27, 340 - j * 14), 210, 325, fill=(50, 145, 215, 80), width=2
        )
    layer.draw(canvas, context(frame))
    return canvas.convert("RGB")


def build_site(output):
    output = Path(output)
    site = output / "particles"
    site.mkdir(parents=True, exist_ok=False)
    cues = site / "synthetic-cues.lrc"
    cues.write_text("[00:00.50][SECTION: DROP 1]\n[00:01.50][DROP: FINAL]\n", encoding="utf-8")
    reference = {"path": cues.name, "sha256": sha256(cues)}
    project = SyntheticProject(site)
    rows = []
    for name, kind, emission, description, parameters in SCENES:
        layer = make_layer(project, name, kind, emission, parameters, reference)
        folder = site / name
        folder.mkdir()
        frames = []
        for i, frame in enumerate(FRAMES):
            draw_test_frame(layer, frame).save(folder / f"{i}.png")
            frames.append(
                {
                    "frame": frame,
                    "active": layer.frame_report["active"],
                    "cue_bursts": layer.frame_report["cue_bursts"],
                }
            )
        rows.append(
            {
                "id": name,
                "label": description,
                "kind": kind,
                "emission": emission,
                "parameters": parameters,
                "frames": frames,
            }
        )
    catalogue = {
        "schema": "framerudiments.synthetic-particle-gallery.v1",
        "synthetic": True,
        "measured_audio": False,
        "scenes": rows,
        "frames": list(FRAMES),
    }
    (site / "catalogue.json").write_text(json.dumps(catalogue, indent=2) + "\n", encoding="utf-8")
    cards = "\n".join(
        f'<article><img loading="lazy" data-scene="{row["id"]}" src="{row["id"]}/0.png" '
        f'alt="{row["label"]}"><h2>{row["label"]}</h2><small>{row["kind"]} · '
        f"{row['emission']}</small></article>"
        for row in rows
    )
    page = (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FrameRudiments · Particle and Smoke Engine</title><style>
:root{color-scheme:dark}body{font:1rem system-ui;background:#091321;color:#edf5ff;max-width:1350px;margin:auto;padding:32px}a{color:#70eed5}h1{font-size:clamp(2rem,5vw,4rem);margin:18px 0 8px}p{line-height:1.5;color:#bcd2e9}.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:18px}article{border:1px solid #345176;background:#132642;border-radius:13px;overflow:hidden}article img{display:block;width:100%;aspect-ratio:16/9}article h2{font-size:1rem;margin:12px 15px 3px}article small{display:block;margin:0 15px 15px;color:#a4b9d0}.tools{padding:18px 0 26px;display:flex;gap:20px;align-items:center;flex-wrap:wrap}input{width:240px;accent-color:#76f2d6}button{background:#173f50;color:white;padding:8px 15px;border:1px solid #418392;border-radius:9px}</style></head><body>
<a href="../index.html">← All FrameRudiments visualisers</a><h1>Particles, smoke and cue-triggered effects</h1><p>Each example is generated by CI from deterministic synthetic scene geometry and source-timed example cues; this is not recorded music or a fluid-dynamics simulation. Continuous, cue-triggered and hybrid emission share the exact FrameRudiments render clock.</p>
<div class="tools"><label>Timeline <input id="scrub" type="range" min="0" max="3" value="0"></label><output id="moment">1 / 4</output><button id="play">Play samples</button><a href="catalogue.json">JSON settings + evidence</a></div>
<div class="tiles">"""
        + cards
        + """</div><script>
let t=null;const scrub=document.getElementById('scrub');function update(){const n=Number(scrub.value);document.querySelectorAll('img[data-scene]').forEach(im=>im.src=im.dataset.scene+'/'+n+'.png');document.getElementById('moment').textContent=(n+1)+' / 4'}scrub.addEventListener('input',update);document.getElementById('play').addEventListener('click',()=>{if(t){clearInterval(t);t=null;return}t=setInterval(()=>{scrub.value=(Number(scrub.value)+1)%4;update()},800)})
</script></body></html>"""
    )
    (site / "index.html").write_text(page, encoding="utf-8")
    index = output / "index.html"
    if index.exists():
        content = index.read_text(encoding="utf-8")
        if 'href="particles/index.html"' not in content:
            content = content.replace(
                "</header>",
                '<p><a href="particles/index.html">Explore particles, smoke and cue bursts →</a></p></header>',
            )
            index.write_text(content, encoding="utf-8")
    return catalogue


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build_site(args.output)
    print(f"Built {len(result['scenes'])} particle/smoke examples")
