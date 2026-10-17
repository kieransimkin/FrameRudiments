"""Create public, fully synthetic samples contrasting element-local and whole-frame effects."""

import argparse
from pathlib import Path
import json
from types import SimpleNamespace

from PIL import Image, ImageDraw

from framerudiments.scoped_effects import CompositingEffects


SCENES = (
    ("unprocessed", {}),
    (
        "sprite-only",
        {
            "elements": {
                "layer:orbit": [
                    {
                        "id": "orbit-gradient",
                        "visual": {
                            "gradient": {"mode": "angular", "stops": "rainbow", "blend": "replace"},
                            "glow": 4,
                        },
                    }
                ]
            }
        },
    ),
    (
        "shared-element-effects",
        {"all_elements": [{"id": "all", "visual": {"vignette": 0.45, "scanlines": 0.32}}]},
    ),
    (
        "finished-frame",
        {"frame": [{"id": "master", "visual": {"chromatic_shift": 3, "vignette": 0.7, "glow": 2}}]},
    ),
    (
        "two-scope-stack",
        {
            "elements": {
                "layer:orbit": [
                    {
                        "id": "part",
                        "visual": {"gradient": {"mode": "radial", "stops": "fire", "blend": "replace"}},
                    }
                ]
            },
            "frame": [{"id": "all", "visual": {"scanlines": 0.45, "grain": 0.14}}],
        },
    ),
    (
        "native-modulated",
        {
            "elements": {
                "layer:orbit": [
                    {
                        "id": "dancer",
                        "visual": {
                            "gradient": {"mode": "angular", "stops": "aurora", "blend": "replace"},
                            "glow": 1,
                        },
                        "modulations": [
                            {
                                "id": "glow",
                                "target": "visual.glow",
                                "motion": {"kind": "named", "pattern": "bounce"},
                                "axis": "x",
                                "range": [1, 7],
                            },
                            {
                                "id": "palette",
                                "target": "visual.gradient.stops",
                                "choices": ["aurora", "fire", "rainbow"],
                                "motion": {"kind": "named", "pattern": "circle"},
                                "axis": "y",
                            },
                        ],
                    }
                ]
            }
        },
    ),
)
TIMES = (24, 55, 86, 117)


def _project():
    # Only a native clock is needed; there are no song assets, private audio,
    # saved recordings, external fonts, network calls, or spectral claims.
    return SimpleNamespace(base=Path("."), clock=SimpleNamespace(beat_at=lambda s: 2 * s), inputs=[])


def _image(n, pipeline, frame):
    ctx = SimpleNamespace(
        frame=frame,
        output_seconds=frame / 60,
        source_seconds=frame / 60,
        beat=frame / 30,
        lyric=None,
        cue=None,
    )
    pipeline.begin()
    w, h = 480, 270
    image = pipeline.apply_element(Image.new("RGBA", (w, h), "#121e32"), "background", ctx)
    orbit = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(orbit)
    cx = 125 + round(18 * __import__("math").sin(frame / 19))
    d.ellipse((cx - 78, 72, cx + 78, 228), fill="#40bfdf", outline="#aef8ff", width=6)
    for j in range(28):
        x = cx - 100 + j * 7
        d.line((x, 120 - j % 5 * 8, x, 194 + (j + frame // 9) % 7 * 3), fill="#f0a772", width=3)
    image.alpha_composite(pipeline.apply_element(orbit, "layer:orbit", ctx))
    accent = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(accent)
    d.rounded_rectangle((255, 35, 455, 226), radius=16, fill="#33476b", outline="#9bc7e9", width=3)
    for j, amplitude in enumerate((12, 20, 40, 65, 82, 45, 29, 53, 86, 42, 19, 10)):
        x = 268 + j * 15
        d.rectangle((x, 187 - amplitude, x + 8, 187), fill="#f1a267")
    image.alpha_composite(pipeline.apply_element(accent, "layer:accent", ctx))
    caption = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(caption)
    d.rectangle((253, 242, 459, 248), fill="#f8e7c8")
    image.alpha_composite(pipeline.apply_element(caption, "caption", ctx))
    return pipeline.apply_frame(image, ctx)


def build_site(output):
    output = Path(output)
    folder = output / "scopes"
    folder.mkdir(parents=True, exist_ok=False)
    rows = []
    for name, configuration in SCENES:
        pipeline = CompositingEffects(
            _project(),
            configuration,
            lambda frame: SimpleNamespace(
                frame=frame,
                output_seconds=frame / 60,
                source_seconds=frame / 60,
                beat=frame / 30,
                lyric=None,
                cue=None,
            ),
            valid_elements={"background", "layer:orbit", "layer:accent", "caption"},
        )
        root = folder / name
        root.mkdir()
        for i, frame in enumerate(TIMES):
            _image(i, pipeline, frame).save(root / f"{i}.png")
        rows.append({"name": name, "frames": len(TIMES), "configuration": configuration})
    (folder / "examples.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    cards = "\n".join(
        f'<article><img loading="lazy" data-name="{row["name"]}" src="{row["name"]}/0.png" alt="{row["name"]} effect example"><h2>{row["name"]}</h2></article>'
        for row in rows
    )
    page = (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FrameRudiments · Element &amp; frame effects</title><style>
:root{color-scheme:dark}body{font:1rem system-ui;background:#0b1525;color:#edf4ff;max-width:1200px;margin:auto;padding:30px}
a{color:#6ce9da}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}
article{border:1px solid #37506a;border-radius:12px;background:#152943;overflow:hidden}img{width:100%;display:block;aspect-ratio:16/9}h2{font-size:1rem;padding:0 16px}input{width:250px}</style></head><body>
<a href="../index.html">← All Rudiment visualisers</a><h1>Post-render effects at two scopes</h1>
<p>Every preview is rendered from synthetic shapes and native DanceRudiment movement, not song audio. Element scope treats each layer independently; frame scope grades their final composite. Effects run before MilkDrop/projectM feedback.</p>
<label>Timeline <input type="range" min="0" max="3" step="1" value="0" id="frame"></label>
<p>Six reproducible pipelines × four sampled moments. <a href="examples.json">Configurations</a></p><section class="grid">"""
        + cards
        + """</section>
<script>document.getElementById('frame').addEventListener('input',e=>{document.querySelectorAll('img[data-name]').forEach(img=>img.src=img.dataset.name+'/'+e.target.value+'.png')})</script></body></html>"""
    )
    (folder / "index.html").write_text(page, encoding="utf-8")
    home = output / "index.html"
    content = home.read_text(encoding="utf-8")
    if 'href="scopes/index.html"' not in content:
        content = content.replace(
            "</header>",
            '<p><a href="scopes/index.html">Explore element-local vs full-frame effects →</a></p></header>',
        )
        home.write_text(content, encoding="utf-8")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build_site(args.output)
    print(f"Built {len(result)} scope examples with {len(TIMES)} deterministic moments each")
