"""CLI boundary for manually admitted, checksum-pinned native OpenFX plugins."""
import argparse
import json
import subprocess
import sys

from .openfx_m15 import describe, render


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in offline OpenFX v1 Filter/RGBA8 host")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Validate pinned binary architecture without loading it")
    scan.add_argument("manifest")
    effect = commands.add_parser("render", help="Render exactly one frame in an owned plugin process")
    effect.add_argument("manifest")
    effect.add_argument("--output", required=True, help="New PNG destination; never overwrites")
    args = parser.parse_args(argv)
    try:
        info = describe(args.manifest) if args.command == "scan" else render(args.manifest, args.output)
        print(json.dumps(info, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        print("FrameRudiments OpenFX: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
