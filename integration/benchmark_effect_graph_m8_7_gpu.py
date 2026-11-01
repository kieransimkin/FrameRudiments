"""M8.7 real-EGL resident matte/key whole-graph performance and parity evidence.

Measures upload, shaders, scheduling and final readback, excluding media I/O.
The texture estimate is not physical VRAM; large frames are opt-in.
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
    width, height = size
    ink = Image.new('RGBA', size, (50, 170, 60, 230))
    mask = Image.new('RGBA', size, (65, 40, 190, 0))
    secondary = Image.new('RGBA', size, (220, 155, 90, 0))
    drawing = ImageDraw.Draw(ink)
    drawing.rectangle((width // 5, height // 4, width * 3 // 4,
                       height * 3 // 4), fill=(255, 10, 210, 170))
    drawing.ellipse((width // 3, height // 8, width * 4 // 5,
                     height * 7 // 8), fill=(0, 245, 0, 210))
    ImageDraw.Draw(mask).ellipse((width // 8, height // 10,
                                  width * 4 // 5, height * 9 // 10),
                                 fill=(210, 210, 160, 200))
    ImageDraw.Draw(secondary).rectangle((width // 5, height // 4,
                                          width * 9 // 10, height * 3 // 4),
                                         fill=(70, 150, 220, 180))
    return {'ink': ink, 'mask': mask, 'second': secondary}


def graph_for_size(size_name):
    # The filter reference limits graphics processing to 16,777,216 pixels.
    # Large-frame runs omit the blur, retaining matte/chroma and one readback.
    source = 'ink'
    nodes = [{'id': key, 'type': 'source'} for key in ('ink', 'mask', 'second')]
    if size_name != '8k':
        nodes += [{'id': 'filtered', 'type': 'filter', 'inputs': ['ink'],
                   'filter_radius': 1.2, 'filter_border': 'transparent'}]
        source = 'filtered'
    nodes.extend([
        {'id': 'matte', 'type': 'matte',
         'inputs': [source, 'mask', 'second'],
         'matte_channel': 'alpha', 'mask_combine': 'screen',
         'alpha_source': 'original', 'expand': 1, 'feather': 1.2,
         'mask_opacity': .85},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['matte'],
         'alpha_source': 'original', 'key_colors': ['#00ff00', '#77ff00'],
         'key_space': 'rgb', 'tolerance': .09, 'softness': .18,
         'edge_expand': 1, 'despill': .5},
        {'id': 'output', 'type': 'composite', 'inputs': ['key', 'second']},
    ])
    return EffectGraph({'nodes': nodes, 'output': 'output'})


def visible_difference(cpu, gpu):
    import numpy as np
    a = np.asarray(cpu, dtype=np.int16)
    b = np.asarray(gpu, dtype=np.int16)
    diff = np.abs(a - b)
    visible = (a[..., 3] > 16) & (b[..., 3] > 16)
    return {'max_alpha_difference': int(diff[..., 3].max()),
            'max_visible_rgb_difference': int(diff[..., :3][visible].max(initial=0))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', choices=SIZES, default='small')
    parser.add_argument('--gpu-budget-mib', type=int, default=768)
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--cpu', action='store_true', help='compare with CPU reference')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10 or not 128 <= args.gpu_budget_mib <= 8192:
        parser.error('repeats must be 1–10 and GPU budget 128–8192 MiB')

    graph = graph_for_size(args.size)
    sources = fixture(SIZES[args.size])
    plan = resident_plan(graph)
    cpu_seconds = None
    reference = None
    if args.cpu:
        start = perf_counter()
        reference = graph.render(sources)
        cpu_seconds = perf_counter() - start
    with GLMatteBackend(backend='egl',
                        max_working_set_bytes=args.gpu_budget_mib * 1024**2) as gpu:
        samples = []
        digest = None
        parity = None
        for _ in range(args.repeats):
            start = perf_counter()
            output = graph.render(sources, gpu_backend=gpu, gpu_strict=True)
            elapsed = perf_counter() - start
            report = dict(gpu.last_transport)
            current_digest = sha256(output.tobytes()).hexdigest()
            if digest is not None and current_digest != digest:
                raise RuntimeError('non-deterministic resident matte/key output')
            digest = current_digest
            if report['uploads'] != 3 or report['readbacks'] != 1:
                raise RuntimeError(f'resident transfer contract violated: {report}')
            if report['resident_peak_allocated_surfaces'] != plan[
                    'resident_peak_allocated_surfaces']:
                raise RuntimeError('resident allocation plan mismatch')
            if reference is not None:
                parity = visible_difference(reference, output)
                if (parity['max_alpha_difference'] > 26
                        or parity['max_visible_rgb_difference'] > 16):
                    raise RuntimeError(f'GPU/CPU parity error: {parity}')
            samples.append({'seconds': elapsed, 'transport': report})
        result = {
            'schema': 'framerudiments.m8-7-matte-key-benchmark.v1',
            'size': args.size, 'resolution': list(SIZES[args.size]),
            'graphics_backend': 'egl', 'renderer': gpu.capabilities.renderer,
            'vendor': str(gpu.ctx.info.get('GL_VENDOR', 'unknown')),
            'gl_version': gpu.capabilities.context_version,
            'budget_mib': args.gpu_budget_mib,
            'scope': 'whole closed resident DAG: source uploads, shaders, final readback',
            'estimated_texture_payload_not_measured_vram': True,
            'plan': plan, 'cpu_seconds': cpu_seconds,
            'parity': parity, 'frame_sha256': digest,
            'median_gpu_seconds': median(row['seconds'] for row in samples),
            'samples': samples,
        }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
