"""Opt-in M12 colour and AOV command line tools; no effect on production render."""
import argparse
import json
import subprocess
import sys

from .colour_aov import (frame_from_manifest, inspect_exr, read_exr,
                         transform_ocio, write_exr)


def main(argv=None):
    parser = argparse.ArgumentParser(description="FrameRudiments M12 float32 colour and AOV tools")
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("export-exr", help="Publish pinned .npy AOV channels as lossless EXR")
    pack.add_argument("manifest")
    pack.add_argument("--output", required=True)
    inspect = commands.add_parser("inspect-exr", help="Verify EXR and report channel hashes")
    inspect.add_argument("file")
    inspect.add_argument("--sha256", required=True)
    convert = commands.add_parser("transform-exr", help="Transform trusted scene-linear EXR RGB with OCIO")
    convert.add_argument("file")
    convert.add_argument("--sha256", required=True)
    convert.add_argument("--output", required=True)
    convert.add_argument("--ocio-config", required=True)
    convert.add_argument("--config-sha256", required=True)
    convert.add_argument("--from-space", required=True)
    convert.add_argument("--to-space", required=True)
    convert.add_argument("--ocio-resource", action="append", nargs=2, metavar=("PATH", "SHA256"),
                         default=[], help="Additional pinned LUT or config dependency")
    args = parser.parse_args(argv)
    try:
        if args.command == "export-exr":
            frame, source = frame_from_manifest(args.manifest)
            result = write_exr(args.output, frame, source_evidence=source)
        elif args.command == "inspect-exr":
            result = inspect_exr(args.file, sha256=args.sha256)
        else:
            frame = read_exr(args.file, sha256=args.sha256)
            converted = transform_ocio(
                frame, config=args.ocio_config, config_sha256=args.config_sha256,
                source_space=args.from_space, target_space=args.to_space,
                pinned_resources=args.ocio_resource,
            )
            result = write_exr(args.output, converted,
                               source_evidence={"input_exr_sha256": args.sha256.lower(),
                                                "ocio_config_sha256": args.config_sha256.lower()})
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        print(f"FrameRudiments M12: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
