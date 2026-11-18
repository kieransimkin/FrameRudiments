"""Explicit M15.1 GStreamer adapter CLI; no new compositor or media input hooks."""
import argparse
import json
import subprocess
import sys

from .gstreamer_m15 import capabilities, encode_video
from .gstreamer_ingest_m15 import capture_live, ingest_video, ingest_capabilities


def main(argv=None):
    parser = argparse.ArgumentParser(description="Optional pinned, offline GStreamer video export")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Inspect installed, trusted GStreamer elements")
    export = commands.add_parser("encode-video", help="Encode M11.4 pinned RGB frames as H.264/MP4")
    export.add_argument("manifest")
    export.add_argument("--output", required=True)
    export.add_argument("--ffmpeg")
    export.add_argument("--ffprobe")
    export.add_argument("--bitrate-kbps", type=int, default=4000)
    export.add_argument("--preset", default="fast")
    inspect_ingest = commands.add_parser("inspect-ingest", help="Inspect required GStreamer decode/capture plugins")
    ingest = commands.add_parser("ingest-video", help="Decode pinned video preserving actual VFR PTS")
    ingest.add_argument("source")
    ingest.add_argument("--sha256", required=True)
    ingest.add_argument("--output", required=True, help="New output directory, never overwritten")
    ingest.add_argument("--audio-source", help="Optional separately pinned audio reference")
    ingest.add_argument("--audio-sha256", help="Required with --audio-source")
    ingest.add_argument("--audio-rate", type=int, help="Caller-declared alternative sample rate")
    ingest.add_argument("--audio-origin", help="Exact rational audio origin seconds")
    ingest.add_argument("--ffmpeg")
    ingest.add_argument("--ffprobe")
    ingest.add_argument("--timeout", type=int, default=120)
    live = commands.add_parser("capture-live", help="Finite opt-in capture, no default device/network")
    live.add_argument("--source", choices=("test", "v4l2", "rtsp"), required=True)
    live.add_argument("--output", required=True, help="New output directory")
    live.add_argument("--device", help="Explicit permitted /dev/videoN")
    live.add_argument("--rtsp-url", help="Explicit unauthenticated loopback RTSP source")
    live.add_argument("--allow-network", action="store_true")
    live.add_argument("--frames", type=int, default=24)
    live.add_argument("--fps", default="30")
    live.add_argument("--width", type=int, default=128)
    live.add_argument("--height", type=int, default=72)
    live.add_argument("--timeout", type=int, default=120)
    live.add_argument("--retries", type=int, default=0)
    for command in (inspect, inspect_ingest, export, ingest, live):
        command.add_argument("--gst-launch")
        command.add_argument("--gst-inspect")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = capabilities(gst_launch=args.gst_launch, gst_inspect=args.gst_inspect)
        elif args.command == "inspect-ingest":
            result = ingest_capabilities(gst_launch=args.gst_launch, gst_inspect=args.gst_inspect)
        elif args.command == "encode-video":
            result = encode_video(args.manifest, args.output, gst_launch=args.gst_launch,
                                  gst_inspect=args.gst_inspect, ffmpeg=args.ffmpeg,
                                  ffprobe=args.ffprobe, bitrate_kbps=args.bitrate_kbps,
                                  preset=args.preset)
        elif args.command == "ingest-video":
            result = ingest_video(args.source, args.output, sha256=args.sha256,
                                  gst_launch=args.gst_launch, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                                  audio_source=args.audio_source, audio_sha256=args.audio_sha256,
                                  audio_rate=args.audio_rate, audio_origin=args.audio_origin,
                                  timeout=args.timeout)
        else:
            result = capture_live(args.output, source=args.source, limit=args.frames,
                                  fps=args.fps, width=args.width, height=args.height,
                                  device=args.device, rtsp_url=args.rtsp_url,
                                  allow_network=args.allow_network, gst_launch=args.gst_launch,
                                  timeout=args.timeout, retries=args.retries)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0 if args.command not in ("inspect", "inspect-ingest") or result["available"] else 2
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("FrameRudiments GStreamer: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
