"""Build actual seven-family compositor evidence with native DanceRudiments motion."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont

from framerudiments import FRACTAL_FAMILIES, Project, __version__
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.io import write_json


PALETTE = [
    {"id": "ink", "position": 0.0, "color": "#030712"},
    {"id": "violet", "position": .28, "color": "#6d28d9"},
    {"id": "cyan", "position": .57, "color": "#06b6d4"},
    {"id": "amber", "position": .81, "color": "#f59e0b"},
    {"id": "paper", "position": 1.0, "color": "#fff7ed"},
]
FAMILIES = ["julia", "mandelbrot", "tricorn", "burning_ship", "celtic", "buffalo", "newton"]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def project_data(original, family):
    data = deepcopy(original.data)
    data["title"] = f"{family} fractal compositor demonstration"
    data["width"], data["height"], data["fps"] = 192, 108, 15
    data["fps_reason"] = "Small documentation preview; production renders retain the project delivery rate."
    data["render_quality"] = {"enabled": False}
    node = {
        "id": "fractal", "type": "procedural_field", "inputs": ["frame"],
        "field_type": family, "field_output": "color", "field_scale": [58, 58],
        "field_gamma": .28, "field_palette": PALETTE,
        "field_color_space": "oklab", "field_interpolation": "smoothstep",
    }
    if family == "julia":
        node.update(field_julia_c=[-.745, .113], field_julia_orbit_radius=.14,
                    field_julia_iterations=56)
        controls = [
            ("field_julia_phase", "akwf_round_saw", [-1, 1]),
            ("field_julia_zoom", "lfo_breathe", [.85, 1.35]),
            ("field_julia_rotation", "sway", [-.12, .12]),
        ]
    elif family == "newton":
        node.update(field_newton_iterations=36, field_newton_degree=3)
        controls = [
            ("field_newton_phase", "akwf_round_saw", [-1, 1]),
            ("field_newton_zoom", "lfo_breathe", [.85, 1.35]),
            ("field_newton_rotation", "sway", [-.12, .12]),
        ]
    else:
        node.update(field_fractal_iterations=56, field_fractal_orbit_radius=.1)
        controls = [
            ("field_fractal_phase", "akwf_round_saw", [-1, 1]),
            ("field_fractal_zoom", "lfo_breathe", [.85, 1.35]),
            ("field_fractal_rotation", "sway", [-.12, .12]),
        ]
    data["effect_graph"] = {
        "nodes": [{"id": "frame", "type": "source"}, node], "output": "fractal",
    }
    data["effect_graph_controls"] = [
        {"id": f"{family}-{index}", "node": "fractal", "property": prop,
         "motion": {"kind": "named", "pattern": pattern}, "axis": "x", "range": span}
        for index, (prop, pattern, span) in enumerate(controls)
    ]
    return data


def labelled_grid(images, font, *, title=None):
    columns, panel_width, panel_height, label_height = 4, 192, 108, 26
    rows = 2
    title_height = 32 if title else 0
    canvas = Image.new("RGB", (columns * panel_width, rows * (panel_height + label_height) + title_height), "#030712")
    draw = ImageDraw.Draw(canvas)
    small = ImageFont.truetype(str(font), 15)
    heading = ImageFont.truetype(str(font), 18)
    if title:
        draw.text((10, 5), title, font=heading, fill="#fff7ed")
    for index, (family, image) in enumerate(images):
        x = (index % columns) * panel_width
        y = (index // columns) * (panel_height + label_height) + title_height
        canvas.paste(image.convert("RGB"), (x, y))
        draw.text((x + 8, y + panel_height + 4), family.replace("_", " ").title(), font=small, fill="#fff7ed")
    return canvas


def build(output, font, ffmpeg=None):
    output, font = Path(output).resolve(), Path(font).resolve()
    output.mkdir(parents=True, exist_ok=False)
    frame_dir = output / "frames"
    frame_dir.mkdir()
    compositors, project_hashes = [], {}
    try:
        for family in FAMILIES:
            project_dir = output / "_projects" / family
            path = create_demo(project_dir, str(font), width=192, height=108)
            original = Project.load(path)
            write_json(path, project_data(original, family))
            project_hashes[family] = sha(path)
            compositors.append((family, Compositor(Project.load(path))))
        selected = None
        endpoint = None
        for frame in range(31):
            images = [(family, compositor.frame(frame, apply_quality=False)[0])
                      for family, compositor in compositors]
            if frame == 8:
                selected = [(family, image.copy()) for family, image in images]
            if frame in {0, 30}:
                candidate = [image.tobytes() for _, image in images]
                endpoint = candidate if endpoint is None else (endpoint, candidate)
            if frame < 30:
                labelled_grid(images, font).save(frame_dir / f"frame-{frame:04d}.png")
        loop_equal = endpoint[0] == endpoint[1]
        if not loop_equal:
            raise RuntimeError("The four-beat native motion endpoint did not return to frame zero")
    finally:
        for _, compositor in compositors:
            compositor.close()
    still = output / "fractal-library.png"
    labelled_grid(selected, font, title="Seven compositable fractal families").save(still)
    video, command = None, None
    if ffmpeg:
        video = output / "fractal-library-motion.mp4"
        command = [
            str(Path(ffmpeg).resolve()), "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", "15", "-i", str(frame_dir / "frame-%04d.png"),
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video),
        ]
        subprocess.run(command, check=True)
    evidence = {
        "schema": "framerudiments.fractal-library-demo.v1",
        "framerudiments": __version__, "families": FAMILIES,
        "exported_families": sorted(FRACTAL_FAMILIES),
        "project_sha256": project_hashes, "frames": 30, "fps": 15,
        "duration_seconds": 2, "native_motion_period_beats": 4,
        "frame_0_equals_frame_30": loop_equal,
        "normalization": {"motion_range": [-1, 1], "period_radians": 2 * 3.141592653589793},
        "still": {"path": still.name, "sha256": sha(still)},
        "video": None if video is None else {"path": video.name, "sha256": sha(video)},
        "ffmpeg_command": command, "audio": "none; silent synthetic documentation preview",
        "review": "actual compositor output; perceptual approval not implied",
    }
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("--font", required=True)
    parser.add_argument("--ffmpeg")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.font, args.ffmpeg), indent=2))


if __name__ == "__main__":
    main()
