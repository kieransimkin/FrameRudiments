"""Shared finite numeric takes: source pins, leases, replay and atomic storage.

No transport module or device library is imported while loading or replaying.
"""
from bisect import bisect_right
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .effect_graph_modulation import MAX_GRAPH_CONTROLS
from .io import fields, integer, number, parse_json
from .preview_stream_contract import canonical_digest

MAX_EVENTS = 8192
MAX_TAKE_BYTES = 2 * 1024 * 1024


def _without_paths(value):
    if isinstance(value, dict):
        if set(value) == {'path', 'sha256'}:
            return {'sha256': value['sha256']}
        return {key: _without_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_paths(item) for item in value]
    return value


def source_identity(project):
    if 'effect_graph' not in project.data:
        raise ValueError('Captured controls require an effect graph')
    return {'source_audio_sha256': project.audio_ref['sha256'], 'sample_rate': project.sample_rate,
            'start_sample': project.start_sample, 'end_sample_exclusive': project.end_sample,
            'repeats': project.repeats, 'fps': project.fps, 'frame_count': project.frame_count,
            'clock_sha256': canonical_digest(_without_paths(project.data['clock'])),
            'graph_sha256': canonical_digest(_without_paths(project.data['effect_graph'])),
            'native_controls_sha256': canonical_digest(_without_paths({
                'controls': project.data.get('effect_graph_controls', []),
                'stem_sources': project.data.get('stem_sources', {})}))}


@dataclass(frozen=True)
class Binding:
    address: str
    node: str
    property: str
    kind: str
    lower: float
    upper: float

    @property
    def target(self):
        return self.node, self.property

    def document(self):
        return {'address': self.address, 'node': self.node, 'property': self.property}

    def value(self, value):
        number(value, 'Control value', minimum=self.lower, maximum=self.upper)
        if self.kind == 'integer' and type(value) is not int:
            raise ValueError('Integer targets require an integer value')
        return value


def numeric_bindings_for(project, graph, rows):
    # Do not import the network module during replay.
    import re
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_GRAPH_CONTROLS:
        raise ValueError('Control bindings need 1 to 64 explicit numeric targets')
    native = project.data.get('effect_graph_controls', [])
    if not isinstance(native, list) or any(not isinstance(row, dict) for row in native):
        raise ValueError('Native graph controls must be a binding list')
    native_targets = {(row.get('node'), row.get('property')) for row in native}
    result, addresses, targets = [], set(), set()
    for row in rows:
        fields(row, {'address', 'node', 'property'}, 'Control binding')
        if set(row) != {'address', 'node', 'property'}:
            raise ValueError('Control binding fields are incomplete')
        address, node, prop = row['address'], row['node'], row['property']
        if (not isinstance(address, str) or len(address) > 128 or
                not re.fullmatch(r'/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', address)):
            raise ValueError('Control address must be a bounded literal ASCII path')
        if not isinstance(node, str) or not isinstance(prop, str):
            raise ValueError('Control target needs native node and property names')
        graph.validate_control_target(node, prop)
        descriptor = graph.control_descriptors(node).get(prop, {})
        if descriptor.get('type') not in ('number', 'integer') or not descriptor.get('animatable'):
            raise ValueError('Control supports numeric animatable parameters only')
        if address in addresses or (node, prop) in targets or (node, prop) in native_targets:
            raise ValueError('Control targets and addresses must be unique and separate from native bindings')
        addresses.add(address)
        targets.add((node, prop))
        lower, upper = descriptor['range']
        result.append(Binding(address, node, prop, descriptor['type'], lower, upper))
    if len(native_targets | targets) > MAX_GRAPH_CONTROLS:
        raise ValueError('Combined native and OSC controls exceed 64 targets')
    return tuple(result)


class FiniteControlTake:
    """Immutable-by-contract data; arbitrary frame reads have no mutable history."""
    schema = None
    protocol = 'control'
    event_key = 'address'
    closed_key = 'receiver_closed'
    extra_event_fields = set()
    active_key = 'active_addresses'
    expired_key = 'expired_addresses'

    @staticmethod
    def make_bindings(project, graph, rows):
        raise NotImplementedError

    def validate_event(self, row, binding):
        pass

    def __init__(self, document, project, graph):
        allowed = {'schema', 'source', 'bindings', 'lease_frames', 'events', 'capture'}
        fields(document, allowed, f'{self.protocol} take')
        if set(document) != allowed:
            raise ValueError(f'{self.protocol} take fields are incomplete')
        if (document['schema'] != self.schema or
                canonical_digest(document['source']) != canonical_digest(source_identity(project))):
            raise ValueError(f'{self.protocol} source, graph or clock differs from the native project')
        self.bindings = self.make_bindings(project, graph, document['bindings'])
        self.lease_frames = integer(document['lease_frames'], f'{self.protocol} source-frame lease',
                                    minimum=1, maximum=project.frame_count)
        self.frame_count = project.frame_count
        capture = document['capture']
        if (not isinstance(capture, dict) or capture.get(self.closed_key) is not True or
                type(capture.get('last_frame')) is not int or capture['last_frame'] != project.frame_count - 1):
            raise ValueError(f'{self.protocol} take needs a closed receiver and a complete finite frame extent')
        rows = document['events']
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_EVENTS:
            raise ValueError(f'{self.protocol} take needs a bounded nonempty event list')
        events = {binding.address: [] for binding in self.bindings}
        lookup = {binding.address: binding for binding in self.bindings}
        previous, boundaries = (-1, ''), {0, project.frame_count - 1}
        for row in rows:
            expected = {'frame', self.event_key, 'value', 'source_seconds', 'beat'} | self.extra_event_fields
            fields(row, expected, f'{self.protocol} event')
            if set(row) != expected:
                raise ValueError(f'{self.protocol} event fields are incomplete')
            frame = integer(row['frame'], f'{self.protocol} encoded frame', maximum=project.frame_count - 1)
            address = row[self.event_key]
            if not isinstance(address, str) or address not in lookup or (frame, address) <= previous:
                raise ValueError(f'{self.protocol} events need unique ordered frame/address pairs')
            previous = frame, address
            lookup[address].value(row['value'])
            self.validate_event(row, lookup[address])
            if (number(row['source_seconds'], f'{self.protocol} source seconds') != project.source_time(frame) or
                    number(row['beat'], f'{self.protocol} continuous beat') != project.continuous_beat(frame)):
                raise ValueError(f'{self.protocol} event timing differs from the native clock')
            events[address].append((frame, row['value']))
            boundaries.update((frame, min(project.frame_count - 1, frame + self.lease_frames + 1)))
        self._events = {key: tuple(value) for key, value in events.items()}
        self._indices = {key: tuple(frame for frame, _ in value) for key, value in self._events.items()}
        for frame in sorted(boundaries):
            graph.apply_control_values(self.values_at(frame))
        self._bytes = json.dumps(document, ensure_ascii=True, allow_nan=False,
                                 sort_keys=True, separators=(',', ':')).encode('utf-8')
        if len(self._bytes) > MAX_TAKE_BYTES:
            raise ValueError(f'{self.protocol} take exceeds its byte budget')
        self.sha256 = hashlib.sha256(self._bytes).hexdigest()

    def document(self):
        return json.loads(self._bytes)

    def _state(self, frame):
        integer(frame, f'{self.protocol} playback frame', maximum=self.frame_count - 1)
        values, active, expired = {}, [], []
        for binding in self.bindings:
            index = bisect_right(self._indices[binding.address], frame) - 1
            if index < 0:
                continue
            at, value = self._events[binding.address][index]
            if frame - at > self.lease_frames:
                expired.append(binding.address)
            else:
                values[binding.target] = value
                active.append(binding.address)
        return values, active, expired

    def values_at(self, frame):
        return self._state(frame)[0]

    def report_at(self, frame):
        _, active, expired = self._state(frame)
        return {'encoded_frame': frame, self.active_key: active, self.expired_key: expired,
                'expiry_policy': 'release_to_native_base', 'lease_frames': self.lease_frames}

    @property
    def evidence(self):
        data = self.document()
        return {'schema': self.schema, 'take_sha256': self.sha256, 'source': data['source'],
                'event_count': len(data['events']), 'binding_count': len(self.bindings),
                'lease_frames': self.lease_frames, 'capture': data['capture'],
                'replay_network_activity': False}

    def write(self, path):
        path = Path(path)
        descriptor, pending = tempfile.mkstemp(prefix=f'.{self.protocol.lower()}-take-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(self._bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(pending, path)  # Atomic no-overwrite, as in the other M15 adapters.
        finally:
            Path(pending).unlink(missing_ok=True)
        return {'path': str(path), 'sha256': self.sha256}

    @classmethod
    def load(cls, reference, project, graph):
        with Path(reference['path']).open('rb') as stream:
            raw = stream.read(MAX_TAKE_BYTES + 1)
        if len(raw) > MAX_TAKE_BYTES or hashlib.sha256(raw).hexdigest() != reference['sha256']:
            raise ValueError(f'{cls.protocol} take bytes differ from the pin or exceed the byte budget')
        take = cls(parse_json(raw.decode('utf-8-sig')), project, graph)
        # Keep evidence and a subsequent write bound to the exact validated file,
        # including legal JSON whitespace, rather than a canonical re-encoding.
        take._bytes = raw
        take.sha256 = reference['sha256']
        return take
