"""Deterministic synthetic QA for M6 alpha-derived styles, not creative approval."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph import EffectGraph


def source_image(size=(240, 180)):
    scale = 3
    high = Image.new('RGBA', (size[0]*scale, size[1]*scale))
    draw = ImageDraw.Draw(high)
    def box(coords):
        return tuple(round(x*scale) for x in coords)
    draw.rounded_rectangle(box((48, 36, 191, 143)), radius=18*scale, fill=(229, 116, 42, 235))
    draw.ellipse(box((79, 58, 133, 112)), fill=(0, 0, 0, 0))
    draw.polygon([(x*scale, y*scale) for x, y in ((159, 52), (178, 95), (145, 108))],
                 fill=(66, 210, 231, 120))
    return high.resize(size, Image.Resampling.LANCZOS)


def _checker(size):
    image = Image.new('RGBA', size, (34, 36, 42, 255))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], 12):
        for x in range(0, size[0], 12):
            if (x//12 + y//12) % 2:
                draw.rectangle((x, y, x+11, y+11), fill=(46, 49, 56, 255))
    return image


def cases():
    return [
        ('Source / soft alpha + hole', None),
        ('Drop shadow / fractional XY', dict(style_type='drop_shadow', style_offset=[10.5, 8.25],
                                          style_blur=5, style_spread=2, style_color='#030710', style_strength=.8)),
        ('Shadow only / fork output', dict(style_type='drop_shadow', style_offset=[10.5, 8.25],
                                         style_blur=4, style_output='effect', style_color='#b1beff')),
        ('Inner shadow / alpha preserved', dict(style_type='inner_shadow', style_offset=[5, 4],
                                              style_blur=3, style_color='#30143c')),
        ('Outer glow / cyan', dict(style_type='outer_glow', style_spread=3, style_blur=5,
                                  style_color='#17e6ff')),
        ('Glow only / fork output', dict(style_type='outer_glow', style_spread=3, style_blur=5,
                                        style_color='#17e6ff', style_output='effect')),
        ('Inner glow / soft highlight', dict(style_type='inner_glow', style_spread=3, style_blur=3,
                                            style_color='#fff2a8')),
        ('Outer outline / 5 px', dict(style_type='outline', style_width=5, style_color='#20e1ff')),
        ('Inner outline / 5 px', dict(style_type='outline', style_width=5,
                                     outline_position='inner', style_color='#ffeba0')),
        ('Centred outline / 6 px', dict(style_type='outline', style_width=6,
                                       outline_position='center', style_color='#f653d6')),
        ('No fill / inner edge only', dict(style_type='outline', style_width=4,
                                           outline_position='inner', fill_opacity=0, style_color='#fff0ad')),
        ('Three branches / original alpha', 'stack'),
    ]


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    image = source_image()
    font = ImageFont.load_default()
    tile_width, tile_height = image.width, image.height+26
    sheet = Image.new('RGB', (tile_width*4, tile_height*3+38), (21, 23, 29))
    draw = ImageDraw.Draw(sheet)
    draw.text((12, 12), 'M6 CPU alpha-style test renders | synthetic assets | no GPU or creative approval implied',
              fill=(226, 231, 238), font=font)
    records = []
    for index, (label, config) in enumerate(cases()):
        nodes = [{'id': 's', 'type': 'source'}]
        selected = 's'
        if config == 'stack':
            nodes.extend([
                {'id': 'shadow', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'drop_shadow',
                 'style_color': '#08091d', 'style_offset': [9, 9], 'style_blur': 3, 'style_output': 'effect'},
                {'id': 'glow', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'outer_glow',
                 'style_color': '#ae37ff', 'style_spread': 4, 'style_blur': 7, 'style_output': 'effect'},
                {'id': 'line', 'type': 'layer_style', 'inputs': ['s'], 'style_type': 'outline',
                 'style_color': '#fff0b8', 'style_width': 1.5},
                {'id': 'out', 'type': 'composite', 'inputs': ['shadow', 'glow', 'line']},
            ])
            selected = 'out'
        elif config is not None:
            nodes.append({'id': 'out', 'type': 'layer_style', 'inputs': ['s'], **config})
            selected = 'out'
        rendered = EffectGraph({'nodes': nodes, 'output': selected}).render({'s': image})
        name = f'style-{index:02d}.png'
        rendered.save(output/name)
        preview = Image.alpha_composite(_checker(image.size), rendered).convert('RGB')
        x, y = (index % 4)*tile_width, 38+(index//4)*tile_height
        sheet.paste(preview, (x, y))
        draw.text((x+8, y+image.height+6), label, fill=(224, 229, 237), font=font)
        records.append(dict(label=label, file=name, pixel_sha256=hashlib.sha256(rendered.tobytes()).hexdigest(),
                            alpha_bounds=rendered.getchannel('A').getbbox(), graph={'nodes': nodes, 'output': selected}))
    sheet.save(output/'alpha-styles-contact-sheet.png')
    manifest = dict(schema='framerudiments.m6.qa.v1', backend='cpu', pillow=pillow_version,
                    inputs='synthetic, generated here; no private artwork or fonts',
                    creative_approval=False, frames=records,
                    sheet_sha256=hashlib.sha256(sheet.tobytes()).hexdigest())
    (output/'alpha-styles-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.output)
