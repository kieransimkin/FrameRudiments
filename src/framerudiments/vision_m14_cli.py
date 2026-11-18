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
    cache = sub.add_parser("warm-graph", help="Precompute pinned project vision sources without rendering")
    cache.add_argument("project")
    cache.add_argument("--frame", type=int, action="append", required=True,
                       help="Exact encoded frame to warm; may repeat")
    cache.add_argument("--source", action="append", default=[],
                       help="Full graph ID, e.g. vision:people-mask; defaults to all")
    args = cli.parse_args(argv)
    try:
        if args.command == "warm-graph":
            from .project import Project
            from .vision_graph_m14 import VisionGraphSources
            project = Project.load(args.project)
            if "vision_sources" not in project.data:
                raise ValueError("Project has no opt-in vision_sources")
            provider = VisionGraphSources(project, project.data["vision_sources"])
            names = args.source or sorted(provider.source_ids)
            if any(name not in provider.source_ids for name in names):
                raise ValueError("Unknown vision graph source in warm-graph request")
            receipts = []
            for frame in sorted(set(args.frame)):
                for name in names:
                    image = provider.source(name, frame)
                    image.close()
                    receipts.append({"frame": frame, "source": name,
                                     "estimate": provider.last_used.get(name)})
            project.verify_inputs()
            result = {"schema": "framerudiments.vision-graph-warm.v1",
                      "model_estimated": True, "sources": receipts}
        elif args.command == "inspect":
            result = inspect_request(args.request).document()
        else:
            result = infer(args.request, args.output)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, RuntimeError, TimeoutError, OSError, subprocess.SubprocessError) as exc:
        print(f"FrameRudiments vision: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
