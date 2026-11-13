"""Compare CPU and GPU output at the authored limits of spatial warp controls."""
from __future__ import annotations

import argparse
import json
from math import atan2, degrees, tau
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageDraw, ImageFont

from benchmark_effect_graph_m8_9_gpu import FPS, SIZES, benchmark, fixture, mapped_fixture
from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GLMatteBackend


def precision_cases(size):
    width, height = size
    images = fixture(size)
    draw = ImageDraw.Draw(images['source'])
    draw.rectangle((0, 0, 3, height - 1), fill=(14, 251, 164, 255))
    draw.rectangle((width - 4, 0, width - 1, height - 1), fill=(252, 51, 32, 195))
    centre = [(width // 2 + .5 + 1e-7) / width, (height // 2 + .5 - 2e-7) / height]
    extreme = dict(warp_amplitude=[4096, -4096], warp_scale=[1.013, 1.041], warp_phase=-196.13)
    yield 'wave', images, dict(extreme)
    yield 'turbulence', images, dict(extreme, warp_octaves=6, warp_seed=83)
    _, mapped = mapped_fixture(size)
    yield 'displacement', {**images, 'map': mapped['map']}, dict(
        warp_amplitude=[4096, -4096], warp_neutral=1 - 1.1 / 255,
        warp_map_x='luminance', warp_map_y='a',
    )
    yield 'radial', images, dict(warp_center=centre, warp_amount=-2, warp_radius=8192)
    for border, radius in (('transparent', 128), ('clamp', .5), ('mirror', .50131), ('repeat', .50003)):
        yield f'polar_wrap_{border}', images, dict(warp_border=border, warp_angle=180.000000001,
                                                   warp_radius=radius, warp_center=[.5, .5])
    yield 'polar_wrap_oblique', images, dict(warp_border='transparent', warp_radius=height / 2,
                                            warp_angle=degrees(atan2(2, 3)), warp_center=centre)
    for variant in ('rings', 'droplets', 'storm'):
        yield f'raindrop_{variant}', images, dict(
            extreme, warp_ripple_variant=variant, warp_radius=min(size) * .43,
            warp_center=centre, warp_segments=32, warp_seed=42, warp_phase=tau - 1e-8,
        )


def precision_graph(name, images, settings):
    kind = 'polar_wrap' if name.startswith('polar_wrap_') else (
        'raindrop_ripples' if name.startswith('raindrop_') else name)
    return EffectGraph({'nodes': [
        *({'id': key, 'type': 'source'} for key in images),
        {'id': 'warp', 'type': 'spatial_warp', 'inputs': list(images),
         'warp_type': kind, 'warp_border': 'repeat', **settings},
    ], 'output': 'warp'})


def render_seam_media(gpu, directory, ffmpeg, verify_video, ffprobe, *, frames=2 * FPS):
    """Show both CPU and GPU sampling an oblique transparent polar seam."""
    import subprocess
    from types import SimpleNamespace

    directory.mkdir(exist_ok=True, parents=True)
    width, height = 480, 320
    images = fixture((width, height))
    draw = ImageDraw.Draw(images['source'])
    draw.rectangle((0, 0, width // 12, height), fill=(44, 241, 170, 255))
    draw.rectangle((width - width // 12, 0, width, height), fill=(244, 83, 47, 220))
    for y in range(12, height, 18):
        draw.line((0, y, width, y), fill=(146, 180, 255, 210), width=2)
    graph = precision_graph('polar_wrap_oblique', images, dict(
        warp_border='transparent', warp_radius=152, warp_center=[.5, .5],
    ))
    sheet_width, sheet_height = 2 * width, height + 44
    font = ImageFont.load_default(size=20)

    def panel(frame):
        overrides = {('warp', 'warp_angle'): 15 + 180 * frame / frames}
        reference = graph.render(images, parameter_values=overrides)
        device = graph.render(images, parameter_values=overrides, gpu_backend=gpu, gpu_strict=True)
        image = Image.new('RGBA', (sheet_width, sheet_height), (17, 24, 39, 255))
        captions = ImageDraw.Draw(image)
        for x, label, pixels in ((0, 'CPU reference', reference), (width, 'GPU result', device)):
            captions.text((x + 16, 12), label, font=font, fill='white')
            image.alpha_composite(pixels, (x, 44))
        return image

    start = perf_counter()
    panel(frames // 3).save(directory / 'effect-graph-m8-9-polar-seam.png')
    video = directory / 'effect-graph-m8-9-polar-seam.mp4'
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgba',
               '-video_size', f'{sheet_width}x{sheet_height}', '-framerate', str(FPS), '-i', '-', '-an',
               '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(video)]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
        try:
            for frame in range(frames):
                process.stdin.write(panel(frame).tobytes())
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError('FFmpeg failed to encode the polar seam comparison')
        except BaseException:
            process.kill()
            raise
    project = SimpleNamespace(fps=FPS, width=sheet_width, height=sheet_height)
    receipt = verify_video(video, project, frames, ffmpeg, ffprobe)
    return dict(receipt, elapsed_seconds=perf_counter() - start)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', choices=SIZES, default='hd')
    parser.add_argument('--backend', default='egl')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', action='append', help='restrict the comparison to named cases')
    parser.add_argument('--media', type=Path, help='render the polar seam comparison')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--ffprobe', default='ffprobe')
    args = parser.parse_args()
    size = SIZES[args.size]
    with GLMatteBackend(backend=None if args.backend == 'auto' else args.backend) as gpu:
        result = {'schema': 'framerudiments.m8-9-precision-benchmark.v1', 'resolution': list(size),
                  'renderer': gpu.capabilities.renderer, 'version': gpu.ctx.info.get('GL_VERSION'),
                  'precise_spatial_warps': gpu.supports_precise_spatial_warps, 'cases': {}}
        for name, images, settings in precision_cases(size):
            if args.case is None or name in args.case:
                graph = precision_graph(name, images, settings)
                result['cases'][name] = benchmark(gpu, graph, images, 1)
                print(name, result['cases'][name]['parity'], flush=True)
        if args.media is not None:
            from framerudiments.encoding import verify_video

            result['media'] = render_seam_media(gpu, args.media, args.ffmpeg, verify_video, args.ffprobe)
    if not result['cases'] or args.case and set(args.case) != result['cases'].keys():
        parser.error('unknown case')
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')


if __name__ == '__main__':
    main()
