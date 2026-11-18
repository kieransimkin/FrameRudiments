"""M15.2: explicitly admitted GStreamer video ingest and bounded live capture.

GStreamer owns decode/capture. The independent M11.4 ffprobe frame ledger is
compared against multifilesink's per-buffer PTS, *never* inferred from FPS.
There is no implicit network activity, device discovery, service or clock reset.
"""
from __future__ import annotations

from fractions import Fraction
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from PIL import Image

from .encoding import binaries
from .gstreamer_m15 import _executable, _bounded_command
from .video_adapters import VideoInput, _digest, _pinned, _rate

SCHEMA = "framerudiments.gstreamer-ingest.v1"
LIVE_SCHEMA = "framerudiments.gstreamer-live-capture.v1"
MAX_FRAMES = 256
MAX_RAW_BYTES = 512 * 1024 * 1024
MAX_INPUT_BYTES = 1024 * 1024 * 1024
MAX_LOG_BYTES = 512 * 1024
MAX_RETRIES = 2
REQUIRED_INGEST_ELEMENTS = ("filesrc", "decodebin", "videoconvert", "pngenc",
                            "multifilesink", "videotestsrc", "identity", "queue")
TIMEOUT_SECONDS = 120
_MESSAGE = re.compile(r"GstMultiFileSink,\s*filename=\(string\)([^,;]+),\s*"
                      r"index=\(int\)(\d+),\s*timestamp=\(guint64\)(\d+)")
_TIMESTAMP_MISSING = (1 << 64) - 1


def ingest_capabilities(*, gst_launch=None, gst_inspect=None):
    """Read-only required-element discovery; never enumerate devices or URLs."""
    launch = _executable(gst_launch, "gst-launch-1.0")
    inspect = _executable(gst_inspect, "gst-inspect-1.0")
    status = {"schema": SCHEMA, "available": False, "required_elements": {},
              "live_synthetic": False, "device_opened": False, "network_opened": False}
    if not launch or not inspect:
        status["reason"] = "gst-launch-1.0 and gst-inspect-1.0 are required"
        return status
    version = _bounded_command([launch, "--version"])
    if version.returncode or b"gst-launch-1.0 version" not in version.stdout:
        status["reason"] = "Invalid GStreamer launcher/version"
        return status
    for name in REQUIRED_INGEST_ELEMENTS:
        result = _bounded_command([inspect, name])
        status["required_elements"][name] = bool(
            result.returncode == 0 and b"Factory Details" in result.stdout)
    status["available"] = all(status["required_elements"].values())
    status["live_synthetic"] = status["available"]
    status["reason"] = None if status["available"] else "M15.2 decode/capture plugins missing"
    return status


def _positive_int(value, label, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be a bounded positive integer")
    return value


def _run(command, *, cwd, timeout=TIMEOUT_SECONDS):
    """Supervise a single in-process GStreamer launcher; bounded runtime/diagnostics.

    Do not start a shell. Native GStreamer plugins run in the child process.
    A timed-out run kills/reaps it and leaves no published output.
    """
    if not 1 <= timeout <= TIMEOUT_SECONDS:
        raise ValueError("GStreamer timeout is outside 1..120 seconds")
    startup = {"start_new_session": True} if os.name == "posix" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    try:
        child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, **startup)
    except OSError as exc:
        raise RuntimeError("Cannot start trusted local GStreamer launcher") from exc
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Never leave an active live decoder after a cancelled/failed capture.
        try:
            if os.name == "posix":
                import signal
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
        except ProcessLookupError:
            pass
        child.communicate()
        raise TimeoutError("GStreamer capture deadline exceeded") from exc
    if len(stdout) > MAX_LOG_BYTES or len(stderr) > MAX_LOG_BYTES:
        raise ValueError("GStreamer capture diagnostic output exceeds its budget")
    if child.returncode:
        raise RuntimeError("GStreamer decode/capture failed: " +
                           stderr.decode("utf-8", "replace")[-600:])
    return stdout.decode("utf-8", "replace") + "\n" + stderr.decode("utf-8", "replace")


def _messages(output):
    """Require native multifilesink PTS/index messages in capture order."""
    rows = []
    for line in output.splitlines():
        if "GstMultiFileSink," not in line:
            continue
        match = _MESSAGE.search(line)
        if match is None:
            raise ValueError("Malformed native GStreamer frame timestamp message")
        name, ordinal, stamp = match.groups()
        index, nanoseconds = int(ordinal), int(stamp)
        if (index != len(rows) or index >= MAX_FRAMES or
                nanoseconds == _TIMESTAMP_MISSING or nanoseconds >= (1 << 63) or
                (rows and nanoseconds <= rows[-1][1]) or
                Path(name).name != f"frame-{index:06d}.png"):
            raise ValueError("Nonmonotonic, unknown or missing GStreamer frame timestamp")
        rows.append((index, nanoseconds))
    if not rows:
        raise ValueError("GStreamer emitted no independently timestamped frames")
    return rows


def _frame_evidence(stage, rows, *, width, height):
    """Read every actual native PNG, reject unexpected files, pin stored bytes."""
    frames = []
    total_bytes = 0
    expected = {f"frame-{n:06d}.png" for n in range(len(rows))}
    present = {p.name for p in stage.iterdir() if p.suffix.lower() == ".png"}
    if expected != present:
        raise ValueError("GStreamer emitted missing or unexpected frame files")
    for n, stamp in rows:
        path = stage / f"frame-{n:06d}.png"
        if path.is_symlink() or not path.is_file():
            raise ValueError("GStreamer frame is absent or symlinked")
        total_bytes += path.stat().st_size
        if total_bytes > MAX_RAW_BYTES:
            raise ValueError("Decoded PNG budget exceeded")
        with Image.open(path) as image:
            if image.format != "PNG" or image.mode != "RGB" or image.size != (width, height):
                raise ValueError("GStreamer decoded frame must be an RGB8 PNG at source resolution")
            image.verify()
        frames.append({"index": n, "path": path.name, "sha256": _digest(path),
                       "gst_pts_ns": stamp})
    return frames


def _bounded_dimensions(width, height, count):
    _positive_int(count, "decoded frames", MAX_FRAMES)
    if (type(width) is not int or type(height) is not int or
            not 16 <= width <= 3840 or not 16 <= height <= 2160 or
            width * height * 3 * count > MAX_RAW_BYTES):
        raise ValueError("Decoded video exceeds 512 MiB RGB budget")


def _publish_directory(target, stage):
    if target.exists() or target.is_symlink():
        raise ValueError("Output directory appeared during capture")
    # Local atomic rename publishes a complete directory; never merge with an
    # existing output. On unusual cross-filesystem moves fail closed.
    stage.rename(target)


def _target_directory(output):
    target = Path(output).absolute()
    if not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise ValueError("Output must be a new directory beneath an existing parent")
    return target


def _launcher(gst_launch):
    launch = _executable(gst_launch, "gst-launch-1.0")
    if launch is None:
        raise ValueError("gst-launch-1.0 is required for M15.2; no decoder fallback")
    return launch


def _video_pipeline(*, live=False, limit=None):
    items = ["!", "videoconvert", "!", "video/x-raw,format=RGB", "!", "pngenc"]
    if live:
        _positive_int(limit, "live frame limit", MAX_FRAMES)
        # Downstream buffer leaky policy is explicitly recorded as possible
        # dropped frames; it does not manufacture missing PTS.
        # identity raises EOS *before* passing its trigger buffer: N+1
        # yields exactly N published frames, even after leaky queue drops.
        items += ["!", "identity", f"eos-after={limit + 1}"]
    return items + ["!", "multifilesink", "location=frame-%06d.png", "post-messages=true"]


def _verify_pts(rows, video):
    if len(rows) != video.frame_count:
        raise ValueError("GStreamer frame count differs from independently probed video")
    for (_, nanoseconds), instant in zip(rows, video.timestamps):
        # GStreamer GstClockTime is an integer nanosecond clock. Compare
        # deltas to exact ffprobe PTS, allowing only one clock quantisation ns.
        delta = Fraction(nanoseconds - rows[0][1], 1_000_000_000)
        if abs(delta - instant) > Fraction(1, 1_000_000_000):
            raise ValueError("GStreamer decoded PTS differs from FFprobe presentation ledger")


def reconcile_audio_clock(video, *, sample_rate, audio_origin="0", max_error_samples="1/2"):
    """Report exact video-presentation -> audio-sample positions, no resampling.

    `audio_origin` is measured on the SAME source PTS axis as the independent
    video timestamps; do not subtract first video PTS without accounting for it.
    """
    _positive_int(sample_rate, "audio sample rate", 384000)
    try:
        origin = Fraction(audio_origin)
        tolerance = Fraction(max_error_samples)
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise ValueError("Audio clock requires exact rational strings") from exc
    if not 0 <= tolerance <= 1 or abs(origin) > 24 * 3600:
        raise ValueError("Audio origin or alignment tolerance is not bounded")
    if not video or len(video) > MAX_FRAMES:
        raise ValueError("Audio alignment needs a bounded nonempty video presentation ledger")
    rows = []
    for value in video:
        if not isinstance(value, str):
            raise ValueError("Video clock timestamps must be exact rational strings")
        stamp = Fraction(value)
        position = (stamp - origin) * sample_rate
        sample = (position.numerator * 2 + position.denominator) // (2 * position.denominator)
        error = abs(Fraction(sample) - position)
        if error > tolerance:
            raise ValueError("Video frame exceeds permitted sample-clock quantisation tolerance")
        rows.append({"video_seconds": str(stamp), "nearest_audio_sample": sample,
                     "quantisation_error_samples": str(error)})
    if any(a["nearest_audio_sample"] > b["nearest_audio_sample"]
           for a, b in zip(rows, rows[1:])):
        raise ValueError("Audio sample positions run backward")
    return {"sample_rate": sample_rate, "audio_origin_seconds": str(origin),
            "max_error_samples": str(tolerance), "samples": rows,
            "audio_resampled": False, "clock_alignment_only": True}


def _pinned_audio_clock(source, pin, *, ffprobe):
    """Use a separately pinned audio stream's exact sample clock; do not decode it."""
    path = _pinned(source, pin)
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Audio reference is above the 1 GiB bound")
    command = [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_streams",
               "-show_entries", "stream=index,sample_rate,time_base,start_pts,start_time",
               "-of", "json", str(path)]
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Pinned audio reference cannot be probed") from exc
    if len(result.stdout) > 256 * 1024:
        raise ValueError("Audio stream probe exceeds the expected size")
    data = json.loads(result.stdout)
    if len(data.get("streams", [])) != 1:
        raise ValueError("Pinned audio reference must contain a selected audio stream")
    audio = data["streams"][0]
    rate = int(audio["sample_rate"])
    _positive_int(rate, "audio sample rate", 384000)
    if audio.get("start_pts") is not None:
        origin = Fraction(int(audio["start_pts"])) * Fraction(audio["time_base"])
        source_clock = "probed_audio_pts"
    elif audio.get("start_time") is not None:
        origin = Fraction(audio["start_time"])
        source_clock = "probed_audio_start_time"
    else:
        # WAV and some raw PCM containers have no explicit start PTS. Zero is
        # a declared fallback, not evidence of measured external sync.
        origin = Fraction(0)
        source_clock = "no_start_pts_assumed_zero"
    _pinned(path, pin)
    return rate, origin, {"source_sha256": pin.lower(),
                          "stream_index": audio["index"], "source_clock": source_clock}


def ingest_video(source, output, *, sha256, gst_launch=None, ffmpeg=None, ffprobe=None,
                 audio_source=None, audio_sha256=None, audio_rate=None, audio_origin=None,
                 timeout=TIMEOUT_SECONDS):
    """Decode pinned local video with GStreamer; publish verified RGB PNG + VFR ledger."""
    target = _target_directory(output)
    pinned = _pinned(source, sha256)
    if pinned.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Input media exceeds 1 GiB budget")
    if (audio_source is None) != (audio_sha256 is None):
        raise ValueError("Pinned audio requires both source and SHA-256")
    _ffmpeg, probe = binaries(ffmpeg, ffprobe)
    video = VideoInput.open(pinned, sha256=sha256, ffprobe=probe)
    _bounded_dimensions(video.width, video.height, video.frame_count)
    if audio_source is not None:
        if audio_rate is not None or audio_origin is not None:
            raise ValueError("Use a pinned audio reference OR explicit audio clock, not both")
        audio_rate, audio_origin, audio_evidence = _pinned_audio_clock(
            audio_source, audio_sha256, ffprobe=probe)
    elif audio_rate is not None:
        if audio_origin is None:
            raise ValueError("Explicit sample rate also requires a rational audio origin")
        audio_evidence = {"source_clock": "caller_declared_unverified"}
    elif audio_origin is not None:
        raise ValueError("Audio origin cannot be specified without a sample rate")
    else:
        audio_evidence = None
    launcher = _launcher(gst_launch)
    with TemporaryDirectory(prefix=".fr-m15-ingest-", dir=target.parent) as tmp:
        staging = Path(tmp)
        media = staging / "source.media"
        shutil.copyfile(pinned, media)
        if _digest(media) != sha256.lower():
            raise ValueError("Staged GStreamer video changed during copy")
        result = _run([launcher, "-m", "-e", "filesrc", "location=source.media", "!", "decodebin",
                       *_video_pipeline()], cwd=staging, timeout=timeout)
        rows = _messages(result)
        _verify_pts(rows, video)
        frames = _frame_evidence(staging, rows, width=video.width, height=video.height)
        for i, frame in enumerate(frames):
            frame["source_pts"] = video.pts[i]
            frame["time_seconds"] = str(video.timestamps[i])
        ledger = {"schema": SCHEMA, "source_sha256": sha256.lower(),
                  "input_video_stream": video.stream_index,
                  "source_time_base": str(video.time_base), "source_first_pts": video.pts[0],
                  "video_dimensions": [video.width, video.height], "variable_frame_rate": video.is_vfr,
                  "frames": frames, "comparison": "decoded GStreamer ns versus exact FFprobe PTS",
                  "presentation_quantisation_ns": 1,
                  "audio_clock": ({**reconcile_audio_clock(
                      [str(p * video.time_base) for p in video.pts],
                      sample_rate=audio_rate, audio_origin=str(audio_origin)),
                      "evidence": audio_evidence} if audio_rate else None),
                  "audio_muxed": False, "model_estimated": False}
        (staging / "index.json").write_text(json.dumps(ledger, sort_keys=True, indent=2) + "\n",
                                              encoding="utf-8")
        # Recheck the source before making any generated assets visible.
        _pinned(pinned, sha256)
        if audio_source is not None:
            _pinned(audio_source, audio_sha256)
        media.unlink()
        _publish_directory(target, staging)
    return ledger


def _admit_live_source(kind, *, rtsp_url=None, device=None, allow_network=False, limit=30,
                       fps="30", width=128, height=72):
    _positive_int(limit, "live frame limit", MAX_FRAMES)
    fps = _rate(fps)
    if not 1 <= fps <= 60 or type(width) is not int or type(height) is not int or (
            not 16 <= width <= 1920 or not 16 <= height <= 1080 or
            width * height * limit * 3 > MAX_RAW_BYTES):
        raise ValueError("Live capture dimensions, rate or raw budget unsupported")
    if kind == "test":
        if rtsp_url is not None or device is not None or allow_network:
            raise ValueError("Synthetic live input accepts no external device or network")
        source = ["videotestsrc", "is-live=true", "pattern=ball", "!",
                  f"video/x-raw,width={width},height={height},framerate={fps.numerator}/{fps.denominator}"]
    elif kind == "v4l2":
        if device is None or rtsp_url is not None or allow_network:
            raise ValueError("V4L2 requires only an explicit device path")
        actual = Path(device)
        if (os.name != "posix" or not re.fullmatch(r"/dev/video\d{1,3}", str(actual)) or
                not actual.exists() or actual.is_symlink()):
            raise ValueError("V4L2 capture requires a permitted /dev/videoN device")
        source = ["v4l2src", "device=" + str(actual), "!",
                  f"video/x-raw,width={width},height={height},framerate={fps.numerator}/{fps.denominator}"]
    elif kind == "rtsp":
        if device is not None or not allow_network or not isinstance(rtsp_url, str):
            raise ValueError("RTSP requires an explicit URL and --allow-network")
        url = urlsplit(rtsp_url)
        try:
            loopback = ipaddress.ip_address(url.hostname).is_loopback
        except (ValueError, TypeError):
            loopback = False
        try:
            port = url.port or 554
        except ValueError:
            port = 0
        valid_token = re.fullmatch(
            r"rtsp://(?:127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?(?:/[a-zA-Z0-9_.~%/-]*)?",
            rtsp_url) is not None
        if (url.scheme != "rtsp" or not loopback or not valid_token or not 1 <= port <= 65535 or
                url.username or url.password or url.query or url.fragment or
                len(rtsp_url) > 1024):
            raise ValueError("M15.2 RTSP is restricted to explicit unauthenticated loopback TCP URLs")
        source = ["rtspsrc", "location=" + rtsp_url, "protocols=tcp", "latency=120", "!",
                  "rtph264depay", "!", "h264parse", "!", "avdec_h264"]
    else:
        raise ValueError("Unknown live capture source; no default device discovery")
    return source, fps


def capture_live(output, *, source="test", limit=30, fps="30", width=128, height=72,
                 gst_launch=None, device=None, rtsp_url=None, allow_network=False,
                 timeout=TIMEOUT_SECONDS, retries=0):
    """Finite opt-in capture; bounded queue/drop policy and whole-attempt retry.

    RTSP and V4L2 are manually admitted but *not* certified by synthetic CI.
    No background server or multicast/broadcast listener is created.
    """
    target = _target_directory(output)
    if type(retries) is not int or not 0 <= retries <= MAX_RETRIES:
        raise ValueError("Reconnect attempts must be between 0 and 2")
    head, nominal = _admit_live_source(source, rtsp_url=rtsp_url, device=device,
                                       allow_network=allow_network, limit=limit,
                                       fps=fps, width=width, height=height)
    launcher = _launcher(gst_launch)
    failures = []
    for attempt in range(retries + 1):
        try:
            with TemporaryDirectory(prefix=".fr-m15-live-", dir=target.parent) as tmp:
                stage = Path(tmp)
                pipeline = [*head, "!", "queue", "max-size-buffers=4", "max-size-bytes=0",
                            "max-size-time=0", "leaky=downstream", *_video_pipeline(live=True, limit=limit)]
                output_log = _run([launcher, "-m", "-e", *pipeline], cwd=stage, timeout=timeout)
                pts = _messages(output_log)
                if len(pts) != limit:
                    raise ValueError("Live capture stopped before the declared frame limit")
                result = _frame_evidence(stage, pts, width=width, height=height)
                index = {"schema": LIVE_SCHEMA, "source": source, "frames": result,
                         "nominal_fps": str(nominal), "dimensions": [width, height],
                         "backpressure": "queue(4), leaky=downstream",
                         "dropped_frames_possible": True, "attempt": attempt + 1,
                         "reconnections": attempt, "clock": "GStreamer live PTS nanoseconds",
                         "audio_muxed": False, "network_bound": False}
                (stage / "index.json").write_text(json.dumps(index, sort_keys=True, indent=2) + "\n",
                                                  encoding="utf-8")
                _publish_directory(target, stage)
                return index
        except (RuntimeError, ValueError, TimeoutError) as exc:
            failures.append(type(exc).__name__)
            if attempt == retries:
                raise
    raise RuntimeError("Live capture exhausted reconnect budget")
