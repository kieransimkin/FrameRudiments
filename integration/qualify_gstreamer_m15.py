"""Require real GStreamer H.264 encodes and FFmpeg exact-PTS/decode QA.

This is deliberately outside the default pytest test tree: the ordinary
package/CPU matrix has no GStreamer dependency; the dedicated workflow does.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

from framerudiments.gstreamer_m15 import SCHEMA, capabilities, encode_video


def _digest(file):
    return hashlib.sha256(Path(file).read_bytes()).hexdigest()


def _fixture(folder, fps, *, size, frames):
    folder.mkdir(parents=True)
    image_refs = []
    for n in range(frames):
        image = Image.new("RGB", size, (20 + n * 5 % 210, 44, 150))
        ink = ImageDraw.Draw(image)
        ink.rectangle((n * 6 % (size[0] - 20), 12, n * 6 % (size[0] - 20) + 18, 35),
                      fill=(245, 200 - n * 4 % 150, 40))
        target = folder / f"native-{n:03d}.png"
        image.save(target)
        image_refs.append({"path": target.name, "sha256": _digest(target)})
    document = {"schema": "framerudiments.video-frames.v1",
                "width": size[0], "height": size[1], "fps": fps, "frames": image_refs}
    manifest = folder / "manifest.json"
    manifest.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _decode_all(video, *, width, height, count):
    command = ["ffmpeg", "-v", "error", "-xerror", "-i", str(video),
               "-map", "0:v:0", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    response = subprocess.run(command, capture_output=True, timeout=90, check=True)
    expected = width * height * count * 3
    if len(response.stdout) != expected:
        raise RuntimeError("Independent FFmpeg RGB decode has wrong byte length")
    chunk = width * height * 3
    if response.stdout[:chunk] == response.stdout[-chunk:]:
        raise RuntimeError("Real GStreamer output did not preserve frame-to-frame changes")
    return Image.frombytes("RGB", (width, height), response.stdout[-chunk:])


def qualify(output):
    out = Path(output).absolute()
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError("Native GStreamer qualification needs a new output directory")
    out.mkdir()
    report = capabilities()
    if not report["available"]:
        raise RuntimeError("CI must install the complete native GStreamer pipeline: " + report["reason"])
    results = []
    for fps, size in (("24000/1001", (128, 96)), ("30000/1001", (130, 96)),
                      ("60", (128, 96))):
        name = fps.replace("/", "_")
        folder = out / name
        manifest = _fixture(folder, fps, size=size, frames=18)
        video = folder / "gstreamer.mp4"
        receipt = encode_video(manifest, video, bitrate_kbps=1200, preset="veryfast")
        if (receipt["schema"] != SCHEMA or receipt["encoding"]["frames"] != 18 or
                receipt["independent_ffmpeg_verification"]["frames"] != 18 or
                receipt["independent_ffmpeg_verification"]["fps"] != str(Fraction(fps)) or
                not receipt["exact_pts_qualified"] or
                _digest(video) != receipt["video_sha256"]):
            raise RuntimeError("M15.1 GStreamer qualification receipt mismatch")
        last = _decode_all(video, width=size[0], height=size[1], count=18)
        last.save(folder / "last-frame.png")
        results.append({"fps": fps, "size": size, "frames": 18,
                        "output_sha256": receipt["video_sha256"],
                        "poster_sha256": _digest(folder / "last-frame.png"),
                        "verified_timestamps": receipt["independent_ffmpeg_verification"]["timestamps"]})
    proof = {"schema": "framerudiments.gstreamer-qualification.v1", "cases": results,
             "producer_version": report["version"], "independent_decode": "passed",
             "physical_gpu": "not-tested", "network_input": "none", "audio": "not-included"}
    (out / "qualification.json").write_text(json.dumps(proof, sort_keys=True, indent=2) + "\n",
                                            encoding="utf-8")
    return proof


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--output", required=True)
    args = cli.parse_args()
    result = qualify(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
