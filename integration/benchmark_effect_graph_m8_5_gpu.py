"""M8.5 reproducible resident-DAG EGL benchmarks and CPU parity evidence.

Includes real source uploads, scheduling, shader execution and final readback.
It is NOT a measurement of physical VRAM, decoding, encoding or total render
pipeline latency. Large sizes are deliberately opt-in on capable GPU runners.
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


def fixture(size):
    a = Image.new('RGBA', size, (120, 58, 200, 175))
    b = Image.new('RGBA', size, (40, 175, 120, 215))
    draw = ImageDraw.Draw(a)
    width, height = size
    draw.rectangle((width // 8, height // 8, width // 3, height // 2),
                   fill=(255, 0, 155, 0))
    draw.ellipse((width // 2, height // 4, width * 7 // 8, height * 3 // 4),
                 fill=(0, 250, 230, 230))
    return {'a': a, 'b': b}


def graph_for_size(size_name):
    nodes = [{'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
             {'id': 'clone', 'type': 'copy', 'inputs': ['a']}]
    if size_name in ('small', 'hd', '4k'):
        nodes += [
            {'id': 'blur', 'type': 'filter', 'inputs': ['a'],
             'filter_radius': 1.3, 'filter_border': 'transparent'},
            {'id': 'shift', 'type': 'chromatic_displacement', 'inputs': ['blur'],
             'red_offset': [1.25, -.5]},
            {'id': 'mix', 'type': 'transition', 'inputs': ['shift', 'b'],
             'transition_style': 'radial', 'progress': .44, 'feather': .12},
        ]
    else:
        # 8K exceeds the bounded filter reference pixel cap (16.78M).
        # Benchmark resident forks/transitions/compositing without filtering.
        nodes += [
            {'id': 'mix', 'type': 'transition', 'inputs': ['a', 'b'],
             'transition_style': 'radial', 'progress': .44, 'feather': .12},
        ]
    nodes.append({'id': 'out', 'type': 'composite',
                  'inputs': ['mix', 'clone'], 'opacity': .84})
    return EffectGraph({'nodes': nodes, 'output': 'out'})


def compare_visible(reference, actual):
    import numpy as np

    a = np.asarray(reference, dtype=np.int16)
    b = np.asarray(actual, dtype=np.int16)
    differences = np.abs(a - b)
    visible = (a[..., 3] > 16) & (b[..., 3] > 16)
    return {'max_alpha_difference': int(differences[..., 3].max()),
            'max_visible_rgb_difference': int(differences[..., :3][visible].max(initial=0))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', choices=tuple(SIZES), default='small')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--gpu-budget-mib', type=int, default=768)
    parser.add_argument('--cpu', action='store_true', help='calculate full-frame CPU parity')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10 or not 128 <= args.gpu_budget_mib <= 8192:
        parser.error('repeats must be 1–10, GPU budget 128–8192 MiB')

    sources = fixture(SIZES[args.size])
    graph = graph_for_size(args.size)
    reference = None
    cpu_seconds = None
    if args.cpu:
        before = perf_counter()
        reference = graph.render(sources)
        cpu_seconds = perf_counter() - before

    plan = resident_plan(graph)
    with GLMatteBackend(backend='egl',
                        max_working_set_bytes=args.gpu_budget_mib * 1024**2) as gpu:
        samples = []
        digest = None
        parity = None
        for _ in range(args.repeats):
            start = perf_counter()
            output = graph.render(sources, gpu_backend=gpu, gpu_strict=True)
            seconds = perf_counter() - start
            new_digest = sha256(output.tobytes()).hexdigest()
            if digest is not None and new_digest != digest:
                raise RuntimeError('GPU output changed across identical evaluations')
            digest = new_digest
            report = dict(gpu.last_transport)
            if (report['uploads'] != len(graph.source_ids)
                    or report['readbacks'] != 1
                    or report['resident_peak_allocated_surfaces'] != plan[
                        'resident_peak_allocated_surfaces']):
                raise RuntimeError('M8.5 resident transfer/allocation contract violated')
            if reference is not None:
                parity = compare_visible(reference, output)
                if parity['max_alpha_difference'] > 4 or parity['max_visible_rgb_difference'] > 10:
                    raise RuntimeError(f'M8.5 CPU/GPU pixel parity rejected: {parity}')
            samples.append({'seconds': seconds, 'transport': report})

        result = {
            'schema': 'framerudiments.m8-5-resident-benchmark.v1',
            'size_label': args.size, 'resolution': list(SIZES[args.size]),
            'backend': 'egl', 'gpu_renderer': gpu.capabilities.renderer,
            'gpu_vendor': str(gpu.ctx.info.get('GL_VENDOR', 'unknown')),
            'gl_version': gpu.capabilities.context_version,
            'gpu_budget_mib': args.gpu_budget_mib,
            'profile_scope': 'full resident graph upload+draw+readback, excludes encoder and decoder',
            'peak_texture_bytes_are_estimates_not_physical_vram': True,
            'deterministic_fixture': True,
            'frame_sha256': digest,
            'planned_surfaces': plan,
            'cpu_reference_seconds': cpu_seconds,
            'parity': parity,
            'median_gpu_seconds': median(row['seconds'] for row in samples),
            'samples': samples,
        }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
