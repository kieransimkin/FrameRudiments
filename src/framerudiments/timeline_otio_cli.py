"""Explicit M13 timeline import/export commands; no implicit media access."""

import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys

from .timeline_otio_m13 import (export_otio, import_otio, manifest_file, project_manifest,
                               summary, verify_sources)


def _parse_fps(value):
    try:
        fps = Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError("FPS must be an exact rational, e.g. 30000/1001") from exc
    return [fps.numerator, fps.denominator]


def main(argv=None):
    parser = argparse.ArgumentParser(description="FrameRudiments M13 exact-frame OpenTimelineIO interchange")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate a native timeline manifest")
    validate.add_argument("manifest")
    validate.add_argument("--verify-sources", action="store_true", help="SHA-256-check local referenced bytes")
    validate.add_argument("--allow-unpinned", action="store_true")
    export = commands.add_parser("export", help="Export a native timeline manifest to .otio")
    export.add_argument("manifest")
    export.add_argument("--output", required=True)
    export.add_argument("--verify-sources", action="store_true")
    export.add_argument("--allow-unpinned", action="store_true")
    imported = commands.add_parser("import", help="Import a flat .otio to a native JSON timeline")
    imported.add_argument("otio")
    imported.add_argument("--output", required=True)
    imported.add_argument("--fps", help="Explicit output FPS for third-party OTIO without private metadata")
    imported.add_argument("--allow-unpinned", action="store_true")
    project = commands.add_parser("project-export", help="Export one native project's pinned audio/cues")
    project.add_argument("project")
    project.add_argument("--output", required=True, help="New .otio beside the project file")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            document = manifest_file(args.manifest, allow_unpinned=args.allow_unpinned)
            receipt = summary(document)
            if args.verify_sources:
                receipt["verified_media"] = verify_sources(document, Path(args.manifest).resolve().parent)
        elif args.command == "export":
            document = manifest_file(args.manifest, allow_unpinned=args.allow_unpinned)
            receipt = export_otio(document, args.output, allow_unpinned=args.allow_unpinned,
                                  media_root=Path(args.output).resolve().parent
                                  if args.verify_sources else None)
        elif args.command == "import":
            receipt = import_otio(args.otio, args.output, fps=_parse_fps(args.fps) if args.fps else None,
                                  allow_unpinned=args.allow_unpinned)
        else:
            from .project import Project

            project_path = Path(args.project).resolve(strict=True)
            output = Path(args.output).resolve().parent
            if output != project_path.parent:
                raise ValueError("Portable project OTIO export must be beside the original project")
            native = Project.load(project_path)
            document = project_manifest(native)
            receipt = export_otio(document, args.output, media_root=native.base)
        print(json.dumps(receipt, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, UnicodeError, KeyError, TypeError, OverflowError) as exc:
        print(f"FrameRudiments timeline: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
