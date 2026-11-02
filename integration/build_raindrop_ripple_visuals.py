"""Build actual synthetic raindrop-ripple documentation media."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont

from framerudiments.effect_graph_spatial import render_spatial_warp
from framerudiments.effect_graph_transition_geometry import geometric_transition


VARIANTS = ('rings', 'droplets', 'storm')


def source(size, *, target=False):
    width, height = size
    image = Image.new('RGBA', size, (10, 21, 48, 255) if not target else (10, 58, 45, 255))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        if target:
            colour = (round(20 + 30 * t), round(120 + 90 * t), round(100 + 75 * t), 255)
        else:
            colour = (round(18 + 35 * t), round(38 + 55 * t), round(95 + 110 * t), 255)
        draw.line((0, y, width, y), fill=colour)
    step = max(12, width // 12)
    for x in range(-height, width + height, step):
        draw.line((x, 0, x + height, height), fill=(235, 245, 255, 115), width=max(1, step // 7))
    draw.ellipse((width * .18, height * .18, width * .82, height * .82),
                 outline=(255, 218, 86, 255) if not target else (255, 156, 205, 255),
                 width=max(2, width // 90))
    return image


def labelled(image, label):
    result = image.copy()
    draw = ImageDraw.Draw(result, 'RGBA')
    font = ImageFont.load_default()
    box = draw.textbbox((0, 0), label, font=font)
    draw.rounded_rectangle((8, 8, 20 + box[2], 18 + box[3]), radius=5, fill=(0, 0, 0, 180))
    draw.text((14, 13), label, font=font, fill='white')
    return result


def layer_frame(size, variant, phase):
    return labelled(render_spatial_warp(source(size), {
        'warp_type': 'raindrop_ripples', 'warp_ripple_variant': variant,
        'warp_amplitude': [18, 16], 'warp_scale': [18, 18],
        'warp_radius': min(size) * .42, 'warp_phase': phase,
        'warp_segments': 9, 'warp_seed': 27, 'warp_border': 'clamp',
    }), variant)


def wash_frame(size, progress, variant='storm'):
    return labelled(geometric_transition(source(size), source(size, target=True), {
        'transition_style': 'wash', 'wash_variant': variant, 'wash_seed': 27,
        'segments': 9, 'frequency': 7, 'amplitude': 22, 'strength': .9,
        'phase': .35, 'feather': .08, 'progress': progress,
    }), f'wash / {variant} / {progress:.2f}')


def composite_frame(progress):
    width, height = 640, 360
    top_height = height // 2
    panel_widths = (width // 3, width // 3, width - 2 * (width // 3))
    phase = progress * 2 * math.pi
    image = Image.new('RGBA', (width, height), 'black')
    x = 0
    for variant, panel_width in zip(VARIANTS, panel_widths):
        image.alpha_composite(layer_frame((panel_width, top_height), variant, phase), (x, 0))
        x += panel_width
    image.alpha_composite(wash_frame((width, height - top_height), progress), (0, top_height))
    return image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--seconds', type=float, default=2)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    still = composite_frame(.5)
    still.save(output / 'raindrop-ripples.png')

    fps = 60
    frame_count = max(2, round(args.seconds * fps))
    command = [args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
               '-f', 'rawvideo', '-pixel_format', 'rgba', '-video_size', '640x360',
               '-framerate', str(fps), '-i', '-', '-an', '-c:v', 'libx264',
               '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p',
               '-movflags', '+faststart', str(output / 'raindrop-ripples.mp4')]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for frame in range(frame_count):
            progress = frame / (frame_count - 1)
            process.stdin.write(composite_frame(progress).tobytes())
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError('ffmpeg failed while encoding raindrop-ripples.mp4')
    except BaseException:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        process.kill()
        process.wait()
        raise


if __name__ == '__main__':
    main()
