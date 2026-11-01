"""Create deterministic CPU-only chain/parallel transition QA evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph import EffectGraph  # noqa: E402

SIZE = (240, 135)


def sources():
    a = Image.new('RGBA', SIZE, (18, 32, 63, 255))
    b = Image.new('RGBA', SIZE, (14, 22, 47, 255))
    c = Image.new('RGBA', SIZE, (21, 15, 42, 255))
    draw = ImageDraw.Draw(a)
    draw.ellipse((35, 25, 127, 118), fill=(252, 115, 50, 235))
    draw.rectangle((158, 17, 208, 113), fill=(255, 204, 90, 240))
    draw = ImageDraw.Draw(b)
    for x in range(18, SIZE[0], 25):
        draw.rounded_rectangle((x, 15, min(x + 14, 239), 120), radius=5, fill=(46, 199, 229, 225))
    draw = ImageDraw.Draw(c)
    draw.polygon(((120, 10), (227, 112), (14, 113)), fill=(217, 65, 193, 245))
    draw.ellipse((85, 45, 153, 111), fill=(38, 34, 82, 210))
    return {'a': a, 'b': b, 'c': c}


def graph():
    return EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'c', 'type': 'source'},
        {'id': 'sequence', 'type': 'transition_chain', 'inputs': ['a', 'b', 'c'],
         'input_effects': [[], [{'id': 'shift', 'type': 'chromatic_displacement',
                                  'red_offset': [3, 0], 'blue_offset': [-3, 0]}], []],
         'stages': [
             {'id': 'intro', 'transition_style': 'liquid', 'progress': .3,
              'amplitude': 8, 'frequency': 3, 'feather': .1},
             {'id': 'outro', 'transition_style': 'rotate', 'progress': .6,
              'rotation': 130, 'strength': .65},
         ]},
        {'id': 'side', 'type': 'transition_chain', 'inputs': ['a', 'c'],
         'stages': [{'id': 'glow', 'transition_style': 'dissolve', 'progress': .5}],
         'opacity': .35},
        {'id': 'out', 'type': 'composite', 'inputs': ['sequence', 'side']},
    ], 'output': 'out'})


def generate(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    src = sources()
    stack = graph()
    progress = (0, .2, .4, .6, .8, 1)
    rows = (('intro only', False, 0), ('chained', True, 0), ('parallel blend', True, 1))
    sheet = Image.new('RGB', (SIZE[0] * len(progress), (SIZE[1] + 22) * len(rows)), '#111826')
    drawer = ImageDraw.Draw(sheet)
    hashes = {}
    for j, (label, use_outro, parallel) in enumerate(rows):
        for i, t in enumerate(progress):
            overrides = {
                ('sequence::stage::intro', 'progress'): t,
                ('sequence::stage::outro', 'progress'): t if use_outro else 0,
                ('side::stage::glow', 'progress'): t,
                ('side', 'opacity'): .35 if parallel else 0,
            }
            result = stack.render(src, parameter_values=overrides)
            image = Image.new('RGB', SIZE, '#111826')
            image.paste(result, mask=result.getchannel('A'))
            xy = (SIZE[0] * i, j * (SIZE[1] + 22) + 22)
            sheet.paste(image, xy)
            drawer.text((xy[0] + 6, xy[1] - 19), f'{label}  p={t:.1f}', fill='#eef7ff')
            hashes[f'{label}/{t:.1f}'] = hashlib.sha256(result.tobytes()).hexdigest()
    png = output / 'transition-chain-contact-sheet.png'
    sheet.save(png, optimize=False)
    manifest = {'width': SIZE[0], 'height': SIZE[1], 'progress': progress,
                'frames': hashes, 'contact_sheet_sha256': hashlib.sha256(png.read_bytes()).hexdigest(),
                'graph_nodes': len(stack.nodes), 'format': 'rgba8', 'backend': 'cpu'}
    (output / 'transition-chain-manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('qa/m5-4'))
    options = parser.parse_args()
    print(json.dumps(generate(options.output), indent=2, sort_keys=True))
