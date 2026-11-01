"""M8.6 GPU-resident spectral DAG benchmark with explicit transfer/plan evidence.

Only measures this opt-in graph evaluation: uploads, GLSL draws and final readback.
No claims about physical VRAM, decoder/encoder, driver process RSS or speedup.
"""
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
from framerudiments.effect_graph_gpu_resident import resident_plan

SIZES = {'small': (320, 180), 'hd': (1920, 1080),
         '4k': (3840, 2160), '8k': (7680, 4320)}


def example(size):
    image = Image.new('RGBA', size, (25, 80, 150, 180))
    painter = ImageDraw.Draw(image)
    w, h = size
    painter.rectangle((w // 9, h // 7, w // 2, h * 3 // 4),
                      fill=(255, 0, 30, 210))
    painter.ellipse((w * 4 // 9, h // 9, w * 8 // 9, h * 5 // 6),
                    fill=(0, 245, 35, 200))
    return image


def graph():
    return EffectGraph({'nodes': [
        {'id': 'source', 'type': 'source'},
        {'id': 'clone', 'type': 'copy', 'inputs': ['source']},
        {'id': 'banded', 'type': 'spectral_displacement', 'inputs': ['source'],
         'spectral_mode': 'replace', 'alpha_mode': 'source', 'mix': .7,
         'bands': [
             {'id': 'warm', 'source_color': '#ff001e', 'target_color': '#00bbff',
              'space': 'oklab', 'offset': [3.25, -1.5], 'opacity': .9,
              'recolor': .8, 'tolerance': .10, 'softness': .16},
             {'id': 'cool', 'source_color': '#00f523', 'target_color': '#ff00bb',
              'space': 'hsv', 'offset': [-2.5, 1.25], 'opacity': .65,
              'recolor': .7, 'tolerance': .10, 'softness': .13},
         ]},
        {'id': 'out', 'type': 'transition', 'inputs': ['clone', 'banded'],
         'transition_style': 'dissolve', 'progress': .65},
    ], 'output': 'out'})


def parity(expected, actual):
    import numpy as np
    a = np.asarray(expected, dtype=np.int16)
    b = np.asarray(actual, dtype=np.int16)
    delta = np.abs(a - b)
    valid = (a[..., 3] > 8) & (b[..., 3] > 8)
    return {'alpha_max': int(delta[..., 3].max()),
            'visible_rgb_max': int(delta[..., :3][valid].max(initial=0))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', choices=tuple(SIZES), default='small')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--cpu', action='store_true')
    parser.add_argument('--gpu-budget-mib', type=int, default=768)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10 or not 128 <= args.gpu_budget_mib <= 8192:
        parser.error('repeats 1..10; GPU budget 128..8192 MiB')
    if args.size == '8k' and args.cpu:
        parser.error('8K CPU parity must be run in a separate, memory-qualified environment')

    graph_obj = graph()
    source = example(SIZES[args.size])
    reference = None
    cpu_seconds = None
    if args.cpu:
        start = perf_counter()
        reference = graph_obj.render({'source': source})
        cpu_seconds = perf_counter() - start
    plan = resident_plan(graph_obj)
    with GLMatteBackend(backend='egl',
                        max_working_set_bytes=args.gpu_budget_mib * 1024**2) as gpu:
        rows = []
        seen_hash = None
        match = None
        for _ in range(args.repeats):
            start = perf_counter()
            image = graph_obj.render({'source': source}, gpu_backend=gpu, gpu_strict=True)
            seconds = perf_counter() - start
            digest = sha256(image.tobytes()).hexdigest()
            if seen_hash is not None and digest != seen_hash:
                raise RuntimeError('non-deterministic GPU output across runs')
            seen_hash = digest
            info = dict(gpu.last_transport)
            if (info.get('uploads') != 1 or info.get('readbacks') != 1
                    or not info.get('gpu_resident_graph')
                    or info.get('resident_peak_allocated_surfaces') !=
                    plan['resident_peak_allocated_surfaces']):
                raise RuntimeError('spectral resident transfer or allocation contract failed')
            if reference is not None:
                match = parity(reference, image)
                if match['alpha_max'] > 6 or match['visible_rgb_max'] > 13:
                    raise RuntimeError(f'M8.6 spectral CPU/GPU parity failed: {match}')
            rows.append({'seconds': seconds, 'transport': info})
        result = {
            'schema': 'framerudiments.m8-6-spectral-benchmark.v1',
            'resolution': list(SIZES[args.size]), 'size': args.size,
            'gpu_backend': 'egl', 'renderer': gpu.capabilities.renderer,
            'gpu_vendor': str(gpu.ctx.info.get('GL_VENDOR', 'unknown')),
            'gl_version': gpu.capabilities.context_version,
            'gpu_budget_mib': args.gpu_budget_mib,
            'measured_vram': False, 'estimated_rgba8_texture_payload_only': True,
            'scope': 'one spectral closed-DAG evaluation: upload + resident draws + readback',
            'planned': plan, 'frame_sha256': seen_hash,
            'cpu_seconds': cpu_seconds, 'parity': match,
            'median_gpu_seconds': median(row['seconds'] for row in rows),
            'samples': rows,
        }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
