"""Inspect MIDI support, explicitly list inputs, or validate a device-free take."""
import argparse
import json

from .effect_graph import EffectGraph
from .midi_control_take_m15 import MIDIControlTake
from .midi_controls_m15 import capabilities
from .midi_protocol_m15 import APIS, input_ports
from .project import Project


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inspect')
    ports = commands.add_parser('ports')
    ports.add_argument('--api', choices=APIS, required=True)
    validate = commands.add_parser('validate')
    validate.add_argument('project')
    args = parser.parse_args(argv)
    if args.command == 'ports':
        result = input_ports(args.api)
    elif args.command == 'validate':
        project = Project.load(args.project)
        project.verify_inputs()
        if project.midi_control_take_ref is None:
            parser.error('Project needs a hash-pinned midi_control_take')
        take = MIDIControlTake.load(project.midi_control_take_ref, project, EffectGraph(project.effect_graph_spec))
        result = {'status': 'validated', 'take': take.evidence, 'replay_device_activity': False}
        project.verify_inputs()
    else:
        result = capabilities()
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
