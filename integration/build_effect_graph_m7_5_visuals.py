"""Render actual M7.5 CPU QA surfaces; no model-generated illustrations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph import EffectGraph  # noqa: E402
from framerudiments.effect_graph_fields import render_procedural_field  # noqa: E402

PALETTE = [dict(id='cool', position=0, color='#10142c'),
           dict(id='mid', position=.48, color='#25c9c8'),
           dict(id='hot', position=1, color='#ffbd69')]


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    size = (240, 160)
    reference = Image.new('RGBA', size)
    draw = ImageDraw.Draw(reference)
    draw.rounded_rectangle((28, 26, 211, 135), radius=24, fill='#f4b76ac0')
    draw.ellipse((92, 58, 147, 112), fill=(0, 0, 0, 0))
    for x in range(32, 210, 16):
        draw.line((x, 31, x, 130), fill='#39d0c899', width=4)
    specs = [(kind, dict(field_type=kind, field_output='color', field_palette=PALETTE,
                        field_scale=[64, 48], field_phase=.13, field_seed=23))
             for kind in ('linear', 'radial', 'angular', 'stripes', 'checker', 'rings', 'noise', 'fbm', 'ridged', 'cellular')]
    specs += [('alpha / source coverage', dict(field_type='stripes', field_output='alpha',
                field_alpha_source='current', field_scale=[42, 42], field_angle=28)),
              ('gradient vector', dict(field_type='fbm', field_output='vector', field_vector='gradient',
                field_scale=[44, 44], field_seed=23, field_vector_gain=32)),
              ('curl vector', dict(field_type='fbm', field_output='vector', field_vector='curl',
                field_scale=[44, 44], field_seed=23, field_vector_gain=32))]
    records, images = [], []
    for index, (label, cfg) in enumerate(specs):
        image = render_procedural_field(reference, cfg)
        filename = f'{index:02d}-field.png'
        image.save(output/filename)
        records.append(dict(label=label, file=filename, config=cfg,
                            rgba_sha256=hashlib.sha256(image.tobytes()).hexdigest()))
        images.append(image)
    for label, nodes, selected in [
        ('noise matte', [dict(id='m', type='matte', inputs=['s', 'f'], matte_channel='luminance')], 'm'),
        ('curl displacement', [dict(id='w', type='spatial_warp', inputs=['s', 'f'], warp_type='displacement',
                                   warp_amplitude=[22, 22])], 'w'),
        ('generated texture', [dict(id='p', type='layer_style', inputs=['s', 'f'],
                                   style_type='pattern_overlay', pattern_kind='texture', pattern_scale=[240, 160])], 'p'),
    ]:
        cfg = dict(field_type='fbm', field_output='vector' if selected=='w' else 'color' if selected=='p' else 'grayscale',
                   field_scale=[36, 36], field_vector_gain=32, field_palette=PALETTE, field_seed=23)
        g = EffectGraph({'nodes': [dict(id='s', type='source'),
            dict(id='f', type='procedural_field', inputs=['s'], **cfg), *nodes], 'output':selected})
        image = g.render({'s':reference})
        filename = f'{len(images):02d}-consumer.png'
        image.save(output/filename)
        records.append(dict(label=label, file=filename, graph_output=selected,
                            rgba_sha256=hashlib.sha256(image.tobytes()).hexdigest()))
        images.append(image)
    sheet = Image.new('RGB', (4*size[0], 4*(size[1]+30)), '#101725')
    font = ImageFont.load_default()
    draw = ImageDraw.Draw(sheet)
    for index, (im, record) in enumerate(zip(images, records)):
        tile = Image.new('RGBA', size, '#263247')
        td = ImageDraw.Draw(tile)
        for y in range(0, size[1], 16):
            for x in range(0, size[0], 16):
                if (x//16+y//16)%2:
                    td.rectangle((x,y,x+15,y+15), fill='#354158')
        tile.alpha_composite(im)
        x,y = (index%4)*size[0], (index//4)*(size[1]+30)
        sheet.paste(tile.convert('RGB'), (x,y+30))
        draw.text((x+8,y+8), record['label'], font=font, fill='white')
    sheet.save(output/'procedural-fields-contact-sheet.png')
    (output/'manifest.json').write_text(json.dumps({'schema':'framerudiments.m7-5-visual-qa.v1',
        'synthetic':True, 'perceptual_approval':False, 'renders':records}, indent=2)+'\n', encoding='utf-8')
    return records


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    build(parser.parse_args().output)
