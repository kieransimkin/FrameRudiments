"""CI-generated demonstration of source-pip layer routing and clean overlays.

These are synthetic router/feedback *simulations*, not real Butterchurn or
projectM shader output. Native engine acceptance remains a separate CI suite.
"""

import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFilter, ImageChops

from framerudiments.visualiser_routing import VisualiserLayerRouting

SIZE = (460, 260)
MOMENTS = (0, 1, 2, 4, 8)
PIPS = (0, 10, 27, 45, 83, 99, 120, 141, 164)  # deliberately nonuniform tempo
NAMES = ("background", "layer:orbit", "caption", "layer:burst")
SCENES = (
    ("default-legacy", "Original continuous feed", {}, ("butterchurn",)),
    (
        "butterchurn-window",
        "Butterchurn: first 16 pips",
        {"elements": {"layer:orbit": {"feed": {"mode": "first_pips", "duration_pips": 16}}}},
        ("butterchurn",),
    ),
    (
        "projectm-window",
        "projectM: first 32 pips",
        {"elements": {"layer:orbit": {"feed": {"mode": "first_pips", "duration_pips": 32}}}},
        ("projectm",),
    ),
    (
        "butterchurn-overlay",
        "Butterchurn: 16 pips + clean overlay",
        {"elements": {"layer:orbit": {"feed": {"mode": "first_pips", "duration_pips": 16}, "overlay": True}}},
        ("butterchurn",),
    ),
    (
        "projectm-overlay",
        "projectM: 32 pips + clean overlay",
        {"elements": {"layer:orbit": {"feed": {"mode": "first_pips", "duration_pips": 32}, "overlay": True}}},
        ("projectm",),
    ),
    (
        "never-feed",
        "Never feed; always overlaid",
        {
            "elements": {
                "caption": {"feed": "never", "overlay": True},
                "layer:orbit": {"feed": "never", "overlay": True},
            }
        },
        ("butterchurn",),
    ),
    (
        "feedback-only",
        "Feed-only: no clean overlay",
        {
            "elements": {
                "caption": {"feed": {"mode": "first_pips", "duration_pips": 16}},
                "layer:orbit": {"feed": {"mode": "first_pips", "duration_pips": 16}},
            }
        },
        ("projectm",),
    ),
    (
        "caption-over",
        "Clean subtitles above feedback",
        {"elements": {"caption": {"feed": "never", "overlay": True}}},
        ("butterchurn",),
    ),
    (
        "background-only",
        "Background injected; effects clean on top",
        {
            "default": {"feed": "never", "overlay": True},
            "elements": {"background": {"feed": "continuous", "overlay": False}},
        },
        ("projectm",),
    ),
    (
        "cue-burst",
        "Burst after cue; on top and in feedback",
        {
            "elements": {
                "layer:burst": {"feed": {"mode": "first_pips", "duration_pips": 32}, "overlay": True},
                "caption": {"feed": "never", "overlay": True},
            }
        },
        ("butterchurn", "projectm"),
    ),
    (
        "reappearing",
        "Reappear: feed window resets",
        {
            "elements": {
                "layer:orbit": {
                    "feed": {"mode": "first_pips", "duration_pips": 16},
                    "retrigger": "reappearance",
                    "overlay": True,
                }
            }
        },
        ("butterchurn",),
    ),
    (
        "two-engines",
        "Butterchurn → projectM, clean captions",
        {
            "elements": {
                "caption": {"feed": "never", "overlay": True},
                "layer:orbit": {"feed": {"mode": "first_pips", "duration_pips": 32}},
            }
        },
        ("butterchurn", "projectm"),
    ),
)


def _context(frame):
    return SimpleNamespace(
        frame=frame,
        source_seconds=frame / 60,
        output_seconds=frame / 60,
        beat=PIPS[frame] / 64,
        lyric=None,
        cue=None,
    )


def _parts(frame, *, reappear=False):
    w, h = SIZE
    background = Image.new("RGBA", SIZE, "#102238")
    draw = ImageDraw.Draw(background)
    for y in range(30, h, 25):
        draw.line((0, y, w, y), fill="#1c3851", width=1)
    for x in range(0, w, 25):
        draw.line((x, 0, x, h), fill="#1c3851", width=1)
    orbit = Image.new("RGBA", SIZE)
    d = ImageDraw.Draw(orbit)
    cx = 112 + round(30 * math.sin(frame * 0.55))
    d.ellipse((cx - 47, 88, cx + 47, 182), fill="#f8ae59", outline="#ffdfa6", width=4)
    for i in range(12):
        theta = math.tau * i / 12 + frame * 0.23
        x, y = cx + 70 * math.cos(theta), 138 + 70 * math.sin(theta)
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill="#6ee5e3")
    caption = Image.new("RGBA", SIZE)
    d = ImageDraw.Draw(caption)
    d.rounded_rectangle((210, 188, 448, 241), radius=10, fill=(10, 20, 36, 226))
    d.text((226, 208), "LYRICS STAY LEGIBLE", fill="#e3f5ef")
    burst = Image.new("RGBA", SIZE)
    d = ImageDraw.Draw(burst)
    if frame in (1, 2, 4):
        for i in range(12):
            theta = math.tau * i / 12
            x, y = 329 + 32 * math.cos(theta), 93 + 32 * math.sin(theta)
            d.line((329, 93, x, y), fill="#fa5b7a", width=3)
    items = [("background", background)]
    if not (reappear and frame == 2):
        items.append(("layer:orbit", orbit))
    items.extend([("caption", caption), ("layer:burst", burst)])
    return items


class SimulatedFeedback:
    """Explanatory transfer function only. NOT the actual GPU engine."""

    def __init__(self, name):
        self.name = name
        self.image = Image.new("RGBA", SIZE, "#061020")

    def process(self, input_image, frame):
        if self.name == "butterchurn":
            faded = Image.blend(Image.new("RGBA", SIZE, "#061020"), self.image, 0.91)
            warped = faded.transform(
                SIZE, Image.Transform.AFFINE, (1, 0.006, -1, -0.004, 1, 2), resample=Image.Resampling.BILINEAR
            )
        else:
            faded = Image.blend(Image.new("RGBA", SIZE, "#061020"), self.image, 0.93)
            warped = ImageChops.offset(faded, -2, 1).filter(ImageFilter.GaussianBlur(0.3))
        layer = input_image.copy()
        layer.putalpha(layer.getchannel("A").point(lambda alpha: round(alpha * 0.75)))
        warped.alpha_composite(layer)
        self.image = warped
        return warped


def _panel(input_image, output, caption):
    canvas = Image.new("RGB", (SIZE[0] * 2 + 22, SIZE[1] + 46), "#071421")
    canvas.paste(input_image.convert("RGB"), (0, 35))
    canvas.paste(output.convert("RGB"), (SIZE[0] + 22, 35))
    d = ImageDraw.Draw(canvas)
    d.text((10, 11), "INJECTED TEXTURE", fill="#a3e9ee")
    d.text((SIZE[0] + 32, 11), "SIMULATED FEEDBACK + CLEAN OVERLAY", fill="#a3e9ee")
    d.text((10, SIZE[1] + 38), caption[:100], fill="#bed2e9")
    return canvas


def build_site(output):
    output = Path(output)
    root = output / "routing"
    root.mkdir(parents=True, exist_ok=False)
    catalogue = {
        "schema": "framerudiments.synthetic-routing-gallery.v1",
        "synthetic": True,
        "real_gpu_render": False,
        "engines": ["butterchurn", "projectm"],
        "frames": list(MOMENTS),
        "pips": [PIPS[i] for i in MOMENTS],
        "scenes": [],
    }
    for name, label, config, engine_names in SCENES:
        router = VisualiserLayerRouting(config, NAMES, SIZE)
        engines = [SimulatedFeedback(v) for v in engine_names]
        folder = root / name
        folder.mkdir()
        reports = []
        for n in range(MOMENTS[-1] + 1):
            source, overlay = router.route(_context(n), _parts(n, reappear=name == "reappearing"))
            rendered = source
            for engine in engines:
                rendered = engine.process(rendered, n)
            finished = rendered.copy()
            finished.alpha_composite(overlay)
            annotation = (
                f"Frame {n} | pip {PIPS[n]} | feeding {', '.join(router.last_report['feeding']) or 'none'}"
            )
            if n in MOMENTS:
                _panel(source, finished, annotation).save(folder / f"{n}.png")
                reports.append(router.last_report)
        catalogue["scenes"].append(
            dict(name=name, label=label, engines=list(engine_names), config=config, routing_reports=reports)
        )
    (root / "catalogue.json").write_text(json.dumps(catalogue, indent=2) + "\n", encoding="utf-8")
    cards = "\n".join(
        "<article><h2>"
        + row["label"]
        + "</h2><p>"
        + " → ".join(row["engines"])
        + '</p><img data-name="'
        + row["name"]
        + '" src="'
        + row["name"]
        + '/0.png" alt="'
        + row["label"]
        + ' source and routed feedback preview"></article>'
        for row in catalogue["scenes"]
    )
    page = (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FrameRudiments · Visualiser layer routing</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#071421;color:#ecf7fa;font:16px system-ui}header,main,footer{max-width:1250px;margin:auto;padding:24px}header{padding-top:50px}a{color:#69e7d8}h1{font-size:clamp(2rem,5vw,3.8rem)}p{line-height:1.5;color:#bdd0dc}.control{background:#12243c;padding:16px 24px;position:sticky;top:0}article{border:1px solid #32506d;border-radius:12px;background:#102136;margin:12px 0;padding:14px}article h2{font-size:1rem}article img{width:100%;height:auto}input{width:275px;accent-color:#61e5d4}small{color:#acc3d5}footer{padding-bottom:80px}</style></head><body>
<header><a href="../index.html">← All visualisers</a><h1>What enters feedback — what stays clean</h1><p>Feed a layer continuously, for only its first N rhythmic pips, or never; render it cleanly above the last visualiser independently. Native 64-pip source beat phase supports irregular tempo. A hidden and reappearing layer can restart its feed window.</p><p><strong>Important:</strong> These are deterministic synthetic transfer-function <em>simulations</em> to test routing. They are not screenshots of actual Butterchurn/projectM GPU output. Actual engines retain their separately tested, checksum-pinned runtimes.</p><a href="catalogue.json">Scene configs and per-frame routing evidence</a></header>
<div class="control"><label for="moment">Musical moment: <input id="moment" type="range" min="0" max="4" step="1" value="0"></label> <output id="value">0 pips</output></div>
<main>"""
        + cards
        + """</main><footer><a href="../quality/">Spatial and temporal effects</a> · <a href="../sprites/">Sprite atlas options</a></footer><script>const frames=[0,1,2,4,8],pips=[0,10,27,83,164];const slider=document.getElementById('moment');slider.addEventListener('input',()=>{const idx=Number(slider.value);document.querySelectorAll('img[data-name]').forEach(img=>img.src=img.dataset.name+'/'+frames[idx]+'.png');document.getElementById('value').textContent=pips[idx]+' pips'})</script></body></html>"""
    )
    (root / "index.html").write_text(page, encoding="utf-8")
    home = output / "index.html"
    if home.is_file():
        html = home.read_text(encoding="utf-8")
        link = '<p><a href="routing/index.html">Layer-selective Butterchurn/projectM input routing →</a></p>'
        if link not in html:
            home.write_text(html.replace("</header>", link + "</header>"), encoding="utf-8")
    return catalogue


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    print("Generated", len(build_site(args.output)["scenes"]), "routing scenes")
