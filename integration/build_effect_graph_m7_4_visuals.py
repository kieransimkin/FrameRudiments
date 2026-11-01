"""Render synthetic M7.4 evidence; no private media, fonts or model downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph_raster import render_raster  # noqa: E402


def source_image(width=320, height=200):
    y, x = np.indices((height, width), dtype=np.float64)
    u, v = x / width, y / height
    rgb = np.stack([
        .5 + .5 * np.sin(9 * u + 4 * v),
        .5 + .5 * np.sin(6 * v - 4 * u + 2),
        .5 + .5 * np.cos(11 * u - 7 * v),
    ], axis=-1)
    ellipse = ((u - .5) / .46)**2 + ((v - .5) / .43)**2
    alpha = np.clip((1 - ellipse) * 18, 0, 1) * (.7 + .3 * u)
    hole = ((u - .35) / .09)**2 + ((v - .48) / .18)**2
    alpha *= np.clip((hole - 1) * 12, 0, 1)
    a = np.uint8(np.rint(np.clip(np.concatenate([rgb, alpha[..., None]], axis=-1), 0, 1) * 255))
    image = Image.fromarray(a)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((width * .53, height * .32, width * .82, height * .59),
                           radius=6, fill=(20, 28, 50, 240), outline=(250, 210, 70, 255), width=2)
    draw.text((width * .56, height * .41), 'FrameRudiments', fill=(245, 235, 215, 255))
    for i in range(7):
        at = int(width * (.53 + .04 * i))
        draw.line((at, height * .64, at, height * (.71 + .035 * (i % 3))), fill=(250, 220, 110, 190), width=3)
    return image


def panel(image):
    y, x = np.indices((image.height, image.width))
    grid = np.where((x // 12 + y // 12) % 2, 33, 48).astype(np.uint8)
    bg = Image.fromarray(np.stack([grid, grid, grid, np.full_like(grid, 255)], axis=-1))
    return Image.alpha_composite(bg, image).convert('RGB')


def build(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='raster-probe-', dir=output.parent) as temp:
        a = Path(temp) / 'a'
        a.write_bytes(b'probe')
        assert a.read_bytes() == b'probe'
        a.rename(Path(temp) / 'b')
        (Path(temp) / 'b').unlink()
    output.mkdir(exist_ok=False)
    source = source_image()
    source.save(output / 'source.png')
    cases = [
        ('Original alpha', {'raster_mix': 0}),
        ('Pixels / retained alpha', {'raster_type': 'pixelate', 'pixel_size': [10, 8]}),
        ('Pixels / averaged alpha', {'raster_type': 'pixelate', 'pixel_size': [14, 12], 'pixel_alpha': 'average'}),
        ('Bayer 4 / four levels', {'raster_type': 'dither'}),
        ('Bayer 8 / monochrome', {'raster_type': 'dither', 'dither_pattern': 'bayer8', 'dither_levels': 2, 'dither_monochrome': True}),
        ('Noise / two levels', {'raster_type': 'dither', 'dither_pattern': 'noise', 'dither_levels': 2, 'raster_seed': 31}),
        ('Horizontal scanlines', {'raster_type': 'scanlines', 'scan_strength': .85}),
        ('Vertical fine scanlines', {'raster_type': 'scanlines', 'scan_axis': 'x', 'scan_spacing': 2.7, 'scan_phase': .13}),
        ('CRT / RGB mask', {'raster_type': 'crt', 'crt_gain': 1.2, 'crt_mask_strength': .65}),
        ('CRT / curved BGR', {'raster_type': 'crt', 'crt_mask': 'bgr', 'crt_curvature': .45, 'crt_noise': .06, 'crt_gain': 1.2}),
        ('Glitch / band motion', {'raster_type': 'glitch', 'glitch_density': .7, 'glitch_offset': [35, 4], 'raster_seed': 12}),
        ('Glitch / chromatic bands', {'raster_type': 'glitch', 'glitch_density': .65, 'glitch_offset': [22, 0], 'glitch_chromatic': 8, 'raster_seed': 12}),
        ('Sort / luminance rows', {'raster_type': 'pixel_sort', 'sort_low': .25, 'sort_high': .85}),
        ('Sort / red columns', {'raster_type': 'pixel_sort', 'sort_axis': 'y', 'sort_key': 'red', 'sort_reverse': True}),
        ('Sort / bounded runs', {'raster_type': 'pixel_sort', 'sort_key': 'green', 'sort_span': 24}),
        ('Sort / half mix', {'raster_type': 'pixel_sort', 'sort_key': 'blue', 'raster_mix': .5}),
    ]
    tile_w, tile_h = source.width + 16, source.height + 38
    sheet = Image.new('RGB', (4 * tile_w, 4 * tile_h + 48), '#151c29')
    draw = ImageDraw.Draw(sheet)
    draw.text((16, 14), 'FrameRudiments M7.4 - actual CPU reference renders', fill='#f5ead6')
    records = []
    for i, (label, config) in enumerate(cases):
        image = render_raster(source, config)
        name = f'{i:02d}-{config.get("raster_type", "original")}.png'
        image.save(output / name)
        x, y = (i % 4) * tile_w, 48 + (i // 4) * tile_h
        draw.text((x + 8, y + 5), label, fill='#f5ead6')
        sheet.paste(panel(image), (x + 8, y + 26))
        records.append({'name': name, 'label': label, 'config': config,
                        'pixels_sha256': hashlib.sha256(image.tobytes()).hexdigest()})
    sheet.save(output / 'raster-contact-sheet.png')
    manifest = {'schema': 'framerudiments.raster-qa.v1', 'synthetic': True,
                'source_pixels_sha256': hashlib.sha256(source.tobytes()).hexdigest(), 'cases': records}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output), indent=2))
