"""Opt-in, bounded OpenUSD scene and real-geometry AOV commands."""
import argparse
import json
import sys

from .usd_m15 import capabilities, inspect, render_exr


def main(argv=None):
    parser = argparse.ArgumentParser(description="Trusted, pinned OpenUSD ASCII scene interchange")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", help="Read OpenUSD availability (Hydra never presumed available)")
    for name in ("inspect", "render-exr"):
        command = commands.add_parser(name)
        command.add_argument("source", help="Trusted, single-layer .usda scene")
        command.add_argument("--sha256", required=True)
        command.add_argument("--camera", required=True, help="Literal USD camera prim path")
        command.add_argument("--frame", type=int, required=True)
        command.add_argument("--fps", default="24", help="Exact rational video FPS, e.g. 24000/1001")
        command.add_argument("--width", type=int, default=320)
        command.add_argument("--height", type=int, default=180)
        command.add_argument("--timeout", type=int, default=45)
        if name == "render-exr":
            command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "capabilities":
            result = capabilities()
        else:
            common = dict(camera=args.camera, frame=args.frame, fps=args.fps,
                          width=args.width, height=args.height, timeout=args.timeout)
            if args.command == "inspect":
                result = inspect(args.source, args.sha256, **common)
            else:
                result = render_exr(args.source, args.sha256, args.output, **common)
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print("FrameRudiments USD: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
