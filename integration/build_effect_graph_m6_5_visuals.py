"""Actual M6.5 graph renders, bounds overlays and reproducible JSON diagnostics.

Synthetic RGBA/text fixtures only; this is not a rigged-character render or a
GPU benchmark. No external files, model weights, fonts or private media needed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from framerudiments.effect_graph import EffectGraph  # noqa: E402
from framerudiments.effect_graph_style_contract import style_catalogue  # noqa: E402


def fixtures(size=(128, 96)):
    result = {}
    for name in ('translucent', 'text', 'holed', 'canvas-edge'):
        image = Image.new('RGBA', size)
        draw = ImageDraw.Draw(image)
        if name == 'text':
            draw.text((32, 35), 'STYLE', fill=(240, 130, 40, 190))
        elif name == 'holed':
            draw.ellipse((24, 12, 104, 84), fill=(40, 190, 240, 180))
            draw.ellipse((48, 33, 80, 65), fill=(220, 0, 255, 0))
        else:
            box = (0, 0, 56, 50) if name == 'canvas-edge' else (32, 20, 96, 76)
            draw.rounded_rectangle(box, radius=9, fill=(50, 190, 210, 150))
        result[name] = image
    return result


def _probe(parent):
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='m65-probe-', dir=parent) as folder:
        first = Path(folder) / 'before'
        first.write_bytes(b'frame-rudiments-probe')
        assert first.read_bytes() == b'frame-rudiments-probe'
        second = first.rename(first.with_name('after'))
        second.unlink()


def build(output):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a fresh output directory for M6.5 evidence')
    _probe(output.parent)
    output.mkdir()
    styles = {
        'outline': {'style_width': 3.5, 'style_color': '#ffba30'},
        'drop_shadow': {'style_offset': [-9.5, 7.25], 'style_blur': 2, 'style_color': '#b077ee'},
        'gradient_overlay': {'gradient_angle': 25, 'gradient_space': 'oklab', 'gradient_stops': [
            {'id': 'cool', 'position': 0, 'color': '#087bdd'},
            {'id': 'hot', 'position': 1, 'color': '#ff953e'}]},
    }
    width, height = 188, 142
    sheet = Image.new('RGB', (width * 3, height * 4 + 30), '#151c29')
    draw_sheet = ImageDraw.Draw(sheet)
    draw_sheet.text((8, 8), 'M6.5 actual renders: amber estimate / green actual alpha', fill='white')
    rows = []
    for r, (fixture, source) in enumerate(fixtures().items()):
        for c, (style, settings) in enumerate(styles.items()):
            graph = EffectGraph({'nodes': [
                {'id': 'fixture', 'type': 'source'},
                {'id': 'styled', 'type': 'layer_style', 'inputs': ['fixture'],
                 'style_type': style, **settings}], 'output': 'styled'})
            report = {}
            result = graph.render({'fixture': source}, style_report=report)
            stem = f'{r:02d}-{c:02d}'
            result.save(output / f'{stem}.png')
            manifest = graph.style_manifest(active_only=True)
            (output / f'{stem}.json').write_text(json.dumps(
                {'manifest': manifest, 'report': report}, indent=2, sort_keys=True, allow_nan=False) + '\n',
                encoding='utf-8')
            bounds = report['nodes']['styled']['bounds']
            tile = Image.new('RGBA', source.size, '#202d3b')
            tile.alpha_composite(result)
            paint = ImageDraw.Draw(tile)
            for key, colour in (('conservative_canvas', '#ffbe39'), ('actual_output_alpha', '#66ff9a')):
                box = bounds[key]
                if box is not None:
                    paint.rectangle((box[0], box[1], box[2]-1, box[3]-1), outline=colour)
            x, y = c * width + 8, r * height + 30
            draw_sheet.text((x, y + 1), f'{fixture} / {style}', fill='white')
            sheet.paste(tile.convert('RGB'), (x, y + 18))
            draw_sheet.text((x, y + 118), 'Possible clipping' if bounds['may_clip'] else 'Within canvas',
                            fill='#ffbe39' if bounds['may_clip'] else '#90c9af')
            rows.append({'fixture': fixture, 'style': style, 'image': f'{stem}.png',
                         'pixels_sha256': hashlib.sha256(result.tobytes()).hexdigest(),
                         'bounds': bounds})
    sheet.save(output / 'style-bounds-contact-sheet.png')
    evidence = {'schema': 'framerudiments.m6.5-visual-evidence.v1', 'backend': 'cpu',
                'fixture_kind': 'synthetic_rgba_and_pillow_text', 'cases': rows}
    (output / 'manifest.json').write_text(json.dumps(evidence, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    (output / 'style-catalogue.json').write_text(json.dumps(style_catalogue(), indent=2, sort_keys=True) + '\n',
                                               encoding='utf-8')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.output)
