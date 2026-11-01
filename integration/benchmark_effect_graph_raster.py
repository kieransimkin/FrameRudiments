"""Measure M7.4 CPU kernels only; no claim of project or encoded-video throughput."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

import numpy as np
import PIL

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from framerudiments.effect_graph_raster import render_raster, MAX_DIMENSION, MAX_PIXELS  # noqa: E402
from build_effect_graph_m7_4_visuals import source_image  # noqa: E402


def benchmark(width, height, repeats):
    if (type(width) is not int or type(height) is not int or min(width, height) < 1
            or max(width, height) > MAX_DIMENSION or width * height > MAX_PIXELS):
        raise ValueError('Benchmark dimensions exceed the raster canvas budget')
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError('repeats must be 1..20')
    image = source_image(width, height)
    results = []
    for kind in ('pixelate', 'pixel_sort', 'dither', 'scanlines', 'crt', 'glitch'):
        config = {'raster_type': kind}
        elapsed = []
        checksums = set()
        for _ in range(repeats):
            start = time.perf_counter()
            result = render_raster(image, config)
            elapsed.append(time.perf_counter() - start)
            checksums.add(hashlib.sha256(result.tobytes()).hexdigest())
        if len(checksums) != 1:
            raise RuntimeError(f'Nonrepeatable output for {kind}')
        results.append(dict(config=config, seconds=elapsed,
                            median_seconds=statistics.median(elapsed),
                            pixels_sha256=next(iter(checksums))))
    return dict(schema='framerudiments.raster-benchmark.v1', dimensions=[width, height],
                repeats=repeats, warmup=False, backend='cpu',
                scope='kernel invocation including image allocation; excludes source generation, hashing, encoding and project composition',
                peak_memory_measured=False, hardware_gpu_tested=False,
                runtime=dict(python=platform.python_version(), numpy=np.__version__,
                             pillow=PIL.__version__, platform=platform.system(), machine=platform.machine()),
                results=results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--width', type=int, default=1920)
    parser.add_argument('--height', type=int, default=1080)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='raster-benchmark-probe-', dir=args.output.parent) as temp:
        probe = Path(temp) / 'before'
        probe.write_bytes(b'probe')
        assert probe.read_bytes() == b'probe'
        probe.rename(Path(temp) / 'after')
        (Path(temp) / 'after').unlink()
    # Exclusive creation avoids overwriting a previous measurement report.
    with args.output.open('x', encoding='utf-8') as stream:
        evidence = benchmark(args.width, args.height, args.repeats)
        json.dump(evidence, stream, indent=2)
        stream.write('\n')
    print(json.dumps(evidence, indent=2))
