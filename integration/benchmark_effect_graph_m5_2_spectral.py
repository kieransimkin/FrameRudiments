"""Measured CPU spectral-transition timings; GPU acceleration is not claimed."""
import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from PIL import Image

from framerudiments.effect_graph import EffectGraph

SIZES = {'small': (320, 180), 'hd': (1920, 1080), '4k': (3840, 2160), '8k': (7680, 4320)}


def benchmark(size, repeats):
    w, h = SIZES[size]
    incoming = Image.new('RGBA', (w, h), (20, 40, 230, 255))
    outgoing = Image.new('RGBA', (w, h), (235, 60, 30, 255))
    graph = EffectGraph({'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'transition', 'type': 'transition', 'transition_style': 'spectral',
         'inputs': ['a', 'b'], 'progress': .5, 'bands': [
             {'id': 'warm', 'source_color': '#eb3c1e', 'to_color': '#1428e6',
              'space': 'rgb', 'tolerance': .08, 'softness': .08,
              'offset_from': [1.25, 0], 'offset_to': [-1, .5],
              'start': 0, 'end': .8},
         ]},
    ], 'output': 'transition'})
    times = []
    for _ in range(repeats):
        start = perf_counter()
        frame = graph.render({'a': outgoing, 'b': incoming})
        assert frame.size == (w, h)
        times.append(perf_counter() - start)
    return {'size': size, 'dimensions': [w, h], 'backend': 'CPU reference',
            'repeats': repeats, 'seconds': times, 'median_seconds': median(times),
            'frames_per_second': 1 / median(times)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--size', choices=SIZES, default='small')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 20:
        parser.error('repeats must be 1..20')
    result = benchmark(args.size, args.repeats)
    payload = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(payload, encoding='utf-8')
    print(payload)
