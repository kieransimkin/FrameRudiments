"""Explicit M15.1 GStreamer adapter CLI; no new compositor or media input hooks."""
import argparse
import json
import subprocess
import sys

from .gstreamer_m15 import capabilities, encode_video


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
    for command in (inspect, export):
        command.add_argument("--gst-launch")
        command.add_argument("--gst-inspect")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = capabilities(gst_launch=args.gst_launch, gst_inspect=args.gst_inspect)
        else:
            result = encode_video(args.manifest, args.output, gst_launch=args.gst_launch,
                                  gst_inspect=args.gst_inspect, ffmpeg=args.ffmpeg,
                                  ffprobe=args.ffprobe, bitrate_kbps=args.bitrate_kbps,
                                  preset=args.preset)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0 if args.command != "inspect" or result["available"] else 2
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("FrameRudiments GStreamer: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
