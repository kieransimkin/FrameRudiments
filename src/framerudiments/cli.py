import argparse
import json
import sys

from . import __version__
from .demo import create_demo, create_rudiment_demo, create_spectrum_demo
from .io import write_json
from .project import Project
from .rudiment_renderers import default_renderers
from .render import preflight, render


def main(argv=None):
    parser = argparse.ArgumentParser(description="FrameRudiments: traceable Shorts and lyric visualisers")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("renderers", help="List available built-in rudiment renderers")
    demo = commands.add_parser("demo", help="Create a synthetic audio/image/recording project")
    demo.add_argument("directory")
    demo_views = demo.add_mutually_exclusive_group()
    demo_views.add_argument("--spectrum", action="store_true", help="Analyse a synthetic sweep with optional StemLab 1.6+ and render its spectrum")
    demo_views.add_argument("--rudiments", action="store_true", help="Show particles, bars, waveforms and XY paths")
    demo.add_argument("--font", required=True)
    demo.add_argument("--width", type=int, default=360)
    demo.add_argument("--height", type=int, default=640)
    plan = commands.add_parser("plan", help="Validate pinned inputs and export the frame ledger")
    plan.add_argument("project")
    plan.add_argument("--output", required=True)
    for name in ("preflight", "render"):
        item = commands.add_parser(name)
        item.add_argument("project")
        item.add_argument("--output", required=True, help="A new evidence/output directory; never overwrites")
        item.add_argument("--ffmpeg")
        item.add_argument("--ffprobe")
        if name == "render":
            item.add_argument("--preset", default="fast")
            item.add_argument("--crf", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            factory = create_spectrum_demo if args.spectrum else create_rudiment_demo if args.rudiments else create_demo
            print(factory(args.directory, args.font, width=args.width, height=args.height))
        elif args.command == "renderers":
            print(json.dumps(default_renderers().names))
        elif args.command == "plan":
            output = __import__("pathlib").Path(args.output)
            if output.exists():
                raise ValueError("Plan output already exists")
            project = Project.load(args.project)
            from .compositor import Compositor
            Compositor(project)
            write_json(output, project.summary())
            print(output)
        else:
            runner = preflight if args.command == "preflight" else render
            extra = {"preset": args.preset, "crf": args.crf} if args.command == "render" else {}
            result = runner(args.project, args.output, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe, **extra)
            print(json.dumps({"state": result["state"], "frames": result["frames"], "output": args.output}))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, IndexError) as exc:
        print(f"FrameRudiments: {exc}", file=sys.stderr)
        return 2
