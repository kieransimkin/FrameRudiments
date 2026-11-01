"""Render deterministic M6.4 test cards; no private assets or downloaded fonts."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph import EffectGraph


def providers():
    size = (240, 160)
    source = Image.new('RGBA', size)
    draw = ImageDraw.Draw(source)
    draw.rounded_rectangle((18, 18, 222, 142), radius=24, fill=(140, 170, 205, 210))
    draw.ellipse((46, 36, 126, 116), fill=(55, 120, 160, 255))
    draw.ellipse((67, 57, 105, 95), fill=(0, 0, 0, 0))
    draw.polygon([(151, 33), (211, 109), (132, 125)], fill=(210, 80, 95, 160))
    tile = Image.new('RGBA', (48, 48))
    td = ImageDraw.Draw(tile)
    td.ellipse((3, 3, 45, 45), fill=(20, 220, 255, 220))
    td.ellipse((11, 11, 37, 37), fill=(255, 60, 160, 130))
    td.line((0, 0, 48, 48), fill=(255, 220, 80, 255), width=3)
    return source, tile


def cases():
    stops = [{'id': 'cold', 'position': 0, 'color': '#18d7ff'},
             {'id': 'mid', 'position': .5, 'color': '#ff248e'},
             {'id': 'hot', 'position': 1, 'color': '#ffe261'}]
    return [
        ('Source (soft alpha + hole)', None),
        ('Colour / normal', dict(style_type='color_overlay', style_color='#ffc93d', style_strength=.8)),
        ('Colour / multiply', dict(style_type='color_overlay', style_color='#40eaff', style_blend='multiply')),
        ('Colour / soft light', dict(style_type='color_overlay', style_color='#ff407a', style_blend='soft_light')),
        *[(f'Gradient / {kind}', dict(style_type='gradient_overlay', gradient_kind=kind,
                                     gradient_stops=stops, gradient_space='oklab', gradient_angle=25))
          for kind in ('linear', 'radial', 'angular', 'reflected', 'diamond')],
        *[(f'Pattern / {kind}', dict(style_type='pattern_overlay', pattern_kind=kind,
                                    style_color='#f9d55c', pattern_background='#7439c7',
                                    pattern_scale=[18, 18], pattern_angle=20, pattern_duty=.55))
          for kind in ('checker', 'stripes', 'dots', 'grid')],
        ('Live tile / mirrored', dict(style_type='pattern_overlay', pattern_kind='texture',
                                      pattern_scale=[48, 48], pattern_angle=15, pattern_wrap='mirror')),
        ('Gradient / effect-only', dict(style_type='gradient_overlay', gradient_stops=stops,
                                        style_output='effect', style_strength=.7)),
        ('Pattern + original / screen', dict(style_type='pattern_overlay', pattern_kind='grid',
                                            pattern_scale=[14, 14], pattern_background_opacity=0,
                                            style_color='#00eaff', style_output='effect')),
    ]


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    source, tile = providers()
    cell_w, cell_h, columns = 260, 198, 4
    sheet = Image.new('RGB', (cell_w*columns, cell_h*4+42), '#101522')
    drawing = ImageDraw.Draw(sheet)
    drawing.text((16, 12), 'FrameRudiments M6.4 | actual CPU graph outputs', fill='#eef4ff')
    records = []
    for index, (label, style) in enumerate(cases()):
        nodes = [{'id': 's', 'type': 'source'}]
        out = 's'
        if style is not None:
            inputs = ['s']
            if style.get('pattern_kind') == 'texture':
                nodes.append({'id': 'tile', 'type': 'source'})
                inputs.append('tile')
            nodes.append(dict(id='paint', type='layer_style', inputs=inputs, **style))
            out = 'paint'
            if index == 15:
                nodes.append(dict(id='mix', type='composite', inputs=['s', 'paint'], mode='screen'))
                out = 'mix'
        image = EffectGraph({'nodes': nodes, 'output': out}).render({'s': source, 'tile': tile})
        name = f'overlay-{index:02}.png'
        image.save(output/name)
        checker = Image.new('RGBA', source.size, '#1b2536')
        cd = ImageDraw.Draw(checker)
        for y in range(0, source.height, 10):
            for x in range(0, source.width, 10):
                if (x//10+y//10) % 2:
                    cd.rectangle((x, y, x+9, y+9), fill='#243247')
        checker.alpha_composite(image)
        x, y = (index % columns)*cell_w+10, (index//columns)*cell_h+42
        sheet.paste(checker.convert('RGB'), (x, y+22))
        drawing.text((x, y+3), label, fill='#e3edf9')
        records.append(dict(file=name, label=label, config=style,
                            pixel_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
                            alpha_bbox=image.getchannel('A').getbbox()))
    sheet.save(output/'overlays-contact-sheet.png')
    manifest = dict(schema='framerudiments.overlay-qa.v1', source='synthetic RGBA geometry',
                    purpose='technical regression, not creative approval', cases=records)
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    build(args.output)
