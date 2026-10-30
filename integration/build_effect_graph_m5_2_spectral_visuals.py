"""Deterministic M5.2 colour-band transition visual evidence (no external media)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph

WIDTH, HEIGHT = 160, 96
PROGRESS = (0, .2, .4, .6, .8, 1)


def build_images():
    a, b = Image.new('RGBA', (WIDTH, HEIGHT)), Image.new('RGBA', (WIDTH, HEIGHT))
    da, db = ImageDraw.Draw(a), ImageDraw.Draw(b)
    da.rectangle((0, 0, WIDTH-1, HEIGHT-1), fill=(12, 19, 34, 255))
    db.rectangle((0, 0, WIDTH-1, HEIGHT-1), fill=(10, 20, 39, 255))
    da.ellipse((15, 18, 70, 75), fill=(255, 70, 30, 220))
    da.rounded_rectangle((85, 20, 145, 78), radius=14, fill=(20, 230, 235, 230))
    db.ellipse((20, 12, 82, 68), fill=(60, 85, 255, 235))
    db.rounded_rectangle((85, 24, 155, 82), radius=14, fill=(245, 25, 210, 235))
    return a, b


def graph():
    bands = [
        {'id': 'orange', 'source_color': '#ff461e', 'to_color': '#3c55ff',
         'tolerance': .12, 'softness': .12, 'space': 'rgb', 'start': 0, 'end': .45,
         'offset_from': [-7, 0], 'offset_to': [9, 0], 'echoes': 2, 'echo_decay': .4},
        {'id': 'cyan', 'source_color': '#14e6eb', 'to_color': '#f519d2',
         'tolerance': .11, 'softness': .11, 'space': 'rgb', 'start': .55, 'end': 1,
         'offset_from': [0, 5], 'offset_to': [0, -6], 'target_color': '#9a22ff',
         'recolor': .55},
    ]
    nodes = [
        {'id': 'from', 'type': 'source'},
        {'id': 'to', 'type': 'source'},
        {'id': 'transition', 'type': 'transition', 'inputs': ['from', 'to'],
         'transition_style': 'spectral', 'bands': bands, 'progress': .5, 'feather': .35},
    ]
    return EffectGraph({'nodes': nodes, 'output': 'transition'})


def make(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    a, b = build_images()
    g = graph()
    sheet = Image.new('RGB', (WIDTH * len(PROGRESS), HEIGHT * 2), 'black')
    manifest = {'schema': 'framerudiments.spectral_transition_qa.v1', 'size': [WIDTH, HEIGHT],
                'frames': [], 'rows': ['spectral', 'ordinary_dissolve']}
    original = {'from': a.tobytes(), 'to': b.tobytes()}
    for index, progress in enumerate(PROGRESS):
        # The first row applies two independently scheduled and displaced bands.
        image = g.render({'from': a, 'to': b}, parameter_values={('transition', 'progress'): progress})
        reference = Image.blend(a, b, progress)
        for row, frame in enumerate((image, reference)):
            sheet.paste(frame.convert('RGB'), (index * WIDTH, row * HEIGHT))
            manifest['frames'].append({'kind': manifest['rows'][row], 'progress': progress,
                                       'sha256': hashlib.sha256(frame.tobytes()).hexdigest()})
    assert a.tobytes() == original['from'] and b.tobytes() == original['to']
    sheet.save(output / 'spectral-transition-contact-sheet.png', optimize=False)
    (output / 'spectral-transition-contact-sheet.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    make(parser.parse_args().output)
