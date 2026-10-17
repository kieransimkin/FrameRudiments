"""Synthetic rendering speed, memory and reproducibility evidence.

These are observations, not pass/fail time gates. Used by the Pages gallery.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import time
import tracemalloc

from framerudiments.rudiment_renderers import default_renderers
from framerudiments.super_sampling import vector_viewport
from generate_visualisation_gallery import fixture

CASES = (
    ("waveform-oscilloscope", "motion"),
    ("spectrum-annular-bars", "spectrum"),
    ("spectrogram-linear", "spectrogram"),
)


def profile():
    registry = default_renderers()
    results = []
    for name, family in CASES:
        for scale in (1, 4):
            source = fixture(95, spectral=family != "motion")
            args = (registry, name, (320, 210), {"background": "#142238"}, source, scale)
            tracemalloc.start()
            start = time.perf_counter()
            image, _ = vector_viewport(*args, cache={})
            elapsed = time.perf_counter() - start
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            digest = sha256(image.tobytes()).hexdigest()
            again, _ = vector_viewport(*args, cache={})
            if image.tobytes() != again.tobytes():
                raise RuntimeError(f"Non-deterministic profile frame: {name}")
            results.append(
                {
                    "renderer": name,
                    "spatial_scale": scale,
                    "width": image.width,
                    "height": image.height,
                    "sha256": digest,
                    "elapsed_ms": round(1000 * elapsed, 3),
                    "peak_python_bytes": peak,
                }
            )
    return {
        "schema": "framerudiments.visualisation-profile.v1",
        "synthetic": True,
        "timings_are_nonblocking": True,
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
