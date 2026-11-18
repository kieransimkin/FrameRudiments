"""Explicitly opt-in M14 computer vision commands; no model downloads."""

import argparse
import json
import subprocess
import sys

from .vision_m14 import infer, inspect_request


def main(argv=None):
    cli = argparse.ArgumentParser(description="Pinned offline ONNX analysis, never native geometry")
    sub = cli.add_subparsers(dest="command", required=True)
    info = sub.add_parser("inspect", help="Verify local pinned files and tensor contract without inference")
    info.add_argument("request")
    run = sub.add_parser("infer", help="Run CPU ONNX inference in a time-limited subprocess")
    run.add_argument("request")
    run.add_argument("--output", required=True, help="New directory containing estimate, preview and receipt")
    args = cli.parse_args(argv)
    try:
        result = inspect_request(args.request).document() if args.command == "inspect" else infer(
            args.request, args.output)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, RuntimeError, TimeoutError, OSError, subprocess.SubprocessError) as exc:
        print(f"FrameRudiments vision: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
