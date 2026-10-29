"""Deterministic M5.1 matte-transition contact sheet plus SHA-256 evidence.

Run: python integration/build_effect_graph_m5_visuals.py --output m5-transitions.png
No external fonts, copyrighted images, private assets, or GPU required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph


WIDTH, HEIGHT = 160, 100
STYLES = ("alpha", "luminance", "chroma", "inverted")
PROGRESS = (.15, .5, .85)


def synthetic_sources():
    a = Image.new("RGBA", (WIDTH, HEIGHT), (13, 21, 45, 255))
    b = Image.new("RGBA", (WIDTH, HEIGHT), (19, 60, 120, 255))
    draw_a, draw_b = ImageDraw.Draw(a), ImageDraw.Draw(b)
    draw_a.rounded_rectangle((15, 12, 144, 88), radius=20, fill=(242, 68, 43, 255))
    draw_b.ellipse((10, 8, 150, 92), fill=(20, 215, 230, 220))
    for x in range(0, WIDTH, 12):
        draw_b.line((x, 0, WIDTH - x, HEIGHT), fill=(239, 236, 130, 180), width=2)
    alpha = Image.new("RGBA", (WIDTH, HEIGHT))
    luma = Image.new("RGBA", (WIDTH, HEIGHT))
    chroma = Image.new("RGBA", (WIDTH, HEIGHT), (250, 20, 50, 255))
    da, dl = ImageDraw.Draw(alpha), ImageDraw.Draw(luma)
    for x in range(WIDTH):
        ink = round(x * 255 / (WIDTH - 1))
        da.line((x, 0, x, HEIGHT), fill=(255, 255, 255, ink))
        dl.line((x, 0, x, HEIGHT), fill=(ink, ink, ink, 255))
    ImageDraw.Draw(chroma).ellipse((20, 8, 140, 94), fill=(0, 255, 0, 255))
    return a, b, {"alpha": alpha, "luminance": luma, "chroma": chroma, "inverted": alpha}


def build(output: Path):
    a, b, masks = synthetic_sources()
    pad, label_height = 7, 24
    total_width = len(PROGRESS) * (WIDTH + pad) + pad
    total_height = len(STYLES) * (HEIGHT + label_height + pad) + pad
    sheet = Image.new("RGB", (total_width, total_height), (23, 26, 36))
    draw = ImageDraw.Draw(sheet)
    evidence = []
    for row, style in enumerate(STYLES):
        for col, progress in enumerate(PROGRESS):
            config = {"transition_style": "matte", "progress": progress, "feather": .2}
            if style == "inverted":
                config["invert"] = True
            elif style == "chroma":
                config.update(matte_channel="chroma", key_color="#00ff00", tolerance=.05, softness=.05)
            else:
                config["matte_channel"] = style
            graph = EffectGraph({"nodes": [
                {"id": "a", "type": "source"}, {"id": "b", "type": "source"},
                {"id": "mask", "type": "source"},
                {"id": "out", "type": "transition", "inputs": ["a", "b", "mask"], **config},
            ], "output": "out"})
            frame = graph.render({"a": a, "b": b, "mask": masks[style]})
            x, y = pad + col * (WIDTH + pad), pad + row * (HEIGHT + label_height + pad)
            draw.text((x, y), f"{style}  progress {progress:.2f}", fill="white")
            sheet.paste(frame.convert("RGB"), (x, y + label_height))
            evidence.append({"style": style, "progress": progress,
                             "sha256": hashlib.sha256(frame.tobytes()).hexdigest(),
                             "alpha_sum": sum(frame.getchannel("A").tobytes())})
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    output.with_suffix(".json").write_text(json.dumps({
        "schema": "framerudiments.m5.1.evidence.v1", "width": WIDTH,
        "height": HEIGHT, "samples": evidence,
    }, indent=2) + "\n", encoding="utf-8")
    return sheet, evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("m5-matte-transitions.png"))
    args = parser.parse_args()
    _, rows = build(args.output)
    print(f"Wrote {args.output} with {len(rows)} deterministic samples")


if __name__ == "__main__":
    main()
