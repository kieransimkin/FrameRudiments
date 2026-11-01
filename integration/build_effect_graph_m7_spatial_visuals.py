"""Actual deterministic M7 spatial-warp renders, not illustrative generated art."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph import EffectGraph  # noqa: E402


def fixture(size=(240, 180)):
    image = Image.new('RGBA', size)
    draw = ImageDraw.Draw(image)
    for x in range(8, size[0], 24):
        draw.line((x, 12, x, size[1] - 12), fill=(100, 190, 220, 110), width=2)
    for y in range(12, size[1], 24):
        draw.line((8, y, size[0] - 8, y), fill=(100, 190, 220, 110), width=2)
    draw.rounded_rectangle((32, 32, 193, 149), radius=24, fill=(230, 70, 35, 200))
    draw.ellipse((58, 41, 160, 143), fill=(18, 215, 232, 240))
    draw.ellipse((87, 70, 130, 112), fill=(0, 0, 0, 0))
    draw.polygon(((176, 17), (214, 81), (165, 74)), fill=(255, 216, 70, 255))
    return image


def build(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = fixture()
    field = Image.new('RGBA', source.size)
    field.putdata([(round(255 * x / (source.width - 1)), round(255 * y / (source.height - 1)), 128, 255)
                   for y in range(source.height) for x in range(source.width)])
    cases = [
        ('Source', {'warp_mix': 0}),
        ('Wave', {'warp_type': 'wave', 'warp_amplitude': [9, 6], 'warp_scale': [72, 90], 'warp_phase': .4}),
        ('Turbulence', {'warp_type': 'turbulence', 'warp_amplitude': [20, 16], 'warp_scale': [48, 48],
                        'warp_seed': 42, 'warp_phase': .7}),
        ('Swirl', {'warp_type': 'swirl', 'warp_radius': 100, 'warp_angle': 250}),
        ('Radial pinch', {'warp_type': 'radial', 'warp_radius': 96, 'warp_amount': 1.1}),
        ('Mirror', {'warp_type': 'mirror', 'warp_axis': 'xy'}),
        ('Kaleidoscope', {'warp_type': 'kaleidoscope', 'warp_segments': 5, 'warp_angle': 20}),
        ('Polar unwrap', {'warp_type': 'polar_unwrap', 'warp_radius': 88, 'warp_angle': 0}),
        ('Polar wrap', {'warp_type': 'polar_wrap', 'warp_radius': 80, 'warp_angle': 0}),
        ('Live RG map', {'warp_type': 'displacement', 'warp_amplitude': [26, -18]}),
    ]
    cell_w, cell_h = source.width + 16, source.height + 52
    sheet = Image.new('RGB', (5 * cell_w, 2 * cell_h), '#101621')
    manifest = {'kind': 'actual_synthetic_graph_renders', 'font_files_included': False,
                'canvas': list(source.size), 'cases': []}
    font = ImageFont.load_default(size=16)
    for i, (title, config) in enumerate(cases):
        nodes = [{'id': 'source', 'type': 'source'}]
        providers, inputs = {'source': source}, ['source']
        if config.get('warp_type') == 'displacement':
            nodes.append({'id': 'map', 'type': 'source'})
            providers['map'] = field
            inputs.append('map')
        nodes.append({'id': 'warp', 'type': 'spatial_warp', 'inputs': inputs, **config})
        graph = EffectGraph({'nodes': nodes, 'output': 'warp'})
        rendered = graph.render(providers)
        name = f'spatial-{i:02}.png'
        rendered.save(output / name)
        canvas = Image.new('RGBA', source.size, '#202a38')
        draw = ImageDraw.Draw(canvas)
        for y in range(0, source.height, 12):
            for x in range(0, source.width, 12):
                if (x // 12 + y // 12) % 2:
                    draw.rectangle((x, y, x + 11, y + 11), fill='#283443')
        canvas.alpha_composite(rendered)
        x, y = (i % 5) * cell_w + 8, (i // 5) * cell_h + 34
        sheet.paste(canvas.convert('RGB'), (x, y))
        ImageDraw.Draw(sheet).text((x, y - 25), title, fill='#e6f3fa', font=font)
        manifest['cases'].append({'name': title, 'config': config, 'file': name,
                                  'rgba_sha256': hashlib.sha256(rendered.tobytes()).hexdigest(),
                                  'alpha_bounds': rendered.getchannel('A').getbbox()})
    sheet.save(output / 'spatial-contact-sheet.png')
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.output)
