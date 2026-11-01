"""Deterministic synthetic QA for M6.3. No private artwork or font redistribution."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, __version__ as pillow_version

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from framerudiments.effect_graph_layer_styles import render_layer_style


def source():
    scale = 3
    image = Image.new('RGBA', (288 * scale, 204 * scale))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((30*scale, 28*scale, 152*scale, 154*scale),
                           radius=24*scale, fill='#397cb2')
    draw.rounded_rectangle((59*scale, 57*scale, 123*scale, 125*scale),
                           radius=12*scale, fill=(0, 0, 0, 0))
    draw.polygon([(x*scale, y*scale) for x, y in [(175, 28), (247, 28), (247, 58),
                  (205, 58), (205, 80), (238, 80), (238, 109), (205, 109),
                  (205, 154), (175, 154)]], fill='#52a4d2')
    return image.resize((288, 204), Image.Resampling.LANCZOS)


def cases():
    return [
        ('Source', {}, 'Original RGBA silhouette'),
        ('Bevel / raised', dict(style_type='bevel', style_width=9, bevel_depth=11,
                               light_elevation=25, highlight_strength=1), 'Distance ramp; light 225 degrees'),
        ('Bevel / recessed', dict(style_type='bevel', style_width=9, bevel_depth=11,
                                 light_elevation=25, bevel_direction='down'), 'Same silhouette; inverted relief'),
        ('Emboss / ridge', dict(style_type='emboss', style_width=13, bevel_depth=12,
                               light_elevation=20, highlight_strength=1), 'Two-sided interior ridge'),
        ('Directional rim', dict(style_type='edge_light', style_width=6, light_angle=225,
                                light_elevation=0, highlight_color='#ffe090', highlight_strength=1),
         'Outer edges and holes; one light direction'),
        ('Satin', dict(style_type='satin', style_offset=[9.5, 7.25], style_blur=6,
                       style_color='#e6c6ff', style_strength=.85), 'Soft displaced-alpha interference'),
        ('Extrusion', dict(style_type='extrusion', extrude_length=27.5, extrude_angle=40,
                           style_color='#f47c20', extrude_color='#301860'), 'Nearest-coverage sweep; two colours'),
        ('Long shadow', dict(style_type='long_shadow', extrude_length=62, extrude_angle=35,
                             style_color='#060914', extrude_fade=.7), 'Constant colour; fading depth'),
        ('Bevel / effect only', dict(style_type='bevel', style_width=11, bevel_depth=12,
                                     light_elevation=0, style_output='effect'), 'Reusable highlight and shadow branch'),
        ('Emboss / mirrored light', dict(style_type='emboss', style_width=13, bevel_depth=12,
                                         light_angle=45, light_elevation=20), 'Same ridge; light from opposite side'),
        ('Inner rim / soft source', dict(style_type='edge_light', style_width=5,
                                         outline_position='inner', light_elevation=0,
                                         highlight_color='#fff0c0'), '50% alpha; interior coverage preserved'),
        ('Stacked depth + relief', dict(style_type='bevel', style_width=8,
                                        bevel_depth=10, light_elevation=20), 'Independent style passes, same RGBA contract'),
    ]


def build(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    original = source()
    tile_size = (320, 264)
    sheet = Image.new('RGB', (3*tile_size[0], 4*tile_size[1]), '#111721')
    records = []
    for index, (title, config, caption) in enumerate(cases()):
        image = original.copy()
        if index == 10:
            image.putalpha(image.getchannel('A').point(lambda value: round(value*.5)))
        if index == 11:
            body = render_layer_style(image, config)
            result = render_layer_style(body, dict(style_type='extrusion', extrude_length=20,
                                                   extrude_angle=42, style_color='#222753',
                                                   extrude_color='#060913'))
        else:
            result = render_layer_style(image, config) if config else image.copy()
        name = f'relief-{index:02}.png'
        result.save(directory / name)
        tile = Image.new('RGBA', tile_size, '#182331')
        draw = ImageDraw.Draw(tile)
        # Checkerboard makes genuine alpha/effect-only output easy to inspect.
        for y in range(0, 204, 12):
            for x in range(0, 288, 12):
                draw.rectangle((16+x, 36+y, 27+x, 47+y), fill=('#273241' if (x//12+y//12)%2 else '#202a38'))
        tile.alpha_composite(result, (16, 36))
        draw.text((12, 8), title, fill='#f8e8bd')
        draw.text((12, 247), caption, fill='#d5e1ee')
        sheet.paste(tile.convert('RGB'), ((index % 3)*320, (index // 3)*264))
        records.append({'title': title, 'config': config, 'file': name,
                        'rgba_sha256': hashlib.sha256(result.tobytes()).hexdigest()})
    sheet_path = directory / 'relief-contact-sheet.png'
    sheet.save(sheet_path)
    manifest = {'schema': 'framerudiments.m6.3.qa.v1', 'synthetic': True,
                'pillow_version': pillow_version, 'cases': records,
                'sheet_sha256': hashlib.sha256(sheet_path.read_bytes()).hexdigest()}
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.output)
