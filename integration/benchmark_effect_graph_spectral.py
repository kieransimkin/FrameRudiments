"""Bounded, reproducible CPU spectral-displacement performance probe.

Explicitly reports CPU execution, not GPU speed. 4K/8K are opt-in because
larger canvases require substantial allocations and measured system headroom.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time

from PIL import Image, ImageDraw

from framerudiments.effect_graph_spectral import spectral_displacement


SIZES = {"smoke": (320, 180), "hd": (1920, 1080), "4k": (3840, 2160), "8k": (7680, 4320)}


def benchmark(size, repeats=3):
    image = Image.new("RGBA", size)
    painter = ImageDraw.Draw(image)
    painter.rectangle((size[0] // 5, size[1] // 5, size[0] * 4 // 5, size[1] * 4 // 5),
                      fill=(240, 40, 170, 255))
    spec = {"bands": [{"id": "pink", "source_color": "#f028aa", "space": "rgb",
                       "tolerance": .06, "softness": .08, "offset": [5.25, 2.5]}]}
    values = []
    for _ in range(repeats):
        start = time.perf_counter()
        output = spectral_displacement(image, spec)
        values.append((time.perf_counter() - start) * 1000)
        if output.size != image.size:
            raise AssertionError("invalid spectral benchmark output dimensions")
    return {"backend": "cpu-reference", "width": size[0], "height": size[1],
            "iterations": repeats, "median_ms": round(statistics.median(values), 3),
            "samples_ms": [round(v, 3) for v in values]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=SIZES, default="smoke")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--allow-large", action="store_true")
    args = parser.parse_args()
    if args.iterations < 1 or args.iterations > 20:
        parser.error("iterations must be 1..20")
    if args.size in {"4k", "8k"} and not args.allow_large:
        parser.error("4k/8k require explicit --allow-large")
    print(json.dumps(benchmark(SIZES[args.size], args.iterations), indent=2))
