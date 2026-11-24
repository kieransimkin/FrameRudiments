"""Finite source-clocked OSC takes. Replay performs no network operation."""
from copy import deepcopy

from .control_take_m15 import (
    FiniteControlTake, MAX_EVENTS, MAX_TAKE_BYTES, source_identity, Binding,
    numeric_bindings_for as bindings_for,
)
from .io import fields

__all__ = ['OSCControlTake', 'RecordedOSCControls', 'Binding', 'bindings_for',
           'prepare_take_reference', 'source_identity', 'MAX_EVENTS', 'MAX_TAKE_BYTES', 'SCHEMA']

SCHEMA = 'framerudiments.osc-control-take.v1'


class OSCControlTake(FiniteControlTake):
    schema = SCHEMA
    protocol = 'OSC'
    make_bindings = staticmethod(bindings_for)


def prepare_take_reference(project, value):
    fields(value, {'path', 'sha256'}, 'OSC take reference')
    if set(value) != {'path', 'sha256'}:
        raise ValueError('OSC take reference fields are incomplete')
    if 'effect_graph' not in project.data:
        raise ValueError('osc_control_take requires an effect_graph')
    if (project.base / value['path']).stat().st_size > MAX_TAKE_BYTES:
        raise ValueError('OSC take exceeds its byte budget')
    return project.ref(value)


class RecordedOSCControls:
    def __init__(self, project, graph, native):
        original = getattr(project, '_osc_control_source_project', project)
        self.take = OSCControlTake.load(project.osc_control_take_ref, original, graph)
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
    def osc_evidence(self):
        return {**self.take.evidence, 'last_used': deepcopy(self.last_report),
                'native_subframes_per_encoded_frame': self.factor}
