"""Inspect OSC support or validate a pinned take without opening a socket."""
import argparse
import json

from .effect_graph import EffectGraph
from .osc_control_take_m15 import OSCControlTake
from .osc_controls_m15 import capabilities
from .project import Project


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    validate = commands.add_parser("validate")
    validate.add_argument("project")
    args = parser.parse_args(argv)
    result = capabilities()
    if args.command == "validate":
        project = Project.load(args.project)
        project.verify_inputs()
        if project.osc_control_take_ref is None:
            parser.error("Project needs a hash-pinned osc_control_take")
        take = OSCControlTake.load(project.osc_control_take_ref, project, EffectGraph(project.effect_graph_spec))
        result = {"status": "validated", "take": take.evidence}
        project.verify_inputs()
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
