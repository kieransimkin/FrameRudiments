"""Explicit source-frame OSC capture; ordinary exports only replay frozen takes."""
from collections import Counter
from copy import deepcopy

from .effect_graph import EffectGraph
from .io import integer
from .osc_control_take_m15 import (
    MAX_EVENTS, SCHEMA, OSCControlTake, bindings_for, source_identity,
)
from .osc_protocol_m15 import LoopbackReceiver, MAX_PACKET_BYTES, MAX_POLL_PACKETS
from .preview_stream_contract import canonical_digest


def capabilities():
    return {'schema': SCHEMA, 'experimental': True, 'network_default': 'closed',
            'transport': 'explicit IPv4 loopback UDP with exact peer endpoint',
            'osc_subset': ['literal addresses', 'one int32 or float32 argument'],
            'unsupported': ['bundles', 'timetags', 'patterns', 'strings', 'MIDI', 'LAN', 'device discovery'],
            'max_packet_bytes': MAX_PACKET_BYTES, 'max_poll_packets': MAX_POLL_PACKETS,
            'max_bindings': 64, 'max_events': MAX_EVENTS,
            'clock': 'existing Project encoded frames, source time and continuous beats',
            'lease_policy': 'source-frame lease; expiry releases the target to its native base',
            'capture_owner': 'synchronous context manager; no background thread',
            'replay': 'pinned finite take; random access; no network activity',
            'authentication': 'none; exact peer filtering is not authentication'}


class OSCControlCapture:
    """Only the caller's existing native transport supplies media frame positions."""
    def __init__(self, project, bindings, *, peer, bind=('127.0.0.1', 0), lease_frames=60):
        if any(key in project.data for key in ('osc_control_take', 'midi_control_take')):
            raise ValueError('Capture the source project before attaching an OSC take')
        self.project = project
        self.graph = EffectGraph(project.effect_graph_spec) if project.effect_graph_spec else None
        if self.graph is None:
            raise ValueError('OSC capture requires an effect graph')
        self.bindings = bindings_for(project, self.graph, bindings)
        self._lookup = {binding.address: binding for binding in self.bindings}
        self._source = source_identity(project)
        self.lease_frames = integer(lease_frames, 'OSC source-frame lease', minimum=1,
                                    maximum=project.frame_count)
        self.receiver = LoopbackReceiver(bind, peer)
        self._last_frame = -1
        self._history = []
        self._latest = {}
        self._statistics = Counter()

    @property
    def endpoint(self):
        return self.receiver.bound

    def __enter__(self):
        self.project.verify_inputs()
        self.receiver.__enter__()
        return self

    def close(self):
        self.receiver.close()

    def __exit__(self, *_exc):
        self.close()

    def wait_for_input(self, timeout=1):
        return self.receiver.wait_ready(timeout)

    def sample(self, frame):
        integer(frame, 'OSC encoded frame', maximum=self.project.frame_count - 1)
        if frame <= self._last_frame or self._last_frame < 0 and frame != 0:
            raise ValueError('OSC capture needs increasing native frames starting at zero')
        messages, rejected_peer, rejected_message = self.receiver.poll()
        self._statistics['rejected_peer'] += rejected_peer
        self._statistics['rejected_message'] += rejected_message
        self._statistics['received_packets'] += len(messages) + rejected_peer + rejected_message
        incoming = {}
        for address, value in messages:
            if address not in self._lookup:
                self._statistics['unknown_address'] += 1
                continue
            try:
                incoming[address] = self._lookup[address].value(value)
            except ValueError:
                self._statistics['rejected_value'] += 1
        candidate = {address: value for address, (at, value) in self._latest.items()
                     if frame - at <= self.lease_frames}
        candidate.update(incoming)
        try:
            self.graph.apply_control_values(
                {self._lookup[address].target: value for address, value in candidate.items()})
        except ValueError:
            self._statistics['rejected_batch'] += 1
            incoming = {}
        if len(self._history) + len(incoming) > MAX_EVENTS:
            self.close()
            raise RuntimeError('OSC event budget exceeded; capture stopped')
        for address, value in sorted(incoming.items()):
            self._history.append({'frame': frame, 'address': address, 'value': value,
                                  'source_seconds': self.project.source_time(frame),
                                  'beat': self.project.continuous_beat(frame)})
            self._latest[address] = frame, value
        self._statistics['accepted_events'] += len(incoming)
        self._statistics['sampled_frames'] += 1
        self._last_frame = frame
        return {'encoded_frame': frame, 'accepted_addresses': sorted(incoming),
                'statistics': dict(self._statistics)}

    def freeze(self):
        if (not self.receiver.closed or self.receiver.socket is not None or
                self._last_frame != self.project.frame_count - 1):
            raise ValueError('Close after sampling the programme final frame before freezing')
        self.project.verify_inputs()
        if canonical_digest(source_identity(self.project)) != canonical_digest(self._source):
            raise ValueError('Native source identity changed during capture')
        data = {'schema': SCHEMA, 'source': self._source,
                'bindings': [binding.document() for binding in self.bindings],
                'lease_frames': self.lease_frames, 'events': self._history,
                'capture': {'receiver_closed': True, 'last_frame': self._last_frame,
                            'transport': 'IPv4 loopback UDP', 'bind': list(self.endpoint),
                            'peer': list(self.receiver.peer), 'packet_order': 'socket_receive_order',
                            'statistics': dict(self._statistics)}}
        return OSCControlTake(deepcopy(data), self.project, self.graph)
