"""Render all five fire routes and ice using public-safe synthetic cards and tone."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from framerudiments import FireRenderer, NaturalMaterial, Project, render
from framerudiments.demo import create_demo
from framerudiments.io import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--ffmpeg", required=True)
    args = parser.parse_args()
    path = create_demo(args.output / "inputs", args.font, width=1440, height=900)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(render_quality={"enabled": False}, layers=[], identity=[], fps=60)
    for key in ("captions", "lyrics", "canonical_lyrics"):
        data.pop(key, None)
    write_json(path, data)
    project = Project.load(path)
    methods = ("footage", "sprites", "fluid_2d", "volume", "mantaflow")
    materials = [
        FireRenderer(
            (448, 260),
            method,
            resolution=0.75,
            seed=21,
            glow=0.1,
            warmth=1 if method in ("footage", "sprites") else 0,
            flame_height=0.85,
        )
        for method in methods
    ]
    materials.append(NaturalMaterial((448, 260), "ice", resolution=0.75, seed=21))
    labels = (
        "FILMED FLAME",
        "ANIMATED SPRITES",
        "2D BUOYANT FLUID",
        "3D VOLUME RAYS",
        "BLENDER MANTAFLOW",
        "FRACTURED ICE",
    )
    notes = (
        "CC0 recording; warm colour grade",
        "Real flame frames; particle age clock",
        "Fixed-step visual fluid model",
        "Procedural density; GPU ray integration",
        "Actual baked gas simulation",
        "Procedural frost and fractures",
    )
    font = ImageFont.truetype(str(args.font), 25)
    small = ImageFont.truetype(str(args.font), 18)
    paper = Image.new("RGBA", (448, 260))
    draw = ImageDraw.Draw(paper)
    draw.rounded_rectangle((8, 150, 440, 250), radius=12, fill="#4b2343", outline="#e5a57b", width=2)
    draw.text((42, 186), "FRAME-ADDRESSED BURN", fill="#ffe8bd", font=font)
    base = Image.new("RGBA", (1440, 900), "#0c0710")
    d = ImageDraw.Draw(base)
    d.text((18, 12), "FrameRudiments: five fire methods + ice", font=font, fill="#ffe8bd")
    for n, (label, note) in enumerate(zip(labels, notes)):
        x, y = (n % 3) * 480 + 16, (n // 3) * 410 + 60
        d.rounded_rectangle((x - 2, y, x + 450, y + 388), radius=12, outline="#543345", width=1)
        d.text((x + 10, y + 12), label, font=font, fill="#ffe8bd")
        d.text((x + 10, y + 48), note, font=small, fill="#c6bbc6")
    poster = False

    def scene(image, context):
        nonlocal poster
        image.paste(base)
        t = context.output_seconds
        p = max(0, min(1, (t - 1.8) / 1.9))
        for n, material in enumerate(materials):
            x, y = (n % 3) * 480 + 16, (n // 3) * 410 + 140
            if t < 1.8:
                tile = material.render(t, amount=1)
            else:
                tile = material.freeze(paper, t, p) if n == 5 else material.burn(paper, t, p)
            image.alpha_composite(tile, (x, y))
        if not poster and t >= 1.4:
            image.convert("RGB").save(args.output / "comparison.png")
            poster = True
        return image

    try:
        render(project, args.output / "render", scene=scene, ffmpeg=args.ffmpeg, preset="veryfast")
    finally:
        for material in materials:
            if hasattr(material, "close"):
                material.close()
        write_json(args.output / "methods.json", [m.evidence for m in materials])


if __name__ == "__main__":
    main()
