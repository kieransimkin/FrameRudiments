"""One FFmpeg boundary for exact audio slices, repeats, H.264/AAC and decoded QA."""

from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess


X264_PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
    "placebo",
)


def binaries(ffmpeg=None, ffprobe=None):
    ffmpeg = ffmpeg or os.environ.get("FRAMERUDIMENTS_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("Set FRAMERUDIMENTS_FFMPEG or pass --ffmpeg")
    ffmpeg = str(Path(ffmpeg).resolve())
    ffprobe = ffprobe or os.environ.get("FRAMERUDIMENTS_FFPROBE")
    if not ffprobe:
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        ffprobe = str(sibling) if sibling.is_file() else shutil.which("ffprobe")
    if not ffprobe or not Path(ffprobe).is_file() or not Path(ffmpeg).is_file():
        raise ValueError("Both FFmpeg and ffprobe must be available")
    return ffmpeg, str(Path(ffprobe).resolve())


def probe(path, ffprobe, *, count=False):
    args = [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json"]
    if count:
        args += ["-count_frames"]
    return json.loads(subprocess.check_output([*args, str(path)], encoding="utf-8"))


def validate_audio(project, ffprobe):
    info = probe(project.audio_ref["path"], ffprobe)
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    if int(audio["sample_rate"]) != project.sample_rate:
        raise ValueError("Configured sample rate differs from source audio")
    duration = Fraction(str(audio.get("duration", info["format"]["duration"])))
    if "duration_ts" in audio:
        duration = int(audio["duration_ts"]) * Fraction(audio["time_base"])
    if project.end_sample > duration * project.sample_rate:
        raise ValueError("Excerpt exceeds source audio extent")
    return info


def audio_filters(project):
    p = project
    filters = [f"atrim=start_sample={p.start_sample}:end_sample={p.end_sample}", "asetpts=PTS-STARTPTS"]
    if p.repeats > 1:
        filters += [f"aloop=loop={p.repeats - 1}:size={p.end_sample - p.start_sample}"]
    if p.fade:
        filters += [
            f"afade=t=in:st=0:d={p.fade:.12f}:curve=qsin",
            f"afade=t=out:st={float(p.duration) - p.fade:.12f}:d={p.fade:.12f}:curve=qsin",
        ]
    return ",".join(filters)


def command(project, output, ffmpeg, *, preset="fast", crf=20):
    if preset not in X264_PRESETS:
        raise ValueError("Unsupported encoder preset")
    if not isinstance(crf, int) or not 0 <= crf <= 51:
        raise ValueError("CRF must be an integer in [0,51]")
    p = project
    return [
        ffmpeg,
        "-n",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{p.width}x{p.height}",
        "-framerate",
        str(p.fps),
        "-i",
        "pipe:0",
        "-i",
        p.audio_ref["path"],
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-af",
        audio_filters(p),
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-threads",
        "4",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "cfr",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        "-ar",
        str(p.sample_rate),
        "-movflags",
        "+faststart",
        str(output),
    ]


def video_chunk_command(project, output, ffmpeg, *, preset="fast", crf=20):
    """Encode one finalized video-only chunk from consecutive raw RGB frames."""
    if preset not in X264_PRESETS:
        raise ValueError("Unsupported encoder preset")
    if not isinstance(crf, int) or not 0 <= crf <= 51:
        raise ValueError("CRF must be an integer in [0,51]")
    p = project
    keyint = max(1, p.fps * 2)
    return [
        ffmpeg,
        "-n",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{p.width}x{p.height}",
        "-framerate",
        str(p.fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-threads",
        "4",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "cfr",
        "-g",
        str(keyint),
        "-keyint_min",
        str(keyint),
        "-sc_threshold",
        "0",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-movflags",
        "+faststart",
        str(output),
    ]


def mux_chunks_command(project, concat_file, output, ffmpeg):
    """Stream-copy finalized video chunks and mux the canonical audio once."""
    p = project
    return [
        ffmpeg,
        "-n",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-i",
        p.audio_ref["path"],
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-af",
        audio_filters(p),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "256k",
        "-ar",
        str(p.sample_rate),
        "-movflags",
        "+faststart",
        str(output),
    ]


def verify_video(path, project, expected_frames, ffmpeg, ffprobe):
    """Fully decode a video-only chunk and verify its zero-based CFR ledger."""
    info = probe(path, ffprobe, count=True)
    streams = [stream for stream in info["streams"] if stream["codec_type"] == "video"]
    if len(streams) != 1 or any(stream["codec_type"] == "audio" for stream in info["streams"]):
        raise ValueError("Chunk must contain exactly one video stream and no audio")
    video = streams[0]
    if (
        int(video["nb_read_frames"]) != expected_frames
        or Fraction(video["avg_frame_rate"]) != project.fps
        or Fraction(video["r_frame_rate"]) != project.fps
        or video["codec_name"] != "h264"
        or video["pix_fmt"] != "yuv420p"
        or [video["width"], video["height"]] != [project.width, project.height]
    ):
        raise ValueError("Encoded chunk does not match its frame contract")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    timestamps = [
        float(line.split(",")[0])
        for line in completed.stdout.splitlines()
        if line.split(",")[0].strip()
    ]
    if len(timestamps) != expected_frames or any(
        abs(timestamp - index / project.fps) > 0.000002
        for index, timestamp in enumerate(timestamps)
    ):
        raise ValueError("Chunk timestamps are not constant-rate from zero")
    subprocess.run(
        [ffmpeg, "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
        check=True,
        capture_output=True,
    )
    return dict(
        frames=expected_frames,
        full_decode="passed",
        decoded_timestamps="CFR from zero; all chunk frames checked",
        probe=info,
    )


def verify(path, project, ffmpeg, ffprobe):
    info = probe(path, ffprobe, count=True)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    if (
        int(video["nb_read_frames"]) != project.frame_count
        or Fraction(video["avg_frame_rate"]) != project.fps
        or Fraction(video["r_frame_rate"]) != project.fps
        or video["codec_name"] != "h264"
        or video["pix_fmt"] != "yuv420p"
        or [video["width"], video["height"]] != [project.width, project.height]
    ):
        raise ValueError("Encoded video does not match the prepared frame contract")
    if audio["codec_name"] != "aac" or int(audio["sample_rate"]) != project.sample_rate:
        raise ValueError("Encoded audio contract differs")
    duration_error = float(audio["duration"]) - float(project.duration)
    if abs(duration_error) > max(2 / project.sample_rate, 0.002):
        raise ValueError(f"Encoded audio programme length differs by {duration_error:.6f}s")
    # Inspect decoded timestamps, not only nominal container rate labels.
    timestamp_command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "frame=best_effort_timestamp_time",
        "-of",
        "csv=p=0",
        str(path),
    ]
    completed = subprocess.run(timestamp_command, capture_output=True, text=True, check=True)
    timestamps = [
        float(line.split(",")[0]) for line in completed.stdout.splitlines() if line.split(",")[0].strip()
    ]
    if len(timestamps) != project.frame_count or any(
        abs(t - i / project.fps) > 0.000002 for i, t in enumerate(timestamps)
    ):
        raise ValueError("Decoded frame timestamps are not constant-rate from zero")
    subprocess.run(
        [ffmpeg, "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
        check=True,
        capture_output=True,
    )
    # Verify actual MP4 atom order for fast start.
    atoms = []
    with Path(path).open("rb") as handle:
        while header := handle.read(8):
            if len(header) != 8:
                raise ValueError("Truncated MP4 atom")
            size, kind = int.from_bytes(header[:4], "big"), header[4:].decode("ascii")
            consumed = 8
            if size == 1:
                size = int.from_bytes(handle.read(8), "big")
                consumed = 16
            atoms.append(kind)
            if size == 0:
                break
            if size < consumed:
                raise ValueError("Invalid MP4 atom size")
            handle.seek(size - consumed, 1)
    if "moov" not in atoms or "mdat" not in atoms or atoms.index("moov") > atoms.index("mdat"):
        raise ValueError("MP4 is missing fast-start atom order")
    return dict(
        probe=info,
        full_decode="passed",
        decoded_timestamps="CFR from zero; all frames checked",
        audio_duration_error_seconds=duration_error,
        mp4_atoms=atoms,
        listening_approved=False,
        platform_safe_zone_verified=False,
    )
