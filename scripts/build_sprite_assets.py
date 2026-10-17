"""Regenerate 16 deterministic procedural sprite atlases (not the image-generated source).

Sprite artwork is bundled, not fetched when a project runs. Each atlas has a
strict 8x8 grid of 96px RGBA cells; row indicates sprite silhouette and column
is a colour variant. Pixel placement is source-clock-independent.
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
from pathlib import Path
import math

from PIL import Image, ImageDraw, ImageFilter

CELL = 96
COLUMNS = 8
ROWS = 8
THEMES = (
    "lcd",
    "lcd-green",
    "lcd-amber",
    "led",
    "vfd",
    "arcade",
    "neon",
    "glass",
    "metal",
    "crystal",
    "pixel",
    "hud",
    "plasma",
    "fire",
    "ice",
    "mono",
)
HUES = (0.53, 0.07, 0.29, 0.78, 0.95, 0.15, 0.44, 0.66)


def colour(hue, theme):
    if theme == "mono":
        return (210, 225, 232)
    if theme == "lcd-green":
        hue = 0.32 + (hue - 0.5) * 0.10
    if theme == "lcd-amber" or theme == "fire":
        hue = 0.065 + (hue - 0.5) * 0.08
    if theme == "ice":
        hue = 0.54 + (hue - 0.5) * 0.1
    saturation = 0.58 if theme in {"metal", "glass", "lcd", "hud"} else 0.92
    return tuple(round(v * 255) for v in colorsys.hsv_to_rgb(hue % 1, saturation, 1))


def render_cell(row, column, theme):
    rgba = Image.new("RGBA", (CELL, CELL))
    glow = Image.new("RGBA", rgba.size)
    base = colour(HUES[column], theme)
    ink = (*base, 235)
    edge = tuple(min(255, int(c * 0.55 + 110)) for c in base) + (255,)
    d = ImageDraw.Draw(rgba)
    g = ImageDraw.Draw(glow)
    faceted = theme in {"metal", "crystal", "glass"}
    segmented = theme in {"lcd", "lcd-green", "lcd-amber", "pixel", "arcade"}
    if row == 0:  # Horizontal LCD cell, used by histogram bar stacks.
        if segmented:
            d.rounded_rectangle((11, 33, 85, 63), radius=3, fill=(*base, 225), outline=edge, width=2)
            for x in range(16, 83, 10):
                d.line((x, 36, x, 60), fill=(15, 30, 47, 100), width=2)
        elif faceted:
            d.polygon([(14, 30), (76, 30), (86, 39), (80, 68), (16, 68), (8, 42)], fill=ink)
            d.line((16, 34, 74, 34), fill=edge, width=5)
        else:
            d.rounded_rectangle((12, 31, 84, 65), radius=15, fill=ink, outline=edge, width=2)
            d.line((24, 37, 72, 37), fill=edge, width=3)
        g.rounded_rectangle((14, 32, 82, 65), radius=11, fill=(*base, 138))
    elif row == 1:  # LED/dot/capsule
        radius = 18 if segmented else 25
        d.ellipse((48 - radius, 48 - radius, 48 + radius, 48 + radius), fill=ink, outline=edge, width=3)
        d.ellipse((35, 31, 50, 41), fill=(255, 255, 255, 115))
        g.ellipse((25, 25, 71, 71), fill=(*base, 180))
    elif row == 2:  # Vertical glass tube / solid bar
        if theme in {"pixel", "arcade"}:
            d.rectangle((33, 8, 63, 88), fill=ink)
            for y in range(13, 86, 11):
                d.line((34, y, 62, y), fill=(14, 20, 44, 125), width=2)
        else:
            d.rounded_rectangle((30, 8, 66, 88), radius=14, fill=ink, outline=edge, width=2)
            d.line((39, 15, 39, 80), fill=(245, 255, 255, 170), width=4)
        g.rounded_rectangle((29, 14, 68, 85), radius=17, fill=(*base, 165))
    elif row == 3:  # Diamond/crystal polygon
        pts = [(48, 6), (87, 46), (48, 90), (9, 46)]
        d.polygon(pts, fill=ink, outline=edge, width=3)
        d.line([(9, 46), (48, 55), (48, 6), (87, 46), (48, 55), (48, 90)], fill=(*base, 210), width=3)
        d.line((48, 6, 32, 42, 48, 55, 65, 41, 48, 6), fill=(245, 255, 255, 150), width=2)
        g.ellipse((20, 21, 77, 76), fill=(*base, 100))
    elif row == 4:  # Small gaming icon/star/rune
        pts = []
        for j in range(16):
            angle = 2 * math.pi * j / 16 - math.pi / 2
            length = 38 if j % 2 == 0 else 18
            pts.append((48 + length * math.cos(angle), 48 + length * math.sin(angle)))
        d.polygon(pts, fill=ink, outline=edge, width=2)
        g.ellipse((25, 25, 71, 71), fill=(*base, 160))
    elif row == 5:  # Wave spark / photon
        d.ellipse((26, 26, 70, 70), fill=ink)
        d.line((48, 7, 48, 89), fill=(*base, 210), width=5)
        d.line((7, 48, 89, 48), fill=(*base, 210), width=5)
        d.ellipse((40, 40, 56, 56), fill=(255, 255, 255, 225))
        g.ellipse((12, 12, 84, 84), fill=(*base, 160))
    elif row == 6:  # Pixel/Meter block with crisp border
        d.rounded_rectangle((14, 15, 82, 81), radius=0 if segmented else 6, fill=ink, outline=edge, width=3)
        for x in range(20, 80, 17):
            d.line((x, 20, x, 77), fill=(6, 14, 34, 95), width=2)
        for y in range(20, 80, 17):
            d.line((19, y, 78, y), fill=(6, 14, 34, 95), width=2)
        g.rectangle((16, 16, 79, 78), fill=(*base, 110))
    else:  # Fire/petal silhouette
        tips = (
            (47, 7),
            (59, 27),
            (70, 15),
            (73, 44),
            (84, 46),
            (72, 81),
            (50, 89),
            (25, 80),
            (15, 45),
            (29, 34),
            (31, 15),
        )
        d.polygon(tips, fill=ink, outline=edge, width=2)
        d.polygon(((48, 26), (60, 54), (48, 80), (36, 52)), fill=(255, 252, 196, 150))
        g.ellipse((13, 15, 84, 82), fill=(*base, 158))
    bloom = glow.filter(ImageFilter.GaussianBlur(9 if theme not in {"pixel", "metal", "lcd"} else 4))
    combined = Image.alpha_composite(bloom, rgba)
    if theme == "pixel":
        combined = combined.resize((32, 32), Image.Resampling.NEAREST).resize(
            (CELL, CELL), Image.Resampling.NEAREST
        )
    return combined


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create(output, generated=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "framerudiments.sprite-atlas.v1",
        "tile_size": CELL,
        "columns": COLUMNS,
        "rows": ROWS,
        "rows_meaning": [
            "horizontal-segment",
            "led-dot",
            "vertical-tube",
            "crystal",
            "star-icon",
            "photon",
            "pixel-block",
            "flame-petal",
        ],
        "themes": {},
    }
    for theme in THEMES:
        sheet = Image.new("RGBA", (COLUMNS * CELL, ROWS * CELL))
        for row in range(ROWS):
            for column in range(COLUMNS):
                sheet.alpha_composite(render_cell(row, column, theme), (column * CELL, row * CELL))
        path = output / (theme + ".png")
        sheet.save(path, optimize=True)
        manifest["themes"][theme] = {"file": path.name, "sha256": sha(path), "generated": False}
    if generated:
        art = Image.open(generated).convert("RGBA")
        # The image-generated source has true transparency. Crop its 8x8 cell
        # illustrations, with no generated captions, into exact-size atlas cells.
        sheet = Image.new("RGBA", (COLUMNS * CELL, ROWS * CELL))
        for row in range(ROWS):
            for column in range(COLUMNS):
                crop = art.crop(
                    (
                        round(column * art.width / 8),
                        round(row * art.height / 8),
                        round((column + 1) * art.width / 8),
                        round((row + 1) * art.height / 8),
                    )
                )
                box = crop.getchannel("A").getbbox()
                if box:
                    crop = crop.crop(box)
                crop.thumbnail((CELL - 8, CELL - 8), Image.Resampling.LANCZOS)
                sheet.alpha_composite(
                    crop, (column * CELL + (CELL - crop.width) // 2, row * CELL + (CELL - crop.height) // 2)
                )
        path = output / "generated-neon.png"
        sheet.save(path, optimize=True)
        manifest["themes"]["generated-neon"] = {
            "file": path.name,
            "sha256": sha(path),
            "generated": True,
            "source": "OpenAI image-generated transparent 8x8 sprite art (bundled)",
        }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generated", type=Path)
    args = parser.parse_args()
    manifest = create(args.output, args.generated)
    print(f"Built {len(manifest['themes'])} sprite atlases with 64 cells each")
