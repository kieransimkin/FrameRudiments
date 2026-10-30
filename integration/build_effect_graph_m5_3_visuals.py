"""Reproducible M5.3 geometry transition visual QA; no external media."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph_transition_geometry import geometric_transition  # noqa: E402

STYLES = ('zoom', 'rotate', 'polar', 'kaleidoscope', 'liquid')
STEPS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def sources(size=(144, 88)):
    first = Image.new('RGBA', size)
    second = Image.new('RGBA', size)
    p = ImageDraw.Draw(first)
    p.ellipse((12, 6, 104, 78), fill=(255, 105, 48, 230))
    p.rectangle((60, 38, 130, 79), fill=(255, 219, 70, 210))
    q = ImageDraw.Draw(second)
    q.polygon([(18, 66), (73, 6), (128, 70)], fill=(37, 195, 251, 220))
    q.ellipse((84, 10, 136, 62), fill=(176, 55, 245, 240))
    return first, second


def build(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    width, height = 144, 88
    header = 19
    sheet = Image.new('RGB', (width * len(STEPS), (height + header) * len(STYLES)), '#18212e')
    first, second = sources((width, height))
    hashes = {}
    for row, style in enumerate(STYLES):
        for col, progress in enumerate(STEPS):
            cfg = dict(transition_style=style, progress=progress, feather=.13,
                       strength=.82, amplitude=26, twist=1.15, rotation=235,
                       zoom=1.65, segments=8, frequency=3.2, phase=.8)
            frame = geometric_transition(first, second, cfg)
            matte = Image.new('RGBA', frame.size, '#132130')
            matte.alpha_composite(frame)
            sheet.paste(matte.convert('RGB'), (col * width, row * (height + header) + header))
            hashes[f'{style}-{progress:g}'] = hashlib.sha256(frame.tobytes()).hexdigest()
            ImageDraw.Draw(sheet).text((col * width + 6, row * (height + header) + 3),
                                       f'{style}  {progress:g}', fill='#fafafa')
    png = directory / 'geometric-transition-contact-sheet.png'
    sheet.save(png)
    manifest = {
        'schema': 'framerudiments.effect-graph.m5-3-visuals.v1',
        'source': 'synthetic', 'progress': list(STEPS), 'styles': list(STYLES),
        'frame_sha256': hashes,
        'contact_sheet_sha256': hashlib.sha256(png.read_bytes()).hexdigest(),
    }
    (directory / 'geometric-transition-manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('qa/geometric-transitions'))
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))
