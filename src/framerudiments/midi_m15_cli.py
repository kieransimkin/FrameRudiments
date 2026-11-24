"""Inspect MIDI support, explicitly list inputs, or validate a device-free take."""
import argparse
import json
from pathlib import Path

from .io import sha256
from .midi_notes import import_midi_notes
from .note_view_dispatch import SETTINGS

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
    notes = commands.add_parser('import-notes', help='Normalize SMF 0/1 notes without opening a device')
    notes.add_argument('project')
    notes.add_argument('midi')
    notes.add_argument('--output', required=True)
    notes.add_argument('--offset-seconds', type=float, required=True)
    note_check = commands.add_parser('validate-notes')
    note_check.add_argument('project')
    args = parser.parse_args(argv)
    if args.command == 'ports':
        result = input_ports(args.api)
    elif args.command == 'import-notes':
        project = Project.load(args.project)
        path = Path(args.midi).resolve()
        take = import_midi_notes(project, {'path': str(path), 'sha256': sha256(path)},
                                 offset_seconds=args.offset_seconds)
        result = {'status': 'imported', 'file': take.write(args.output), 'notes': take.evidence,
                  'replay_device_activity': False}
    elif args.command == 'validate-notes':
        from .compositor import Compositor
        project = Project.load(args.project)
        project.verify_inputs()
        compositor = Compositor(project)
        try:
            rows = [layer.evidence for layer in compositor.layers if hasattr(layer, 'timeline')]
            if not rows:
                parser.error('Project needs at least one type notes layer')
            result = {'status': 'validated', 'note_layers': rows, 'replay_device_activity': False}
            project.verify_inputs()
        finally:
            compositor.close()
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
        result['note_visualisers'] = {'schema': 'framerudiments.note-timeline.v1',
            'views': ['piano-roll (falling or causal rising)', 'note-highway (explicit lanes)', *SETTINGS],
            'capture': 'MIDINoteCapture; same qualified MIDI input ownership',
            'messages': ['note-on/off', 'velocity-zero off', 'CC64 sustain', 'CC120/123 panic/release'],
            'smf': 'Optional notes extra; type 0/1, PPQN and native tempo map',
            'replay_devices': False}
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
