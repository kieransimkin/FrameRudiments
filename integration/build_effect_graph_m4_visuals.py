"""Deterministic, synthetic M4 spectral-displacement visual evidence generator.

No song, private media, font, model, GPU or network access required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

from framerudiments.effect_graph_spectral import spectral_displacement


def source_image(size=(320, 180)):
    im = Image.new("RGBA", size)
    draw = ImageDraw.Draw(im)
    draw.ellipse((35, 20, 150, 140), fill=(240, 40, 170, 255))
    draw.rounded_rectangle((130, 45, 245, 155), radius=18, fill=(25, 190, 240, 220))
    draw.polygon([(140, 10), (200, 10), (170, 75)], fill=(255, 245, 225, 255))
    return im


def scenarios():
    pink = {"id": "pink", "source_color": "#f028aa", "space": "rgb",
            "tolerance": .04, "softness": .10, "offset": [-18, 10]}
    cyan = {"id": "cyan", "source_color": "#19bef0", "space": "oklab",
            "tolerance": .07, "softness": .10, "offset": [15, -8]}
    return [
        ("Input", None),
        ("Spectral shifts", {"bands": [pink, cyan]}),
        ("Moving selected ink", {"bands": [pink, cyan], "spectral_mode": "replace"}),
        ("Selective recolouring", {"bands": [{**pink, "target_color": "#ffe535", "recolor": 1}, cyan]}),
        ("Original alpha clip", {"bands": [pink, cyan], "alpha_mode": "source"}),
        ("Soft mixed displacement", {"bands": [pink, cyan], "mix": .5}),
    ]


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    src = source_image()
    original = src.tobytes()
    width, height = src.size
    padding = 10
    caption_height = 25
    panel_height = height + caption_height
    sheet = Image.new("RGB", (3 * width + 4 * padding, 2 * panel_height + 3 * padding), "#111827")
    manifest = {"schema": "framerudiments.spectral.visual-evidence.v1", "cases": []}
    for i, (label, config) in enumerate(scenarios()):
        result = src.copy() if config is None else spectral_displacement(src, config)
        x = padding + (i % 3) * (width + padding)
        y = padding + (i // 3) * (panel_height + padding)
        backdrop = Image.new("RGBA", src.size, "#253248")
        backdrop.alpha_composite(result)
        sheet.paste(backdrop.convert("RGB"), (x, y + caption_height))
        ImageDraw.Draw(sheet).text((x + 6, y + 6), label, fill="#ffffff")
        manifest["cases"].append({
            "name": label,
            "sha256": hashlib.sha256(result.tobytes()).hexdigest(),
            "alpha_total": sum(index * count for index, count in enumerate(result.getchannel("A").histogram())),
        })
    if src.tobytes() != original:
        raise AssertionError("source mutation in spectral QA")
    sheet.save(output / "spectral-contact-sheet.png")
    (output / "spectral-evidence.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.output)
