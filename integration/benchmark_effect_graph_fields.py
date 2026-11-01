"""Bounded end-to-end single-node CPU benchmark; no project/encode/GPU claim."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import time

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph_fields import render_procedural_field  # noqa: E402


def benchmark(width, height, repeats=3):
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError('repeats must be 1..20')
    if not 1 <= width <= 16384 or not 1 <= height <= 16384 or width*height > 33_177_600:
        raise ValueError('canvas exceeds the field budget')
    reference = Image.new('RGBA', (width, height))
    specs = {
        'linear-palette': dict(field_type='linear', field_output='color', field_scale=[width, height]),
        'fbm': dict(field_type='fbm', field_scale=[128, 128]),
        'cellular': dict(field_type='cellular', field_scale=[128, 128]),
        'curl-fbm': dict(field_type='fbm', field_output='vector', field_vector_gain=32, field_scale=[128, 128]),
    }
    rows = []
    for name, config in specs.items():
        warm = render_procedural_field(reference, config)
        expected = hashlib.sha256(warm.tobytes()).hexdigest()
        samples = []
        for _ in range(repeats):
            start = time.perf_counter()
            out = render_procedural_field(reference, config)
            samples.append(time.perf_counter() - start)
            if hashlib.sha256(out.tobytes()).hexdigest() != expected:
                raise RuntimeError('Non-reproducible benchmark output')
        rows.append(dict(name=name, config=config, seconds=samples,
                         mean_seconds=sum(samples)/len(samples), rgba_sha256=expected))
    return dict(schema='framerudiments.procedural-field-benchmark.v1',
                python=sys.version, platform=platform.platform(),
                numpy=importlib.metadata.version('numpy'), pillow=importlib.metadata.version('Pillow'),
                size=[width, height], repeats=repeats, warmup_per_variant=1, backend='cpu',
                scope='single node including output allocation; excludes hashing, project, decode, encode',
                samples=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output file; existing evidence is not overwritten')
    data = benchmark(args.width, args.height, args.repeats)
    args.output.write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
