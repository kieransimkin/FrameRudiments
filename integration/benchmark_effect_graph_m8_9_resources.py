"""Compare bounded resident target reuse with fresh allocations, including transfers."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from PIL import Image, ImageDraw, ImageFont

from benchmark_effect_graph_m8_9_gpu import SIZES, fixture
from framerudiments import __version__
from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import DEFAULT_POOLED_TARGET_BYTES, GLMatteBackend


def effect_graph():
    return EffectGraph({'nodes': [
        {'id': 'source', 'type': 'source'},
        {'id': 'wave', 'type': 'spatial_warp', 'inputs': ['source'],
         'warp_type': 'wave', 'warp_scale': [87, 73], 'warp_amplitude': [21, 14], 'warp_border': 'repeat'},
        {'id': 'swirl', 'type': 'spatial_warp', 'inputs': ['wave'],
         'warp_type': 'swirl', 'warp_radius': 270, 'warp_amount': 1.4, 'warp_border': 'mirror'},
    ], 'output': 'swirl'})


def device_info(gpu):
    return {
        'renderer': gpu.capabilities.renderer,
        'vendor': gpu.ctx.info.get('GL_VENDOR', 'unknown'),
        'gl_version': gpu.ctx.info.get('GL_VERSION', 'unknown'),
        'context_version': gpu.capabilities.context_version,
        'precise_spatial_warps': gpu.supports_precise_spatial_warps,
    }


def measure(graph, sources, repeats, backend, pooled_bytes, *, check_device=None):
    size = sources['source'].size
    surface_bytes = size[0] * size[1] * 4
    retained = min(2, pooled_bytes // surface_bytes)
    frames = []
    with GLMatteBackend(backend=backend, max_pooled_target_bytes=pooled_bytes) as gpu:
        device = device_info(gpu)
        if check_device is not None:
            check_device(device)
        # Compile the shaders and exercise upload/readback once before timing.
        # The smaller warmup canvas cannot populate a timed-size cache.
        graph.render(fixture((160, 90)), gpu_backend=gpu, gpu_strict=True)
        memory_before = gpu.sample_driver_memory()
        for index in range(repeats):
            start = perf_counter()
            output = graph.render(sources, gpu_backend=gpu, gpu_strict=True,
                                  parameter_values={('wave', 'warp_phase'): index * .63})
            elapsed = perf_counter() - start
            transport = dict(gpu.last_transport)
            reused = retained if index else 0
            if (not transport['gpu_resident_graph'] or transport['resident_draws'] != 2
                    or transport['resident_target_allocations'] != 2 - reused
                    or transport['resident_target_reuses'] != reused
                    or transport['retained_target_count'] != retained
                    or transport['retained_target_payload_bytes'] != retained * surface_bytes
                    or transport['upload_bytes'] != surface_bytes or transport['readback_bytes'] != surface_bytes
                    or transport['uploads'] != 1 or transport['readbacks'] != 1):
                raise RuntimeError(f'Unexpected resource or transfer counts in frame {index}: {transport}')
            frames.append({'seconds': elapsed, 'sha256_rgba': sha256(output.tobytes()).hexdigest(),
                           'transport': transport})
        return {
            **device,
            'driver_memory_before': memory_before, 'driver_memory_after': gpu.sample_driver_memory(),
            'max_pooled_target_bytes': pooled_bytes, 'frames': frames,
            'median_steady_seconds': median(frame['seconds'] for frame in frames[1:]),
        }, output


def compare_resources(size, repeats, backend, *, check_device=None):
    graph, sources = effect_graph(), fixture(size)
    disabled, disabled_image = measure(graph, sources, repeats, backend, 0, check_device=check_device)
    bounded, bounded_image = measure(graph, sources, repeats, backend, DEFAULT_POOLED_TARGET_BYTES,
                                    check_device=check_device)
    hashes = [frame['sha256_rgba'] for frame in disabled['frames']]
    if len(set(hashes)) != repeats or hashes != [frame['sha256_rgba'] for frame in bounded['frames']]:
        raise RuntimeError('Changing frames must be distinct and identical between pooling modes')
    report = {'schema': 'framerudiments.m8-9-resources-benchmark.v1', 'version': __version__,
              'resolution': size, 'pixel_parity': 'all RGBA bytes identical per frame',
              'disabled': disabled, 'bounded': bounded}
    return report, disabled_image, bounded_image


def comparison_image(report, disabled_image, bounded_image):
    width, height = report['resolution']
    sheet = Image.new('RGBA', (1000, 420), (18, 25, 40, 255))
    draw = ImageDraw.Draw(sheet)
    title = ImageFont.load_default(size=25)
    body = ImageFont.load_default(size=18)
    small = ImageFont.load_default(size=16)
    draw.text((24, 13), 'FrameRudiments: the same pixels, fewer allocations', font=title, fill='white')
    draw.text((24, 50), f'{width} x {height} on {report["bounded"]["renderer"]}', font=small, fill='#c0cadb')
    for left, mode, label, image in ((24, 'disabled', 'Pool disabled', disabled_image),
                                      (514, 'bounded', 'Bounded pool', bounded_image)):
        thumbnail = image.copy()
        thumbnail.thumbnail((462, 260))
        sheet.alpha_composite(thumbnail, (left, 104))
        transport = report[mode]['frames'][-1]['transport']
        new = transport['resident_target_allocations']
        reused = transport['resident_target_reuses']
        draw.text((left, 78), label, font=body, fill='#eef1f8')
        draw.text((left, 365), f'Final frame: {new} new targets, {reused} reused', font=body, fill='#86efd5')
        draw.text((left, 392), '1 source upload and 1 readback', font=small, fill='#c0cadb')
    return sheet.convert('RGB')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', choices=SIZES, default='hd')
    parser.add_argument('--backend', default='auto', help='ModernGL backend, or auto')
    parser.add_argument('--repeats', type=int, default=6)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--image', type=Path)
    args = parser.parse_args()
    if not 2 <= args.repeats <= 20:
        parser.error('repeats must be 2..20')
    size = SIZES[args.size]
    backend = None if args.backend == 'auto' else args.backend
    report, disabled_image, bounded_image = compare_resources(size, args.repeats, backend)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if args.image:
        args.image.parent.mkdir(parents=True, exist_ok=True)
        comparison_image(report, disabled_image, bounded_image).save(args.image, optimize=True)


if __name__ == '__main__':
    main()
