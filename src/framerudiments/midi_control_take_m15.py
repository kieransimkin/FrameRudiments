"""MIDI numeric mappings and finite takes; no native MIDI import on replay."""
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction

from .control_take_m15 import FiniteControlTake, MAX_TAKE_BYTES, Binding, numeric_bindings_for
from .io import fields, integer, number

SCHEMA = 'framerudiments.midi-control-take.v1'


def control_key(kind, channel, controller=None):
    integer(channel, 'MIDI channel', minimum=1, maximum=16)
    if kind == 'cc':
        integer(controller, 'MIDI controller', maximum=119)
        return f'/midi/cc/{channel}/{controller}'
    if kind == 'pitchbend' and controller is None:
        return f'/midi/pitchbend/{channel}'
    raise ValueError('MIDI supports explicit CC 0..119 and pitchbend only')


@dataclass(frozen=True)
class MIDIBinding:
    native: Binding
    message: str
    channel: int
    controller: int | None
    minimum: int | float
    maximum: int | float

    @property
    def address(self):
        return self.native.address

    @property
    def target(self):
        return self.native.target

    def value(self, value):
        return self.native.value(value)

    def map_raw(self, raw):
        limit = 127 if self.message == 'cc' else 16383
        integer(raw, 'MIDI raw control value', maximum=limit)
        # Rational scaling gives exact integer endpoints and defined half-up
        # rounding, including negative ranges. Float targets stay native floats.
        mapped = Fraction(self.minimum) + Fraction(raw, limit) * (Fraction(self.maximum)-Fraction(self.minimum))
        if self.native.kind == 'integer':
            mapped = (2 * mapped.numerator + mapped.denominator) // (2 * mapped.denominator)
        else:
            mapped = float(mapped)
        return self.value(mapped)

    def document(self):
        result = {'message': self.message, 'channel': self.channel, 'node': self.native.node,
                  'property': self.native.property, 'minimum': self.minimum, 'maximum': self.maximum}
        if self.controller is not None:
            result['controller'] = self.controller
        return result


def bindings_for(project, graph, rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 64:
        raise ValueError('MIDI needs 1 to 64 explicit bindings')
    normal, configs = [], []
    for row in rows:
        allowed = {'message', 'channel', 'controller', 'node', 'property', 'minimum', 'maximum'}
        fields(row, allowed, 'MIDI binding')
        required = allowed if row.get('message') == 'cc' else allowed - {'controller'}
        if set(row) != required:
            raise ValueError('MIDI binding fields are incomplete or unsupported')
        key = control_key(row['message'], row['channel'], row.get('controller'))
        normal.append({'address': key, 'node': row['node'], 'property': row['property']})
        configs.append(row)
    admitted = numeric_bindings_for(project, graph, normal)
    result = []
    for native, row in zip(admitted, configs):
        for key in ('minimum', 'maximum'):
            number(row[key], f'MIDI mapping {key}', minimum=native.lower, maximum=native.upper)
            if native.kind == 'integer' and type(row[key]) is not int:
                raise ValueError('MIDI integer target mapping endpoints must be integers')
        result.append(MIDIBinding(native, row['message'], row['channel'], row.get('controller'),
                                  row['minimum'], row['maximum']))
    return tuple(result)


class MIDIControlTake(FiniteControlTake):
    schema = SCHEMA
    protocol = 'MIDI'
    event_key = 'control'
    closed_key = 'input_closed'
    active_key = 'active_controls'
    expired_key = 'expired_controls'
    extra_event_fields = {'raw'}
    make_bindings = staticmethod(bindings_for)

    def validate_event(self, row, binding):
        if binding.map_raw(row['raw']) != row['value']:
            raise ValueError('MIDI raw value does not reproduce the mapped native value')


def prepare_take_reference(project, value):
    fields(value, {'path', 'sha256'}, 'MIDI take reference')
    if set(value) != {'path', 'sha256'} or 'effect_graph' not in project.data:
        raise ValueError('midi_control_take needs effect_graph and a complete file pin')
    if (project.base / value['path']).stat().st_size > MAX_TAKE_BYTES:
        raise ValueError('MIDI take exceeds its byte budget')
    return project.ref(value)


class RecordedMIDIControls:
    def __init__(self, project, graph, native):
        original = getattr(project, '_midi_control_source_project', project)
        self.take = MIDIControlTake.load(project.midi_control_take_ref, original, graph)
        self.native = native
        self.factor = getattr(project, '_time_sampling_factor', 1)
        self.last_report = None

    def __getattr__(self, name):
        return getattr(self.native, name)

    def values(self, context, graph):
        encoded = context.frame // self.factor
        result = self.native.values(context, graph)
        result.update(self.take.values_at(encoded))
        self.last_report = self.take.report_at(encoded)
        return result

    @property
    def midi_evidence(self):
        return {**self.take.evidence, 'last_used': deepcopy(self.last_report),
                'native_subframes_per_encoded_frame': self.factor,
                'replay_device_activity': False}
