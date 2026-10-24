"""Run opt-in real-engine feedback-writeback smoke checks.

This script needs the documented Butterchurn and projectM environment variables.
It writes tiny synthetic PNG evidence and a JSON receipt; no campaign media is used.
"""

import argparse
import hashlib
import json
from array import array
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageChops, ImageDraw

from framerudiments.projectm_backend import ProjectMBackend
from framerudiments.visualisers import ASSETS, ButterchurnBackend


def digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def input_card(size):
    image = Image.new("RGBA", size, "#10182f")
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, size[0] - 8, size[1] - 8), fill="#ff5b87")
    draw.line((0, size[1] - 1, size[0] - 1, 0), fill="#4ee9ff", width=5)
    return image


def run_engine(name, factory, preset, output):
    size = (96, 64)
    pcm = array("f", [0.0] * 2048)
    card = input_card(size)
    transparent = Image.new("RGBA", size)
    override = Image.new("RGBA", size, "#ff00ff")

    def trial(writeback):
        engine = factory(size, 60, preset, 0.72, 41, output)
        try:
            first = engine.process(card, SimpleNamespace(frame=0, output_seconds=0), pcm)
            if writeback:
                engine.set_feedback(override)
            second = engine.process(
                transparent, SimpleNamespace(frame=1, output_seconds=1 / 60), pcm
            )
            return first, second
        finally:
            engine.close()

    first_control, control = trial(False)
    first_treated, treated = trial(True)
    try:
        difference = ImageChops.difference(control.convert("RGB"), treated.convert("RGB"))
        bbox = difference.getbbox()
        if bbox is None:
            raise AssertionError(f"{name} feedback writeback produced no next-frame pixel change")
        control.save(output / f"{name}-control.png")
        treated.save(output / f"{name}-feedback-hook.png")
        difference.save(output / f"{name}-difference.png")
        return {
            "engine": name,
            "frame0_hashes": [digest(first_control), digest(first_treated)],
            "control_hash": digest(control),
            "feedback_hook_hash": digest(treated),
            "difference_bbox": list(bbox),
            "difference_extrema": [list(value) for value in difference.getextrema()],
        }
    finally:
        del first_control, first_treated, control, treated


def write_receipt(path, receipt):
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--engine",
        choices=("all", "butterchurn", "projectm"),
        default="all",
        help="Run both engines or a single bounded recovery trial.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    receipt = {
        "schema": "framerudiments.feedback-writeback-smoke.v1",
        "synthetic": True,
        "engines": [],
    }
    if args.engine in {"all", "butterchurn"}:
        receipt["engines"].append(
            run_engine(
                "butterchurn",
                ButterchurnBackend,
                ASSETS / "feedback.json",
                args.output,
            )
        )
        write_receipt(args.output / "receipt.json", receipt)
    if args.engine in {"all", "projectm"}:
        receipt["engines"].append(
            run_engine(
                "projectm",
                ProjectMBackend,
                ASSETS / "feedback.milk",
                args.output,
            )
        )
        write_receipt(args.output / "receipt.json", receipt)


if __name__ == "__main__":
    main()
