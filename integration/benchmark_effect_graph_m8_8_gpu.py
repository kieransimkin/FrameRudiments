"""M8.8 mixed-island transfer, parity and timing evidence on a real driver."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GLMatteBackend


SIZES = {'small': (320, 180), 'hd': (1920, 1080)}


def fixture(size):
    image = Image.new('RGBA', size, (0, 235, 25, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((size[0] // 6, size[1] // 10, size[0] * 5 // 6,
                  size[1] * 9 // 10), fill=(230, 75, 135, 235),
                 outline=(60, 205, 55, 255), width=4)
    return {'source': image}


def graph():
    return EffectGraph({'nodes': [
        {'id': 'source', 'type': 'source'},
        {'id': 'blur', 'type': 'filter', 'inputs': ['source'], 'filter_radius': 1.1},
        {'id': 'warp', 'type': 'spatial_warp', 'inputs': ['blur'],
         'warp_type': 'raindrop_ripples', 'warp_amplitude': [2, 0], 'warp_scale': [41, 41]},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['warp'],
         'key_color': '#00eb19', 'tolerance': .11, 'softness': .2,
         'edge_decontaminate': .7, 'edge_radius': 3},
        {'id': 'output', 'type': 'opacity', 'inputs': ['key'], 'opacity': .9},
    ], 'output': 'output'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', choices=SIZES, default='small')
    parser.add_argument('--backend', default='egl', help='ModernGL backend, or auto')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error('repeats must be 1..10')
    sources, effect = fixture(SIZES[args.size]), graph()
    start = perf_counter()
    reference = effect.render(sources)
    cpu_seconds = perf_counter() - start
    samples = []
    with GLMatteBackend(backend=None if args.backend == 'auto' else args.backend) as gpu:
        for _ in range(args.repeats):
            start = perf_counter()
            output = effect.render(sources, gpu_backend=gpu)
            elapsed = perf_counter() - start
            report = dict(gpu.last_transport)
            if report.get('gpu_island_count') != 2 or report['uploads'] != 2 or report['readbacks'] != 2:
                raise RuntimeError(f'mixed-island transfer contract violated: {report}')
            samples.append({'seconds': elapsed, 'transport': report})
        import numpy as np
        a, b = np.asarray(reference, dtype=np.int16), np.asarray(output, dtype=np.int16)
        difference = np.abs(a - b)
        visible = (a[..., 3] > 16) & (b[..., 3] > 16)
        parity = {'max_alpha_difference': int(difference[..., 3].max()),
                  'max_visible_rgb_difference': int(difference[..., :3][visible].max(initial=0))}
        if parity['max_alpha_difference'] > 12 or parity['max_visible_rgb_difference'] > 22:
            raise RuntimeError(f'CPU/GPU parity error: {parity}')
        result = {
            'schema': 'framerudiments.m8-8-mixed-islands-benchmark.v1',
            'size': args.size, 'resolution': list(SIZES[args.size]),
            'backend': gpu.capabilities.backend, 'renderer': gpu.capabilities.renderer,
            'vendor': str(gpu.ctx.info.get('GL_VENDOR', 'unknown')),
            'scope': 'two resident islands separated by one CPU spatial warp',
            'cpu_seconds': cpu_seconds, 'median_mixed_seconds': median(s['seconds'] for s in samples),
            'parity': parity, 'frame_sha256': sha256(output.tobytes()).hexdigest(),
            'samples': samples,
        }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
