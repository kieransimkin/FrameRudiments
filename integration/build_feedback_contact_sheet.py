"""Build a labelled documentation image from real-engine feedback smoke outputs."""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--butterchurn-dir", type=Path, required=True)
    parser.add_argument("--projectm-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    entries = []
    for engine, folder in (
        ("Butterchurn", args.butterchurn_dir),
        ("projectM", args.projectm_dir),
    ):
        for state, filename in (
            ("control", f"{engine.lower()}-control.png"),
            ("feedback writeback", f"{engine.lower()}-feedback-hook.png"),
            ("pixel difference", f"{engine.lower()}-difference.png"),
        ):
            path = folder / filename
            entries.append((engine, state, path, Image.open(path).convert("RGB")))

    scale = 3
    tile = (96 * scale, 64 * scale)
    margin, label_height, footer_height = 20, 42, 22
    sheet = Image.new(
        "RGB",
        (
            margin * 4 + tile[0] * 3,
            margin * 3 + (label_height + tile[1] + footer_height) * 2,
        ),
        "#0b1020",
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=16)
    small = ImageFont.load_default(size=13)
    for index, (engine, state, path, image) in enumerate(entries):
        row, column = divmod(index, 3)
        x = margin + column * (tile[0] + margin)
        y = margin + row * (tile[1] + label_height + footer_height + margin)
        draw.text((x, y), f"{engine} - {state}", font=font, fill="#f4f0e8")
        resized = image.resize(tile, Image.Resampling.NEAREST)
        sheet.paste(resized, (x, y + label_height))
        draw.rectangle((x, y + label_height, x + tile[0] - 1, y + label_height + tile[1] - 1), outline="#5ce1e6", width=2)
        draw.text((x, y + label_height + tile[1] + 3), path.name, font=small, fill="#9aa8c7")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output, optimize=True)
    receipt = {
        "schema": "framerudiments.feedback-contact-sheet.v1",
        "synthetic_input": True,
        "purpose": "Visual evidence of retained-feedback writeback in pinned real engines.",
        "output": str(args.output),
        "output_sha256": file_hash(args.output),
        "sources": [
            {
                "engine": engine,
                "state": state,
                "file": path.name,
                "sha256": file_hash(path),
            }
            for engine, state, path, _ in entries
        ],
    }
    args.output.with_suffix(".json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
