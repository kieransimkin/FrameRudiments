"""Bounded CPU measurement for M5.3; not a GPU performance claim."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph_transition_geometry import geometric_transition  # noqa: E402

SIZES = {'small': (320, 180), 'hd': (1920, 1080),
         '4k': (3840, 2160), '8k': (7680, 4320)}


def run(size, style, repeats):
    width, height = size
    first = Image.new('RGBA', size)
    second = Image.new('RGBA', size)
    ImageDraw.Draw(first).ellipse((width // 9, height // 9, 7*width // 9, 7*height // 9),
                                  fill=(245, 60, 40, 238))
    ImageDraw.Draw(second).rectangle((width // 3, height // 4, 9*width // 10, 8*height // 9),
                                      fill=(45, 180, 245, 236))
    config = {'transition_style': style, 'progress': .45, 'feather': .12,
              'strength': .85, 'amplitude': 26, 'frequency': 3,
              'twist': 1.2, 'rotation': 260, 'zoom': 1.25, 'segments': 8}
    samples = []
    digest = None
    for _ in range(repeats):
        started = time.perf_counter()
        output = geometric_transition(first, second, config)
        seconds = time.perf_counter() - started
        image_hash = hashlib.sha256(output.tobytes()).hexdigest()
        if digest is not None and digest != image_hash:
            raise AssertionError('geometric transition must be deterministic')
        digest = image_hash
        samples.append(seconds)
    return {'size': [width, height], 'style': style, 'backend': 'cpu',
            'repeats': repeats, 'median_seconds': statistics.median(samples),
            'min_seconds': min(samples), 'max_seconds': max(samples),
            'sha256': digest, 'frames_per_second_median': 1 / statistics.median(samples)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--size', choices=SIZES, default='small')
    parser.add_argument('--style', choices=['zoom', 'rotate', 'polar', 'kaleidoscope', 'liquid'],
                        default='liquid')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--allow-8k', action='store_true', help='Explicitly permit a high-memory run')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error('--repeats must be 1..10')
    if args.size == '8k' and not args.allow_8k:
        parser.error('8K requires --allow-8k')
    result = run(SIZES[args.size], args.style, args.repeats)
    payload = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding='utf-8')
    print(payload)
