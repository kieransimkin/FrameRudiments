"""Opt-in MIDI capture sampled only by the existing native media owner."""
from collections import Counter
from copy import deepcopy

from .control_take_m15 import MAX_EVENTS, source_identity
from .effect_graph import EffectGraph
from .io import integer
from .midi_control_take_m15 import MIDIControlTake, SCHEMA, bindings_for
from .midi_protocol_m15 import MIDIInput, MAX_POLL_MESSAGES, QUEUE_LIMIT, decode_message, inspect
from .preview_stream_contract import canonical_digest


def capabilities():
    return {'schema': SCHEMA, 'experimental': True, 'device_default': 'closed',
            'messages': ['MIDI 1.0 CC 0..119 (7-bit)', 'MIDI 1.0 pitchbend (14-bit unsigned)'],
            'channels': 'explicit 1..16', 'mapping': 'explicit native numeric endpoints; integer half-up',
            'backend': inspect(), 'clock': 'existing Project encoded/source/continuous-beat clock',
            'ownership': 'synchronous context, bounded native queue, exact close/delete',
            'max_bindings': 64, 'max_events': MAX_EVENTS, 'queue_size_limit': QUEUE_LIMIT,
            'max_poll_messages': MAX_POLL_MESSAGES, 'replay_device_activity': False,
            'unsupported': ['notes', 'channel mode', 'SysEx', 'MIDI clock/transport', 'MIDI 2.0',
                            '14-bit paired CC', 'RPN/NRPN', 'SMF import', 'automatic discovery/reconnect']}


class MIDIControlCapture:
    def __init__(self, project, bindings, *, api, port_name, lease_frames=60):
        if any(key in project.data for key in ('osc_control_take', 'midi_control_take')):
            raise ValueError('Capture the source project before attaching a control take')
        self.project = project
        self.graph = EffectGraph(project.effect_graph_spec) if project.effect_graph_spec else None
        if self.graph is None:
            raise ValueError('MIDI capture requires an effect graph')
        self.bindings = bindings_for(project, self.graph, bindings)
        self._lookup = {binding.address: binding for binding in self.bindings}
        self._source = source_identity(project)
        self.lease_frames = integer(lease_frames, 'MIDI source-frame lease', minimum=1,
                                    maximum=project.frame_count)
        self.receiver = MIDIInput(api, port_name)
        self._last_frame = -1
        self._history = []
        self._latest = {}
        self._statistics = Counter()
        self._failed = False

    def __enter__(self):
        self.project.verify_inputs()
        self.receiver.__enter__()
        return self

    def close(self):
        self.receiver.close()

    def __exit__(self, exc_type, *_exc):
        self._failed |= exc_type is not None
        self.close()

    def wait_for_input(self, timeout=1, *, minimum_messages=1):
        try:
            return self.receiver.wait_ready(timeout, minimum_messages=minimum_messages)
        except BaseException:
            self._failed = True
            self.close()
            raise

    def sample(self, frame):
        integer(frame, 'MIDI encoded frame', maximum=self.project.frame_count - 1)
        if frame <= self._last_frame or self._last_frame < 0 and frame != 0:
            raise ValueError('MIDI capture needs increasing native frames starting at zero')
        try:
            return self._sample(frame)
        except BaseException:
            self._failed = True
            self.close()
            raise

    def _sample(self, frame):
        messages = self.receiver.poll()
        self._statistics['received_messages'] += len(messages)
        incoming = {}
        for message in messages:
            try:
                key, raw = decode_message(message)
            except ValueError:
                self._statistics['rejected_message'] += 1
                continue
            if key not in self._lookup:
                self._statistics['unbound_control'] += 1
                continue
            incoming[key] = raw, self._lookup[key].map_raw(raw)
        candidate = {key: value for key, (at, value) in self._latest.items()
                     if frame - at <= self.lease_frames}
        candidate.update({key: value for key, (_, value) in incoming.items()})
        try:
            self.graph.apply_control_values({self._lookup[key].target: value for key, value in candidate.items()})
        except ValueError:
            self._statistics['rejected_batch'] += 1
            incoming = {}
        if len(self._history) + len(incoming) > MAX_EVENTS:
            raise RuntimeError('MIDI event budget exceeded; capture stopped')
        for key, (raw, value) in sorted(incoming.items()):
            self._history.append({'frame': frame, 'control': key, 'raw': raw, 'value': value,
                                  'source_seconds': self.project.source_time(frame),
                                  'beat': self.project.continuous_beat(frame)})
            self._latest[key] = frame, value
        self._statistics['accepted_events'] += len(incoming)
        self._statistics['sampled_frames'] += 1
        self._last_frame = frame
        return {'encoded_frame': frame, 'accepted_controls': sorted(incoming),
                'statistics': dict(self._statistics)}

    def freeze(self):
        if (self._failed or not self.receiver.closed or self.receiver.native is not None or
                self.receiver.error_count or self._last_frame != self.project.frame_count - 1):
            raise ValueError('Close a healthy MIDI input after the final programme frame before freezing')
        self.project.verify_inputs()
        if canonical_digest(source_identity(self.project)) != canonical_digest(self._source):
            raise ValueError('Native source identity changed during MIDI capture')
        data = {'schema': SCHEMA, 'source': self._source,
                'bindings': [binding.document() for binding in self.bindings],
                'lease_frames': self.lease_frames, 'events': self._history,
                'capture': {**self.receiver.evidence, 'last_frame': self._last_frame,
                            'message_order': 'native_input_queue', 'statistics': dict(self._statistics)}}
        return MIDIControlTake(deepcopy(data), self.project, self.graph)
