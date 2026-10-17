"""Synthetic CI gallery: true high-res geometry versus true subframe samples.

These are visual algorithm demonstrations; no real song recordings, STEM data
or captured native motion are claimed by this gallery.
"""

import argparse
import json
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from framerudiments.output_quality import OutputQuality
from framerudiments.quality_effects import linear_shutter_composite

MOMENTS = (10, 22, 35, 48)
MODES = (
    ("default-maximum", "Default maximum quality", 4, 8, True),
    ("spatial-only-4x", "4× spatial, one time sample", 4, 1, True),
    ("temporal-only-8x", "8× time, native spatial", 1, 8, True),
    ("spatial-2x-temporal-4x", "Lowered 2× spatial + 4× time", 2, 4, True),
    ("spatial-4x-temporal-4x", "4× spatial + 4× time", 4, 4, True),
    ("temporal-4x-only", "Four source-time samples", 1, 4, True),
    ("single-sample-with-aa", "Single sample and edge AA", 1, 1, True),
    ("explicit-pixel-art", "Both disabled (original pixels)", 1, 1, False),
)


def _font(size):
    choices = (
        os.environ.get("FRAMERUDIMENTS_TEST_FONT"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    )
    for path in choices:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError("No test font: set FRAMERUDIMENTS_TEST_FONT")


def _scene(time, spatial):
    w, h = 480, 280
    actual = Image.new("RGBA", (w * spatial, h * spatial), "#0b1729")
    d = ImageDraw.Draw(actual)
    s = spatial
    # Moving vector object with a true subpixel centre; not a raster translation.
    cx = (140 + 75 * math.sin(time * 4.5)) * s
    cy = (154 + 32 * math.cos(time * 5.9)) * s
    d.polygon(
        (
            (cx - 55 * s, cy - 20 * s),
            (cx + 45 * s, cy - 55 * s),
            (cx + 60 * s, cy + 43 * s),
            (cx - 26 * s, cy + 54 * s),
        ),
        fill="#ffc566",
    )
    d.line(
        [(8 * s, (205 + 20 * math.sin(time * 6)) * s), (468 * s, (130 - 14 * math.sin(time * 3)) * s)],
        fill="#39dfeb",
        width=max(1, 2 * s),
    )
    for i in range(15):
        x = (283 + i * 11) * s
        y = (222 - (15 + i * 3 + 34 * math.sin(i * 0.4 + time * 9))) * s
        d.rectangle((x, y, x + 6 * s, 243 * s), fill="#72c8ff")
    # FreeType hinting changes between sizes: font outlines genuinely rendered
    # above output resolution (not enlarged afterward).
    d.text(
        (22 * s, 21 * s),
        "VECTOR + TIME",
        font=_font(30 * s),
        fill="#e9f2ff",
        stroke_width=max(1, s),
        stroke_fill="#1e8195",
    )
    d.text((22 * s, 250 * s), f"sample {time:05.3f}s", font=_font(15 * s), fill="#9dd5d9")
    return (actual.resize((w, h), Image.Resampling.LANCZOS) if spatial > 1 else actual).convert("RGB")


def _render(frame, spatial, temporal, enabled):
    samples = [_scene((frame + j / temporal) / 60, spatial) for j in range(temporal)]
    combined = linear_shutter_composite(samples, [1] * temporal, strength=1)
    config = {
        "enabled": enabled,
        "spatial_scale": spatial,
        "temporal_samples": temporal,
        "antialias": 1,
        "antialias_passes": 3,
    }
    return OutputQuality(config).apply(combined)


def build_site(site):
    site = Path(site)
    home = site / "index.html"
    if not home.is_file():
        raise ValueError("Generate the main visualisation gallery first")
    root = site / "supersampling"
    root.mkdir(parents=True, exist_ok=False)
    for name, _, spatial, temporal, enabled in MODES:
        folder = root / name
        folder.mkdir()
        for index, frame in enumerate(MOMENTS):
            _render(frame, spatial, temporal, enabled).save(folder / f"{index}.png")
    catalogue = dict(
        schema="framerudiments.synthetic-spatiotemporal-gallery.v1",
        synthetic=True,
        measured_audio=False,
        frames=list(MOMENTS),
        default=dict(enabled=True, spatial_scale=4, temporal_samples=8, antialias_passes=3),
        scenes=[
            dict(name=name, label=label, spatial_scale=spatial, temporal_samples=temporal, enabled=enabled)
            for name, label, spatial, temporal, enabled in MODES
        ],
    )
    (root / "catalogue.json").write_text(json.dumps(catalogue, indent=2) + "\n", encoding="utf-8")
    cards = "\n".join(
        f'<article><img alt="{label}" data-name="{name}" src="{name}/0.png">'
        f"<h2>{label}</h2><p>Spatial {spatial}× · Temporal {temporal}×"
        f" · {'AA on' if enabled else 'AA off'}</p></article>"
        for name, label, spatial, temporal, enabled in MODES
    )
    page = (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FrameRudiments: Spatiotemporal Supersampling</title><style>
:root{color-scheme:dark}body{background:#081225;color:#eaf7ff;font:16px system-ui;max-width:1450px;padding:30px;margin:auto}a{color:#5ae3d7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:20px}article{background:#14253e;border:1px solid #365878;border-radius:12px;padding:16px}img{display:block;width:100%;border-radius:8px}p{color:#b1cadb}input{width:260px;accent-color:#5ae3d7}</style></head><body>
<a href="../index.html">← All visualisations</a><h1>Source-level spatial &amp; temporal supersampling</h1><p>The maximum-quality setting is the FrameRudiments default. Each comparison uses the same synthetic source-time motion curve with genuine source geometry rasterised at the specified spatial factor and a different number of fractional-timeline samples; this does not represent measured audio or pixel reconstruction of a raster sprite.</p>
<label for="moment">Motion position <input id="moment" type="range" min="0" max="3" step="1" value="0"></label> <a href="catalogue.json">Configuration catalogue</a>
<div class="grid">"""
        + cards
        + """</div><script>document.getElementById('moment').addEventListener('input',e=>document.querySelectorAll('img[data-name]').forEach(img=>img.src=img.dataset.name+'/'+e.target.value+'.png'))</script></body></html>"""
    )
    (root / "index.html").write_text(page, encoding="utf-8")
    source = home.read_text(encoding="utf-8")
    link = (
        '<p><a href="supersampling/index.html">High-resolution spatial and temporal supersampling →</a></p>'
    )
    if link not in source:
        home.write_text(source.replace("</header>", link + "</header>"), encoding="utf-8")
    return catalogue


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"Generated {len(build_site(args.output)['scenes'])} supersampling scenes")
