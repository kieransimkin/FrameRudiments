"""Render deterministic, synthetic M3 effect-graph QA contact sheet.

Usage: python integration/build_effect_graph_m3_visuals.py --output /path/to/qa
No fonts, artist media, audio, ffmpeg or graphics device needed. Not a golden
comparison: the PNG + hash manifest are evidence for human/CI review.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from framerudiments.effect_graph import EffectGraph


def _graph(nodes, output):
    return EffectGraph({"nodes": nodes, "output": output})


def _fixture(size=(120, 88)):
    ink = Image.new("RGBA", size)
    draw = ImageDraw.Draw(ink)
    draw.ellipse((14, 12, 104, 80), fill=(243, 40, 79, 240))
    draw.rectangle((35, 29, 84, 57), fill=(0, 240, 55, 235))
    mask = Image.new("RGBA", size)
    draw = ImageDraw.Draw(mask)
    draw.polygon([(0, 0), (size[0] - 1, 0), (90, 65), (22, 77)], fill="white")
    alt = Image.new("RGBA", size, (40, 66, 232, 245))
    return {"a": ink, "mask": mask, "b": alt}


def render_samples():
    sources = _fixture()
    base = [
        {"id": "a", "type": "source"},
        {"id": "mask", "type": "source"},
        {"id": "b", "type": "source"},
        {"id": "dim", "type": "copy", "inputs": ["a"], "opacity": 0.33},
    ]
    scenarios = {
        "current-alpha": base + [
            {"id": "out", "type": "matte", "inputs": ["dim", "mask"]},
        ],
        "original-alpha": base + [
            {"id": "out", "type": "matte", "inputs": ["dim", "mask"],
             "alpha_source": "original"},
        ],
        "key-current": base + [
            {"id": "out", "type": "chroma_key", "inputs": ["dim"], "tolerance": .08,
             "key_color": "#00f037", "softness": .02},
        ],
        "key-original": base + [
            {"id": "out", "type": "chroma_key", "inputs": ["dim"], "tolerance": .08,
             "key_color": "#00f037", "softness": .02, "alpha_source": "original"},
        ],
        "matte-before-transition": base + [
            {"id": "pre", "type": "matte", "inputs": ["a", "mask"]},
            {"id": "out", "type": "transition", "inputs": ["pre", "b"], "progress": .5},
        ],
        "matte-after-transition": base + [
            {"id": "mix", "type": "transition", "inputs": ["a", "b"], "progress": .5},
            {"id": "out", "type": "matte", "inputs": ["mix", "mask"]},
        ],
    }
    return {name: _graph(nodes, "out").render(sources) for name, nodes in scenarios.items()}


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results = render_samples()
    w, h = next(iter(results.values())).size
    title_height, margin = 22, 12
    sheet = Image.new("RGB", (w * 3 + margin * 4, (h + title_height) * 2 + margin * 3), "#171921")
    painter = ImageDraw.Draw(sheet)
    manifest = {"schema": "framerudiments.m3-visual-evidence.v1", "cases": {}}
    for i, (name, rgba) in enumerate(results.items()):
        x = margin + (i % 3) * (w + margin)
        y = margin + (i // 3) * (h + title_height + margin)
        rgb = Image.new("RGBA", rgba.size, "#263043")
        rgb.alpha_composite(rgba)
        sheet.paste(rgb.convert("RGB"), (x, y + title_height))
        painter.text((x, y + 3), name, font=ImageFont.load_default(), fill="white")
        manifest["cases"][name] = {
            "raw_rgba_sha256": sha256(rgba.tobytes()).hexdigest(),
            "alpha_sum": sum(value * count for value, count in enumerate(rgba.getchannel("A").histogram())),
            "center_rgba": rgba.getpixel((w // 2, h // 2)),
        }
    target = output / "effect-graph-m3-alpha-contact-sheet.png"
    sheet.save(target)
    manifest["contact_sheet_sha256"] = sha256(target.read_bytes()).hexdigest()
    (output / "effect-graph-m3-alpha-evidence.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.output))
