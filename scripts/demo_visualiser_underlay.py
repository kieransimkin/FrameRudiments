"""Render a synthetic native alpha-underlay demonstration; no GPU preset or song."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

from framerudiments import __version__
from framerudiments.visual_style import VisualStyle
from framerudiments.visualiser_compositing import composite_stage, validate_stage_composite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    size = (320, 180)
    foreground = Image.new("RGBA", size, "#f18645")
    backdrop = Image.new("RGBA", size, "#133849")
    front_draw, back_draw = ImageDraw.Draw(foreground), ImageDraw.Draw(backdrop)
    for x in range(0, size[0], 20):
        front_draw.line((x, 0, x, size[1]), fill="#5d2419", width=2)
        back_draw.line((x, 0, x, size[1]), fill="#50b8c5", width=1)
    for y in range(0, size[1], 20):
        front_draw.line((0, y, size[0], y), fill="#5d2419", width=2)
        back_draw.line((0, y, size[0], y), fill="#50b8c5", width=1)
    setting = validate_stage_composite({"underlay": {"source": "layer:backdrop"}})
    video = args.output / "visualiser-underlay.mp4"
    process = subprocess.Popen([
        args.ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", "320x180", "-r", "60", "-i", "-", "-an", "-c:v", "libx264",
        "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video),
    ], stdin=subprocess.PIPE)
    checks = []
    frames = []
    try:
        for frame in range(120):
            t = frame / 119
            transformed = VisualStyle({
                "scale": max(.1, 1 - .9 * t), "rotation": -270 * t,
                "opacity": max(0, min(1, (1 - t) * 5)),
            }).apply(foreground)
            result = composite_stage(foreground, transformed, setting, {"layer:backdrop": backdrop})
            process.stdin.write(result.convert("RGB").tobytes())
            if frame in (0, 60, 119):
                frames.append(result.convert("RGB"))
                checks.append({"frame": frame, "corner": result.getpixel((0, 0))})
            if frame == 119:
                assert result.tobytes() == backdrop.tobytes()
    finally:
        process.stdin.close()
        status = process.wait()
    if status:
        raise RuntimeError(f"FFmpeg exited {status}")
    sheet = Image.new("RGB", (960, 180))
    for index, frame in enumerate(frames):
        sheet.paste(frame, (320 * index, 0))
    sheet.save(args.output / "visualiser-underlay.png")
    subprocess.run([args.ffmpeg, "-v", "error", "-i", str(video), "-f", "null", "-"], check=True)
    receipt = {
        "version": __version__, "scope": __doc__, "frames": 120, "fps": 60,
        "audio": "none", "checks": checks, "final_exact_underlay": True,
        "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
    }
    (args.output / "visualiser-underlay.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
