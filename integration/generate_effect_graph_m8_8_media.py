"""Generate actual M8.8 CPU/GPU edge comparison and transfer evidence."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GLMatteBackend


def main():
    root = Path(__file__).resolve().parents[1]
    media = root / 'docs' / 'media'
    media.mkdir(exist_ok=True)
    size = (320, 200)
    source = Image.new('RGBA', size, (0, 232, 20, 255))
    draw = ImageDraw.Draw(source)
    draw.ellipse((55, 20, 265, 195), fill=(230, 72, 132, 255),
                 outline=(70, 210, 55, 255), width=12)
    draw.rectangle((120, 45, 205, 170), fill=(248, 188, 70, 220))
    graph = EffectGraph({'nodes': [
        {'id': 'source', 'type': 'source'},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['source'],
         'key_color': '#00e814', 'tolerance': .12, 'softness': .22,
         'despill': .35, 'edge_decontaminate': .8, 'edge_radius': 4},
    ], 'output': 'key'})
    cpu = graph.render({'source': source})
    with GLMatteBackend(backend=None if sys.platform == 'win32' else 'egl') as gpu:
        accelerated = graph.render({'source': source}, gpu_backend=gpu, gpu_strict=True)
        telemetry = {'renderer': gpu.capabilities.renderer,
                     'backend': gpu.capabilities.backend,
                     'transport': gpu.last_transport,
                     'dispatch': gpu.last_dispatch}
    a = np.asarray(cpu, dtype=np.int16)
    b = np.asarray(accelerated, dtype=np.int16)
    difference = np.uint8(np.clip(np.abs(a - b) * 8, 0, 255))
    difference[..., 3] = 255
    panels = [source, cpu, accelerated, Image.fromarray(difference, 'RGBA')]
    sheet = Image.new('RGBA', (size[0] * 4, size[1] + 34), (22, 24, 31, 255))
    labels = ('SOURCE', 'CPU REFERENCE', 'GPU RESULT', '8X DIFFERENCE')
    label_draw = ImageDraw.Draw(sheet)
    for index, (panel, label) in enumerate(zip(panels, labels)):
        x = index * size[0]
        sheet.paste(panel, (x, 34), panel)
        label_draw.text((x + 10, 10), label, fill=(240, 244, 250, 255))
    sheet.save(media / 'effect-graph-m8-8-decontamination.png')
    (media / 'effect-graph-m8-8-telemetry.json').write_text(
        json.dumps(telemetry, indent=2) + '\n', encoding='utf8')


if __name__ == '__main__':
    main()
