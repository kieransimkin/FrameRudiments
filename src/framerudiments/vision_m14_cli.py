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
    check = sub.add_parser("inspect-batch", help="Validate M14.3 exact-frame batch pins and provider policy")
    check.add_argument("manifest")
    batched = sub.add_parser("infer-batch", help="Run bounded shared-session ONNX batch and GPU/CPU parity")
    batched.add_argument("manifest")
    batched.add_argument("--output", required=True, help="New output directory, never overwritten")
    seed = sub.add_parser("seed-graph-batch", help="Populate exact M14.2 CPU graph cache from a verified batch")
    seed.add_argument("project", help="Pinned FrameRudiments project")
    seed.add_argument("manifest", help="The M14.3 batch used to create the output")
    seed.add_argument("batch_output", help="Existing verified M14.3 batch output directory")
    sub.add_parser("providers", help="List locally installed ONNX execution providers")
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
        elif args.command == "seed-graph-batch":
            from .project import Project
            from .vision_graph_m14 import VisionGraphSources
            from .vision_batch_m14 import seed_graph_cache
            project = Project.load(args.project)
            if "vision_sources" not in project.data:
                raise ValueError("Project has no opt-in vision_sources")
            owner = VisionGraphSources(project, project.data["vision_sources"])
            result = seed_graph_cache(owner, args.manifest, args.batch_output)
        elif args.command == "providers":
            import onnxruntime as runtime
            result = {"schema": "framerudiments.vision-providers.v1",
                      "installed": runtime.get_available_providers(),
                      "qualification": "provider presence is not proof of GPU kernel execution"}
        elif args.command == "inspect-batch":
            from .vision_batch_m14 import inspect_batch
            result = inspect_batch(args.manifest).document()
        elif args.command == "infer-batch":
            from .vision_batch_m14 import infer_batch
            result = infer_batch(args.manifest, args.output)
        else:
            result = infer(args.request, args.output)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, RuntimeError, TimeoutError, OSError, ImportError, subprocess.SubprocessError) as exc:
        print(f"FrameRudiments vision: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
