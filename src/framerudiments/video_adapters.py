"""M11.4: opt-in, exact-PTS video import and rational-CFR MP4 export.

Adapters do not alter Project.fps, audio time, the native compositor or its
existing encoder. The source index is taken from *decoded presentation* PTS,
not guessed from avg_frame_rate, with exact Fraction arithmetic throughout.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from PIL import Image

from .encoding import X264_PRESETS, binaries

SCHEMA = "framerudiments.video-adapter.v1"
MANIFEST_SCHEMA = "framerudiments.video-frames.v1"
MAX_INDEXED_FRAMES = 240_000
MAX_PROBE_BYTES = 48 * 1024 * 1024
MAX_RGB_BYTES = 256 * 1024 * 1024
PROBE_TIMEOUT = 180
DECODE_TIMEOUT = 180
ENCODE_TIMEOUT = 180
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _pinned(path, expected_sha256):
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise ValueError("Video/file reference requires a 64-character SHA-256")
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Pinned media must be a regular local file")
    if _digest(path) != expected_sha256.lower():
        raise ValueError(f"Pinned media SHA-256 differs: {path}")
    return path


def _fraction(value, label, *, positive=False):
    # Float literals are not stable timebase specifications. Require a decimal
    # string, [numerator, denominator], integer or Fraction instead.
    if type(value) is int or isinstance(value, (str, Fraction)):
        try:
            result = Fraction(value)
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            raise ValueError(f"{label} must be an exact rational") from exc
    elif (isinstance(value, (list, tuple)) and len(value) == 2
          and all(type(x) is int for x in value) and value[1]):
        result = Fraction(*value)
    else:
        raise ValueError(f"{label} must be an exact rational, not a float")
    if positive and result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def _rate(value):
    result = _fraction(value, "Video frame rate", positive=True)
    if not 1 <= result <= 120 or result.numerator > 1_000_000 or result.denominator > 1_000_000:
        raise ValueError("Video frame rate must be 1..120 with a bounded rational denominator")
    return result


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON field: {key}")
        value[key] = item
    return value


def _json_file(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_PROBE_BYTES:
        raise ValueError("JSON manifest is not a bounded local file")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)


def _probe(path, ffprobe, *, stream=0):
    if type(stream) is not int or not 0 <= stream < 16:
        raise ValueError("Video stream ordinal must be 0..15")
    cmd = [ffprobe, "-v", "error", "-select_streams", f"v:{stream}",
           "-show_streams", "-show_frames",
           "-show_entries", "stream=index,width,height,time_base,avg_frame_rate:"
                            "frame=best_effort_timestamp",
           "-of", "json", str(path)]
    try:
        completed = subprocess.run(cmd, capture_output=True, timeout=PROBE_TIMEOUT, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("ffprobe could not index the requested video") from exc
    if len(completed.stdout) > MAX_PROBE_BYTES:
        raise ValueError("Video presentation index exceeds the 48 MiB probe limit")
    try:
        data = json.loads(completed.stdout)
        streams = data["streams"]
        frames = data["frames"]
        if len(streams) != 1 or not isinstance(frames, list) or not frames:
            raise ValueError("Expected one selected video stream with decoded frames")
        info = streams[0]
        width, height = int(info["width"]), int(info["height"])
        base = _fraction(info["time_base"], "Stream time base", positive=True)
        pts = tuple(int(frame["best_effort_timestamp"]) for frame in frames)
        if len(pts) > MAX_INDEXED_FRAMES:
            raise ValueError("Video exceeds the bounded presentation index")
        if any(later <= earlier for earlier, later in zip(pts, pts[1:])):
            raise ValueError("Decoded presentation timestamps must increase strictly")
        if width < 1 or height < 1 or width * height * 3 > MAX_RGB_BYTES:
            raise ValueError("Video dimensions exceed the native RGB frame budget")
        nominal = info.get("avg_frame_rate", "0/1")
        nominal_rate = _fraction(nominal, "Nominal frame rate")
    except (KeyError, TypeError, OverflowError, ValueError) as exc:
        raise ValueError(f"Unsupported video presentation index: {exc}") from exc
    return int(info["index"]), width, height, base, pts, nominal_rate


@dataclass(frozen=True)
class VideoInput:
    """SHA-pinned exact decoded-presentation index; random access is stateless.

    Decode by ordinal, not a fast inaccurate container seek. Long GOPs can be
    expensive; ffmpeg is a separate, explicitly bounded local process per read.
    """

    path: Path
    sha256: str
    stream: int
    stream_index: int
    width: int
    height: int
    time_base: Fraction
    pts: tuple[int, ...]
    nominal_rate: Fraction

    @classmethod
    def open(cls, path, *, sha256, ffprobe=None, stream=0):
        source = _pinned(path, sha256)
        _ffmpeg, ffprobe = binaries(ffprobe=ffprobe)
        index, width, height, base, pts, nominal = _probe(source, ffprobe, stream=stream)
        _pinned(source, sha256)  # reject mutation during ffprobe
        return cls(source, sha256.lower(), stream, index, width, height, base, pts, nominal)

    @property
    def frame_count(self):
        return len(self.pts)

    @property
    def timestamps(self):
        origin = self.pts[0]
        return tuple((p - origin) * self.time_base for p in self.pts)

    @property
    def is_vfr(self):
        return len(self.pts) > 2 and len(set(b - a for a, b in zip(self.pts, self.pts[1:]))) > 1

    def frame_at(self, time, *, policy="floor", boundary="clamp"):
        """Select the frame at an exact time since the first decoded video PTS."""
        tick = _fraction(time, "Requested source time")
        if policy not in ("floor", "ceil", "nearest"):
            raise ValueError("Frame selection policy must be floor, ceil or nearest")
        if boundary not in ("clamp", "error"):
            raise ValueError("Video boundary policy must be clamp or error")
        times = self.timestamps
        if boundary == "error" and (tick < 0 or tick > times[-1]):
            raise ValueError("Requested time is outside decoded video presentation frames")
        if policy == "floor":
            index = bisect_right(times, tick) - 1
        elif policy == "ceil":
            index = bisect_left(times, tick)
        else:
            right = bisect_left(times, tick)
            if right <= 0:
                index = 0
            elif right >= self.frame_count:
                index = self.frame_count - 1
            else:
                # Exact ties choose later frame (same convention as the native clock).
                index = right if tick - times[right - 1] >= times[right] - tick else right - 1
        return min(max(index, 0), self.frame_count - 1)

    def frame_for_output(self, number, fps, *, policy="floor", boundary="clamp"):
        if type(number) is not int or number < 0:
            raise ValueError("Output frame index must be a nonnegative integer")
        return self.frame_at(Fraction(number, 1) / _rate(fps), policy=policy, boundary=boundary)

    def read(self, index, *, ffmpeg=None):
        """Read one RGB8 image in presentation order without modifying the source."""
        if type(index) is not int or not 0 <= index < self.frame_count:
            raise ValueError("Video frame index outside decoded source")
        _pinned(self.path, self.sha256)
        ffmpeg, _ffprobe = binaries(ffmpeg=ffmpeg)
        # -noautorotate fixes the decoded dimensions to the validated coded
        # width/height. Generated ordinal is never user-controlled filter text.
        command = [ffmpeg, "-v", "error", "-nostdin", "-noautorotate", "-i", str(self.path),
                   "-map", f"0:{self.stream_index}", "-an", "-sn", "-dn",
                   "-vf", f"select=eq(n\\,{index})", "-fps_mode", "passthrough",
                   "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
        try:
            result = subprocess.run(command, check=True, capture_output=True, timeout=DECODE_TIMEOUT)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError("FFmpeg failed to decode the exact requested video frame") from exc
        if len(result.stdout) != self.width * self.height * 3:
            raise ValueError("Video decoder yielded an incomplete or unexpected RGB frame")
        _pinned(self.path, self.sha256)
        return Image.frombytes("RGB", (self.width, self.height), result.stdout)

    def document(self, *, include_frames=False):
        steps = [b - a for a, b in zip(self.pts, self.pts[1:])]
        result = {"schema": SCHEMA, "source": {"path": str(self.path), "sha256": self.sha256},
                  "stream_index": self.stream_index, "size": [self.width, self.height],
                  "time_base": str(self.time_base), "first_pts": self.pts[0],
                  "last_pts": self.pts[-1], "frame_count": self.frame_count,
                  "duration_to_last_presentation": str(self.timestamps[-1]),
                  "nominal_rate": str(self.nominal_rate), "vfr": self.is_vfr,
                  "constant_step": str(steps[0] * self.time_base) if steps and not self.is_vfr else None,
                  "pts_sha256": hashlib.sha256(
                      ",".join(str(p) for p in self.pts).encode("ascii")).hexdigest()}
        if include_frames:
            result["frames"] = [{"index": i, "pts": pt, "seconds": str(sec)}
                                for i, (pt, sec) in enumerate(zip(self.pts, self.timestamps))]
        return result


def _new_output(path, suffix):
    target = Path(path).absolute()
    if target.suffix.lower() != suffix or not target.parent.is_dir():
        raise ValueError(f"Output needs a directory and {suffix} extension")
    if target.exists() or target.is_symlink():
        raise ValueError("Output already exists; refusing to overwrite")
    return target


def _temporary_near(target, suffix):
    descriptor, path = tempfile.mkstemp(prefix=".fr-video-", suffix=suffix, dir=target.parent)
    os.close(descriptor)
    Path(path).unlink()
    return Path(path)


def extract_png(source, output, *, sha256, index=None, time=None, policy="floor",
                ffmpeg=None, ffprobe=None):
    if (index is None) == (time is None):
        raise ValueError("Specify exactly one of index or exact source time")
    target = _new_output(output, ".png")
    video = VideoInput.open(source, sha256=sha256, ffprobe=ffprobe)
    ordinal = index if index is not None else video.frame_at(time, policy=policy, boundary="error")
    pixels = video.read(ordinal, ffmpeg=ffmpeg)
    temp = _temporary_near(target, ".png")
    try:
        pixels.save(temp, format="PNG")
        os.link(temp, target)  # atomic no-replace, including Windows NTFS
        return {"schema": SCHEMA, "frame": ordinal, "pts": video.pts[ordinal],
                "seconds": str(video.timestamps[ordinal]), "output": str(target),
                "sha256": _digest(target), "source_sha256": video.sha256}
    finally:
        temp.unlink(missing_ok=True)


def _manifest(path):
    manifest_path = Path(path).resolve(strict=True)
    document = _json_file(manifest_path)
    if not isinstance(document, dict) or set(document) != {"schema", "width", "height", "fps", "frames"}:
        raise ValueError("Video frames manifest must declare schema, width, height, fps, frames")
    if document["schema"] != MANIFEST_SCHEMA:
        raise ValueError(f"Expected {MANIFEST_SCHEMA}")
    width, height = document["width"], document["height"]
    if (type(width) is not int or type(height) is not int or width < 16 or height < 16
            or width % 2 or height % 2 or width * height * 3 > MAX_RGB_BYTES):
        raise ValueError("Video export requires even dimensions within the RGB budget")
    fps = _rate(document["fps"])
    frames = document["frames"]
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_INDEXED_FRAMES:
        raise ValueError("Video export needs 1..240000 ordered pinned frames")
    result = []
    for row in frames:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"} or not isinstance(row["path"], str):
            raise ValueError("Every frame requires exactly path and sha256")
        result.append((_pinned(manifest_path.parent / row["path"], row["sha256"]), row["sha256"].lower()))
    return manifest_path, width, height, fps, result


def _verify_export(path, *, width, height, fps, count, ffmpeg, ffprobe):
    info = _probe(path, ffprobe)
    stream_index, w, h, base, pts, _rate_hint = info
    if stream_index < 0 or (w, h) != (width, height) or len(pts) != count:
        raise ValueError("Encoded video dimensions or decoded frame count differ")
    if any(Fraction(pt - pts[0]) * base != Fraction(i, 1) / fps
           for i, pt in enumerate(pts)) or pts[0] != 0:
        raise ValueError("Encoded video PTS is not exact rational constant frame rate from zero")
    try:
        subprocess.run([ffmpeg, "-v", "error", "-xerror", "-i", str(path),
                        "-map", "0:v:0", "-an", "-f", "null", "-"],
                       capture_output=True, check=True, timeout=ENCODE_TIMEOUT)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Encoded video did not pass a full decoded-frame check") from exc
    return {"stream_index": stream_index, "time_base": str(base), "frames": len(pts),
            "fps": str(fps), "first_pts": pts[0], "last_pts": pts[-1],
            "full_decode": "passed", "timestamps": "exact rational CFR from zero"}


def encode_manifest(manifest, output, *, ffmpeg=None, ffprobe=None, preset="fast", crf=20):
    """Encode SHA-pinned RGB PNG frames to an exact-rational video-only MP4.

    Output and adjacent JSON receipt are new files, published only after ffprobe
    PTS validation and a complete independent FFmpeg decode. No audio is added;
    the normal FrameRudiments render pipeline remains authoritative for music.
    """
    target = _new_output(output, ".mp4")
    receipt_path = target.with_name(target.name + ".receipt.json")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ValueError("Video receipt already exists; refusing to overwrite")
    manifest_path, width, height, fps, frames = _manifest(manifest)
    if preset not in X264_PRESETS or type(crf) is not int or not 0 <= crf <= 51:
        raise ValueError("Unsupported H.264 preset or CRF")
    ffmpeg, ffprobe = binaries(ffmpeg, ffprobe)
    source_hash = _digest(manifest_path)
    temp = _temporary_near(target, ".mp4")
    temp_receipt = _temporary_near(target, ".json")
    command = [ffmpeg, "-n", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{width}x{height}",
               "-framerate", str(fps.numerator) + "/" + str(fps.denominator),
               "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", preset,
               "-crf", str(crf), "-threads", "4", "-pix_fmt", "yuv420p",
               "-fps_mode", "cfr", "-video_track_timescale", str(fps.numerator),
               "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
               "-movflags", "+faststart", str(temp)]
    process = None
    published = False
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE)
        assert process.stdin is not None
        try:
            for path, digest in frames:
                _pinned(path, digest)
                with Image.open(path) as image:
                    image.load()
                    if image.mode != "RGB" or image.size != (width, height):
                        raise ValueError("Video export requires identical RGB8 PNG frames")
                    if image.format != "PNG":
                        raise ValueError("Video frame reference must be PNG")
                    process.stdin.write(image.tobytes())
                _pinned(path, digest)  # reject image edits during decode/encoding
            process.stdin.close()
            process.stdin = None
            _stdout, stderr = process.communicate(timeout=ENCODE_TIMEOUT)
            if process.returncode:
                raise ValueError("FFmpeg encoding failed: " + stderr.decode("utf-8", "replace")[-1000:])
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.communicate()
            raise
        checked = _verify_export(temp, width=width, height=height, fps=fps,
                                 count=len(frames), ffmpeg=ffmpeg, ffprobe=ffprobe)
        if _digest(manifest_path) != source_hash:
            raise ValueError("Video frame manifest changed during encoding")
        output_sha = _digest(temp)
        receipt = {"schema": SCHEMA, "output_sha256": output_sha,
                   "manifest_sha256": source_hash, "output": str(target), "verified": checked,
                   "frames": [{"sha256": digest} for _path, digest in frames],
                   "audio": "not_included", "source_pts": "n * fps.denominator / fps.numerator"}
        temp_receipt.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if target.exists() or receipt_path.exists() or target.is_symlink() or receipt_path.is_symlink():
            raise ValueError("Output or receipt appeared during encoding")
        os.link(temp, target)
        published = True
        os.link(temp_receipt, receipt_path)
        return receipt
    except BaseException:
        if published:
            target.unlink(missing_ok=True)
        raise
    finally:
        temp.unlink(missing_ok=True)
        temp_receipt.unlink(missing_ok=True)
