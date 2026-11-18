"""M15.1: bounded, opt-in GStreamer H.264/MP4 output from pinned M11.4 frames.

Native GStreamer plugins are executable, trusted local dependencies. The adapter
accepts no user-authored pipeline or network source and never modifies Project,
the compositor, audio clocks, or the ordinary FFmpeg encoder. FFmpeg independently
verifies decoded frames and exact rational presentation timestamps before publish.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

from PIL import Image

from .encoding import X264_PRESETS, binaries
from .video_adapters import _digest, _json_file, _manifest, _pinned, _verify_export, _new_output

SCHEMA = "framerudiments.gstreamer-m15.v1"
MAX_FRAMES = 8192
MAX_RAW_BYTES = 512 * 1024 * 1024
MAX_MEDIA_BYTES = 1024 * 1024 * 1024
MAX_LOG_BYTES = 1024 * 1024
ENCODE_TIMEOUT_SECONDS = 240
REQUIRED_ELEMENTS = ("filesrc", "rawvideoparse", "videoconvert", "x264enc", "h264parse",
                     "mp4mux", "filesink")
_PLUGIN_VERSION = re.compile(r"(?m)^\s*Version\s+([0-9][^\r\n]{0,79})$")


def _executable(value, fallback):
    """Admit only resolved regular files; command invocation never uses a shell."""
    candidate = value or shutil.which(fallback)
    if not candidate or not isinstance(candidate, (str, os.PathLike)):
        return None
    try:
        path = Path(candidate).resolve(strict=True)
    except (OSError, ValueError):
        return None
    return str(path) if path.is_file() else None


def _bounded_command(command, *, timeout=20, cwd=None):
    try:
        result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("GStreamer inspection or encode failed to execute") from exc
    if len(result.stdout) > MAX_LOG_BYTES or len(result.stderr) > MAX_LOG_BYTES:
        raise ValueError("GStreamer diagnostic output exceeds its allowed size")
    return result


def capabilities(*, gst_launch=None, gst_inspect=None):
    """Read-only discovery. Missing plugins are not a fake CPU/GPU fallback."""
    launch = _executable(gst_launch, "gst-launch-1.0")
    inspect = _executable(gst_inspect, "gst-inspect-1.0")
    evidence = {"schema": SCHEMA, "available": False, "version": None,
                "elements": {}, "exact_pts_qualified": False, "audio": False,
                "backend": "trusted_local_gstreamer", "hardware_gpu_qualified": False}
    if not launch or not inspect:
        evidence["reason"] = "gst-launch-1.0 and gst-inspect-1.0 are required"
        return evidence
    version = _bounded_command([launch, "--version"])
    if version.returncode:
        evidence["reason"] = "GStreamer version probe failed"
        return evidence
    version_text = version.stdout.decode("utf-8", "replace").splitlines()
    if not version_text or not version_text[0].lower().startswith("gst-launch-1.0 version"):
        evidence["reason"] = "Unrecognized GStreamer version response"
        return evidence
    evidence["version"] = version_text[0][:120]
    for name in REQUIRED_ELEMENTS:
        result = _bounded_command([inspect, name])
        stdout = result.stdout.decode("utf-8", "replace")
        found = result.returncode == 0 and "Factory Details" in stdout and "Plugin Details" in stdout
        version = _PLUGIN_VERSION.search(stdout) if found else None
        evidence["elements"][name] = {"available": bool(found and version),
                                      "plugin_version": version.group(1).strip() if version else None}
    evidence["available"] = all(x["available"] for x in evidence["elements"].values())
    evidence["reason"] = None if evidence["available"] else "Required GStreamer elements are unavailable"
    return evidence


def _strict_local_frames(manifest):
    """M11.4's pin checks plus a stricter no-traversal media-root boundary."""
    path = Path(manifest).resolve(strict=True)
    raw = _json_file(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("frames"), list):
        raise ValueError("Expected a pinned M11.4 video frame manifest")
    root = path.parent
    for entry in raw["frames"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("Malformed pinned frame reference")
        name = entry["path"]
        if (not name or "\\" in name or ":" in name or
                PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute() or
                ".." in PurePosixPath(name).parts):
            raise ValueError("GStreamer input frames must stay under the manifest directory")
    resolved, width, height, fps, rows = _manifest(path)
    if any(not f.is_relative_to(root) for f, _ in rows):
        raise ValueError("GStreamer pinned source resolved outside the manifest directory")
    if len(rows) > MAX_FRAMES:
        raise ValueError("GStreamer M15.1 frame count exceeds 8192")
    stride = (width * 3 + 3) & ~3  # GStreamer RGB's documented RU4 row stride.
    size = stride * height * len(rows)
    if size > MAX_RAW_BYTES:
        raise ValueError("GStreamer M15.1 raw frame budget exceeds 512 MiB")
    return resolved, width, height, fps, rows, stride, size


def _write_raw(rows, width, height, stride, destination):
    """Byte-exact RGB8, including explicit zero padding on odd RGB row strides."""
    padding = b"\0" * (stride - width * 3)
    digest = hashlib.sha256()
    count = 0
    with Path(destination).open("xb") as target:
        for path, pin in rows:
            _pinned(path, pin)
            with Image.open(path) as image:
                image.load()
                if image.format != "PNG" or image.mode != "RGB" or image.size != (width, height):
                    raise ValueError("M15.1 requires identical RGB8 PNG frames")
                pixels = image.tobytes()
            row_bytes = width * 3
            if len(pixels) != row_bytes * height:
                raise ValueError("Decoded M15.1 frame byte count differs")
            for start in range(0, len(pixels), row_bytes):
                line = pixels[start:start + row_bytes] + padding
                target.write(line)
                digest.update(line)
                count += len(line)
            _pinned(path, pin)
    return digest.hexdigest(), count


def pipeline(fps, width, height, *, bitrate_kbps=4000, preset="fast"):
    """A closed list of elements/properties. No arbitrary pipelines, URIs or codecs."""
    if type(bitrate_kbps) is not int or not 100 <= bitrate_kbps <= 50000:
        raise ValueError("M15.1 bitrate must be an integer in 100..50000 kbit/s")
    if preset not in X264_PRESETS:
        raise ValueError("Unsupported GStreamer x264 speed preset")
    if type(width) is not int or type(height) is not int or width < 16 or height < 16:
        raise ValueError("Invalid video dimensions")
    return ["filesrc", "location=frames.raw", "!", "rawvideoparse",
            "use-sink-caps=false", "format=rgb", f"width={width}", f"height={height}",
            f"framerate={fps.numerator}/{fps.denominator}", "!", "videoconvert", "!",
            "video/x-raw,format=I420", "!", "x264enc", "tune=zerolatency",
            f"speed-preset={preset}", f"bitrate={bitrate_kbps}", "bframes=0", "!",
            "h264parse", "!", "mp4mux", f"trak-timescale={fps.numerator}",
            "faststart=true", "!", "filesink", "location=video.mp4"]


def encode_video(manifest, output, *, gst_launch=None, gst_inspect=None,
                 ffmpeg=None, ffprobe=None, bitrate_kbps=4000, preset="fast"):
    """Encode trusted local GStreamer pipeline, independently verify, atomically publish.

    Staging occurs alongside the destination, so gst-launch only sees constant,
    whitespace-safe relative filenames. Output and receipt are both no-overwrite.
    """
    target = _new_output(output, ".mp4")
    receipt_target = target.with_name(target.name + ".receipt.json")
    if receipt_target.exists() or receipt_target.is_symlink():
        raise ValueError("GStreamer output receipt already exists")
    original, width, height, fps, frames, stride, raw_limit = _strict_local_frames(manifest)
    elements = pipeline(fps, width, height, bitrate_kbps=bitrate_kbps, preset=preset)
    available = capabilities(gst_launch=gst_launch, gst_inspect=gst_inspect)
    if not available["available"]:
        raise ValueError(available["reason"])
    launch = _executable(gst_launch, "gst-launch-1.0")
    ffmpeg, ffprobe = binaries(ffmpeg, ffprobe)
    source_pin = _digest(original)
    with TemporaryDirectory(prefix=".fr-m15-gst-", dir=target.parent) as staged:
        stage = Path(staged)
        source_hash, staged_bytes = _write_raw(frames, width, height, stride, stage / "frames.raw")
        if staged_bytes != raw_limit or (stage / "frames.raw").stat().st_size != raw_limit:
            raise ValueError("GStreamer raw staged payload length differs from plan")
        command = [launch, "-q", "-e", *elements]
        completed = _bounded_command(command, timeout=ENCODE_TIMEOUT_SECONDS, cwd=stage)
        if completed.returncode:
            raise ValueError("GStreamer encode failed: " + completed.stderr.decode("utf-8", "replace")[-900:])
        video = stage / "video.mp4"
        if (not video.is_file() or video.is_symlink() or
                not 0 < video.stat().st_size <= MAX_MEDIA_BYTES):
            raise ValueError("GStreamer output is absent or outside its size budget")
        check = _verify_export(video, width=width, height=height, fps=fps, count=len(frames),
                               ffmpeg=ffmpeg, ffprobe=ffprobe)
        if _digest(original) != source_pin:
            raise ValueError("GStreamer frame manifest changed during encode")
        for path, pin in frames:
            _pinned(path, pin)
        receipt = {"schema": SCHEMA, "source_manifest_sha256": source_pin,
                   "producer": "gst-launch-1.0", "producer_version": available["version"],
                   "elements": available["elements"], "encoding": {"codec": "H.264", "container": "MP4",
                   "fps": f"{fps.numerator}/{fps.denominator}", "bitrate_kbps": bitrate_kbps,
                   "speed_preset": preset, "frames": len(frames), "row_stride_bytes": stride,
                   "raw_sha256": source_hash, "raw_bytes": raw_limit, "audio": "not_included"},
                   "source_frames_sha256": [digest for _, digest in frames],
                   "video_sha256": _digest(video), "independent_ffmpeg_verification": check,
                   "pts_formula": "n * fps.denominator / fps.numerator", "exact_pts_qualified": True,
                   "hardware_gpu_qualified": False}
        evidence = stage / "receipt.json"
        evidence.write_text(json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + "\n",
                            encoding="utf-8")
        if target.exists() or receipt_target.exists() or target.is_symlink() or receipt_target.is_symlink():
            raise ValueError("GStreamer output or receipt appeared during verification")
        published = False
        try:
            os.link(video, target)  # no overwrite; supports NTFS and POSIX local filesystems
            published = True
            os.link(evidence, receipt_target)
        except BaseException:
            if published:
                target.unlink(missing_ok=True)
            raise
        return receipt
