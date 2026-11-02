"""Build actual synthetic Julia-set compositor evidence with native motion."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw, ImageFont

from framerudiments import Project, __version__
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.io import write_json


PALETTE = [
    {"id": "void", "position": 0.0, "color": "#05040f"},
    {"id": "indigo", "position": 0.28, "color": "#312e81"},
    {"id": "violet", "position": 0.5, "color": "#a855f7"},
    {"id": "cyan", "position": 0.76, "color": "#22d3ee"},
    {"id": "white", "position": 1.0, "color": "#fff7ed"},
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def project_data(original):
    data = deepcopy(original.data)
    data["title"] = "Julia fractal compositor demonstration"
    data["width"], data["height"] = 320, 180
    data["fps"] = 30
    data["fps_reason"] = "Small documentation preview; production renders retain the project delivery rate."
    data["render_quality"] = {"enabled": False}
    data["effect_graph"] = {
        "nodes": [
            {"id": "frame", "type": "source"},
            {
                "id": "julia", "type": "procedural_field", "inputs": ["frame"],
                "field_type": "julia", "field_output": "color",
                "field_scale": [95, 95], "field_julia_c": [-.745, .113],
                "field_julia_orbit_radius": .16, "field_julia_iterations": 72,
                "field_julia_escape_radius": 4, "field_palette": PALETTE,
                "field_gamma": .25,
                "field_color_space": "oklab", "field_interpolation": "smoothstep",
            },
        ],
        "output": "julia",
    }
    data["effect_graph_controls"] = [
        {"id": "constant-orbit", "node": "julia", "property": "field_julia_phase",
         "motion": {"kind": "named", "pattern": "akwf_round_saw"},
         "axis": "x", "range": [-1, 1]},
        {"id": "breathing-zoom", "node": "julia", "property": "field_julia_zoom",
         "motion": {"kind": "named", "pattern": "lfo_breathe"},
         "axis": "x", "range": [.85, 1.35]},
        {"id": "slow-turn", "node": "julia", "property": "field_julia_rotation",
         "motion": {"kind": "named", "pattern": "sway"},
         "axis": "x", "range": [-.12, .12]},
        {"id": "orbit-breath", "node": "julia", "property": "field_julia_orbit_radius",
         "motion": {"kind": "named", "pattern": "lfo_breathe"},
         "axis": "x", "range": [.08, .24]},
    ]
    return data


def contact_sheet(frames, font_path):
    panel_width, panel_height = 256, 144
    heading = 34
    sheet = Image.new("RGB", (panel_width * len(frames), panel_height + heading), "#05040f")
    font = ImageFont.truetype(str(font_path), 16)
    draw = ImageDraw.Draw(sheet)
    for column, (frame, image) in enumerate(frames):
        left = column * panel_width
        sheet.paste(image.resize((panel_width, panel_height), Image.Resampling.LANCZOS).convert("RGB"),
                    (left, heading))
        draw.text((left + 9, 8), f"frame {frame} / {frame / 30:.2f}s", font=font, fill="#fff7ed")
    return sheet


def build(output, font, ffmpeg=None):
    output, font = Path(output).resolve(), Path(font).resolve()
    output.mkdir(parents=True, exist_ok=False)
    assets = output / "_project"
    path = create_demo(assets, str(font), width=320, height=180)
    original = Project.load(path)
    data = project_data(original)
    write_json(path, data)
    project = Project.load(path)
    frame_dir = output / "frames"
    frame_dir.mkdir()
    selected = []
    compositor = Compositor(project)
    try:
        for frame in range(61):
            image = compositor.frame(frame, apply_quality=False)[0]
            if frame < 60:
                image.save(frame_dir / f"frame-{frame:04d}.png")
            if frame in {0, 15, 30, 45, 60}:
                selected.append((frame, image.copy()))
        loop_equal = selected[0][1].tobytes() == selected[-1][1].tobytes()
        if not loop_equal:
            raise RuntimeError("The four-beat native motion endpoint did not return to frame zero")
        controls = compositor.effect_graph_controls.values(compositor.context(30), compositor.effect_graph)
    finally:
        compositor.close()
    still = output / "julia-fractal-composite.png"
    contact_sheet(selected, font).save(still)
    video = None
    command = None
    if ffmpeg:
        video = output / "julia-fractal-motion.mp4"
        command = [
            str(Path(ffmpeg).resolve()), "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", "30", "-i", str(frame_dir / "frame-%04d.png"),
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video),
        ]
        subprocess.run(command, check=True)
    evidence = {
        "schema": "framerudiments.julia-demo.v1",
        "framerudiments": __version__,
        "project_sha256": sha(path),
        "frames": 60,
        "fps": 30,
        "duration_seconds": 2,
        "native_motion_period_beats": 4,
        "frame_0_equals_frame_60": loop_equal,
        "normalization": {"recording_and_motion_range": [-1, 1], "period_radians": 2 * 3.141592653589793},
        "frame_30_controls": {f"{node}.{prop}": value for (node, prop), value in controls.items()},
        "still": {"path": still.name, "sha256": sha(still)},
        "video": None if video is None else {"path": video.name, "sha256": sha(video)},
        "ffmpeg_command": command,
        "audio": "none; silent synthetic documentation preview",
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
