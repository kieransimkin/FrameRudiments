"""M4.2 reproducible spectral CPU/GPU benchmark with explicit memory gating.

Measurements are end-to-end, including Pillow->GPU upload and GPU->Pillow
readback, not isolated shader kernel timings. The script refuses to call a
skipped or unavailable GPU a successful benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time

from PIL import Image, ImageDraw

from framerudiments.effect_graph_spectral import spectral_displacement

SIZES = {"smoke": (320, 180), "hd": (1920, 1080),
         "4k": (3840, 2160), "8k": (7680, 4320)}


def make_case(size, bands=3):
    image = Image.new("RGBA", size)
    draw = ImageDraw.Draw(image)
    width, height = size
    palette = ((245, 10, 30), (15, 230, 65), (30, 40, 245))
    selected = []
    for index, colour in enumerate(palette[:bands]):
        start = ((index + 1) * width // 6, (index + 1) * height // 7)
        end = (min(width - 1, start[0] + width // 3),
               min(height - 1, start[1] + height // 3))
        draw.ellipse((*start, *end), fill=(*colour, 170 + index * 30))
        selected.append({
            "id": f"band{index}",
            "source_color": "#%02x%02x%02x" % colour,
            "target_color": "#%02x%02x%02x" % palette[(index + 1) % 3],
            "space": ["rgb", "hsv", "oklab"][index],
            "tolerance": .10, "softness": .07,
            "offset": [4.25 + index * 1.5, -3.5 + index],
            "opacity": .8, "recolor": .65,
        })
    spec = {"bands": selected, "spectral_mode": "replace", "alpha_mode": "expanded", "mix": .8}
    return image, spec


def samples(call, repeats, warmup=1):
    for _ in range(warmup):
        call()
    durations = []
    output = None
    for _ in range(repeats):
        start = time.perf_counter()
        output = call()
        durations.append((time.perf_counter() - start) * 1000)
    return {"median_ms": round(statistics.median(durations), 3),
            "min_ms": round(min(durations), 3),
            "samples_ms": [round(ms, 3) for ms in durations],
            "sha256": hashlib.sha256(output.tobytes()).hexdigest()}


def benchmark(size, *, repeats=3, budget_mib=768, backend="egl", cpu=False):
    from framerudiments.effect_graph_gpu import GLMatteBackend

    image, spec = make_case(size)
    with GLMatteBackend(backend=backend, max_working_set_bytes=int(budget_mib * 1024**2)) as gl:
        gpu = samples(lambda: gl.spectral_displacement(image, spec), repeats)
        report = {
            "resolution": f"{size[0]}x{size[1]}",
            "bands": len(spec["bands"]), "backend": "moderngl-egl-spectral",
            "renderer": gl.capabilities.renderer,
            "gl_version": gl.capabilities.context_version,
            "working_set_budget_mib": budget_mib,
            "estimated_four_texture_mib": round(size[0] * size[1] * 16 / 1024**2, 2),
            "timing_scope": "upload + spectral passes + readback",
            "gpu": gpu,
        }
    if cpu:
        cpu_measure = samples(lambda: spectral_displacement(image, spec), repeats)
        report["cpu"] = cpu_measure
        report["relative_speedup"] = round(cpu_measure["median_ms"] / gpu["median_ms"], 3)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", choices=(*SIZES, "all"), default="smoke")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--budget-mib", type=int, default=768)
    parser.add_argument("--backend", default="egl")
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--allow-large", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be 1..20")
    if args.budget_mib < 64:
        parser.error("--budget-mib must be at least 64")
    selected = list(SIZES) if args.size == "all" else [args.size]
    if any(name in {"4k", "8k"} for name in selected) and not args.allow_large:
        parser.error("4K/8K require --allow-large")
    try:
        results = [benchmark(SIZES[name], repeats=args.repeats,
                             budget_mib=args.budget_mib, backend=args.backend,
                             cpu=args.cpu) for name in selected]
    except (ImportError, RuntimeError, ValueError) as exc:
        print(f"GPU benchmark failed: {exc}", file=sys.stderr)
        return 1
    payload = {"schema": "framerudiments.spectral-benchmark.v1",
               "results": results}
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
