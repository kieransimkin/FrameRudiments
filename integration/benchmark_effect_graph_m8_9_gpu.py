"""Measure CPU/GPU warp parity and boundary transfers, with optional demo media."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from math import tau
from pathlib import Path
from statistics import median
import subprocess
from time import perf_counter

import numpy as np
from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GLMatteBackend


SIZES = {'small': (320, 180), 'hd': (1920, 1080)}
FPS = 60


def fixture(size):
    width, height = size
    source = Image.new('RGBA', size, (32, 49, 82, 0))
    draw = ImageDraw.Draw(source)
    step = max(12, width // 18)
    for x in range(0, width, step):
        colour = (45 + x * 180 // width, 210 - x * 100 // width, 205, 200)
        draw.line((x, height // 6, x, height * 5 // 6), fill=colour, width=3)
    for y in range(height // 6, height * 5 // 6, max(12, height // 12)):
        draw.line((0, y, width - 1, y), fill=(95, 140, 250, 200), width=2)
    draw.ellipse((width // 4, height // 6, width * 3 // 4, height * 5 // 6),
                 outline=(255, 198, 85, 255), width=4)
    return {'source': source}


def graph(kind='wave', *, boundary=False):
    return EffectGraph({'nodes': [
        {'id': 'source', 'type': 'source'},
        {'id': 'warp', 'type': 'spatial_warp', 'inputs': ['source'], 'warp_type': kind,
         'warp_amplitude': [128, -128] if boundary else [18, 9],
         'warp_scale': [16.03, 16.23] if boundary else [79.1, 59.3],
         'warp_phase': -196.13 if boundary else .8, 'warp_border': 'repeat',
         'warp_octaves': 6 if boundary else 3, 'warp_seed': 23},
    ], 'output': 'warp'})


def mapped_fixture(size):
    sources = fixture(size)
    width, height = size
    y, x = np.ogrid[:height, :width]
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., 0] = (x * 3 + y) % 256
    rgba[..., 1] = (y * 5 - x) % 256
    rgba[..., 2] = (x + y) % 256
    rgba[..., 3] = (x + y * 7) % 256
    sources['map'] = Image.fromarray(rgba)
    effect = EffectGraph({'nodes': [
        {'id': 'source', 'type': 'source'}, {'id': 'map', 'type': 'source'},
        {'id': 'warp', 'type': 'spatial_warp', 'inputs': ['source', 'map'], 'warp_type': 'displacement',
         'warp_amplitude': [128, -128], 'warp_map_x': 'luminance', 'warp_map_y': 'a',
         'warp_neutral': .37, 'warp_border': 'repeat'},
    ], 'output': 'warp'})
    return effect, sources


def polar_graph():
    return EffectGraph({'nodes': [
        {'id': 'source', 'type': 'source'},
        {'id': 'warp', 'type': 'spatial_warp', 'inputs': ['source'], 'warp_type': 'polar_wrap',
         'warp_border': 'repeat', 'warp_radius': 230, 'warp_center': [.50000001, .5],
         'warp_angle': 90.000001},
    ], 'output': 'warp'})


def parity(reference, actual):
    a, b = (np.asarray(image, dtype=np.int16) for image in (reference, actual))
    difference = np.abs(a - b)
    visible = (a[..., 3] >= 16) & (b[..., 3] >= 16)
    # A one-level alpha rounding change matters less on a faint edge than on
    # opaque ink. Report straight RGB too, and gate the composited black/white
    # appearance on every pixel, including the transparent fringe.
    black_a, black_b = (image[..., :3] * (image[..., 3:4] / 255) for image in (a, b))
    white_a, white_b = (c + (255 - im[..., 3:4]) for c, im in ((black_a, a), (black_b, b)))
    return {
        'max_alpha_difference': int(difference[..., 3].max()),
        'max_visible_rgb_difference': int(difference[..., :3][visible].max(initial=0)),
        'max_composited_difference': round(float(max(np.abs(black_a - black_b).max(),
                                                     np.abs(white_a - white_b).max())), 3),
    }


def benchmark(gpu, effect, sources, repeats):
    cpu, device = [], []
    for _ in range(repeats):
        start = perf_counter()
        reference = effect.render(sources)
        cpu.append(perf_counter() - start)
        start = perf_counter()
        output = effect.render(sources, gpu_backend=gpu, gpu_strict=True)
        device.append(perf_counter() - start)
    transport = dict(gpu.last_transport)
    if (not transport['gpu_resident_graph'] or transport['uploads'] != len(effect.source_ids)
            or transport['readbacks'] != 1):
        raise RuntimeError(f'Expected one upload per resident source and one readback: {transport}')
    difference = parity(reference, output)
    if difference['max_alpha_difference'] > 5 or difference['max_composited_difference'] > 5:
        raise RuntimeError(f'CPU/GPU spatial warp parity exceeded the tolerance: {difference}')
    return {'cpu_seconds': cpu, 'gpu_seconds': device, 'median_cpu_seconds': median(cpu),
            'median_gpu_seconds': median(device), 'parity': difference, 'transport': transport,
            'frame_sha256': sha256(output.tobytes()).hexdigest()}


def write_media(gpu, directory, ffmpeg, *, map_and_polar):
    directory.mkdir(parents=True, exist_ok=True)
    images, effect = fixture(SIZES['small']), graph()
    width, height = images['source'].size
    row_height = height + 28
    if map_and_polar:
        mapped_effect, mapped_sources = mapped_fixture((width, height))
        rows = [
            ('Control map', 'CPU map displacement', 'GPU map displacement', mapped_sources['map'],
             mapped_effect, mapped_sources, 'warp_amplitude.x'),
            ('Polar source', 'CPU polar wrap', 'GPU polar wrap', images['source'],
             polar_graph(), images, 'warp_angle'),
        ]
        filename = 'effect-graph-m8-9-map-polar'
    else:
        rows = [('Source', 'CPU reference', 'GPU result', images['source'], effect, images, 'warp_phase')]
        filename = 'effect-graph-m8-9-spatial'
    sheet_size = (width * 3, row_height * len(rows))
    background = (17, 24, 39, 255)

    def panel(phase):
        sheet = Image.new('RGBA', sheet_size, background)
        labels = ImageDraw.Draw(sheet)
        control_values = {'warp_phase': phase, 'warp_amplitude.x': 32 + 8 * phase,
                          'warp_angle': phase / tau * 360}
        for row, (label_source, label_cpu, label_gpu, source, graph_, sources, control) in enumerate(rows):
            controls = {('warp', control): control_values[control]}
            cpu = graph_.render(sources, parameter_values=controls)
            device = graph_.render(sources, parameter_values=controls, gpu_backend=gpu, gpu_strict=True)
            for index, (label, image) in enumerate(zip((label_source, label_cpu, label_gpu),
                                                      (source, cpu, device))):
                x, y = index * width, row * row_height
                labels.text((x + 12, y + 8), label, fill='white')
                sheet.alpha_composite(image, (x, y + 28))
        return sheet

    panel(tau / 4).save(directory / f'{filename}.png')
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
               '-pixel_format', 'rgba', '-video_size', f'{sheet_size[0]}x{sheet_size[1]}',
               '-framerate', str(FPS), '-i', '-', '-an', '-c:v', 'libx264', '-crf', '18',
               '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(directory / f'{filename}.mp4')]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
        try:
            for frame in range(FPS * 2):
                process.stdin.write(panel(frame * tau / (FPS * 2)).tobytes())
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError('FFmpeg failed to encode the spatial warp demonstration')
        except BaseException:
            process.kill()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', choices=SIZES, default='small')
    parser.add_argument('--backend', default='egl', help='ModernGL backend, or auto')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--media', type=Path)
    parser.add_argument('--map-polar-media', action='store_true',
                        help='show the displacement map and polar wrap comparison in the optional media')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error('repeats must be 1..10')
    with GLMatteBackend(backend=None if args.backend == 'auto' else args.backend) as gpu:
        size = SIZES[args.size]
        sources = fixture(size)
        mapped_effect, mapped_sources = mapped_fixture(size)
        result = {
            'schema': 'framerudiments.m8-9-spatial-benchmark.v1',
            'resolution': list(size), 'renderer': gpu.capabilities.renderer,
            'vendor': gpu.ctx.info.get('GL_VENDOR', 'unknown'),
            'gl_version': gpu.ctx.info.get('GL_VERSION', 'unknown'),
            'wave': benchmark(gpu, graph(), sources, args.repeats),
            'wave_at_precision_boundary': benchmark(gpu, graph(boundary=True), sources, 1),
            'turbulence_at_precision_boundary': benchmark(gpu, graph('turbulence', boundary=True), sources, 1),
            'displacement_at_precision_boundary': benchmark(gpu, mapped_effect, mapped_sources, 1),
            'polar_wrap': benchmark(gpu, polar_graph(), sources, args.repeats),
        }
        if args.media is not None:
            write_media(gpu, args.media, args.ffmpeg, map_and_polar=args.map_polar_media)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
