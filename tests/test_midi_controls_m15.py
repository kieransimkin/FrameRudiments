"""Bounded MIDI ownership, independent wire vectors and native take replay.

The injected native port qualifies deterministic ownership/failure paths, not a
physical controller. Real RtMidi/JACK loopback is a separate required CI gate.
"""
from collections import deque
from copy import deepcopy
from hashlib import sha256
import builtins
import json
from pathlib import Path
import socket

import pytest
from PIL import Image

import test_osc_controls_m15 as osc_fixture
from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.effect_graph import EffectGraph
from framerudiments.midi_control_take_m15 import MIDIControlTake, MIDIBinding, bindings_for
from framerudiments.midi_controls_m15 import MIDIControlCapture
from framerudiments.midi_protocol_m15 import MIDIInput, decode_message, input_ports
from framerudiments.osc_control_take_m15 import Binding
import framerudiments.midi_protocol_m15 as protocol

source = osc_fixture.source
BINDINGS = [{'message': 'cc', 'channel': 1, 'controller': 7, 'node': 'gain',
             'property': 'opacity', 'minimum': 0, 'maximum': 1}]


class NativePort:
    def __init__(self, owner, **kwargs):
        self.owner = owner
        self.kwargs = kwargs
        self.queue = deque()
        self.opened = False
        self.deleted = False
        self.calls = []
        self.error_callback = None
        self.ports = list(owner.ports)
        self.fail = owner.fail

    def _call(self, name):
        assert not self.deleted, 'A native method was used after delete'
        self.calls.append(name)
        if name == self.fail:
            raise OSError('Injected native MIDI boundary failure')

    def get_current_api(self):
        self._call('api')
        return self.owner.api

    def get_ports(self):
        self._call('ports')
        return self.ports

    def get_port_name(self, index):
        self._call('name')
        return self.ports[index]

    def ignore_types(self, **kwargs):
        self._call('ignore')
        assert kwargs == {'sysex': True, 'timing': True, 'active_sense': True}

    def set_error_callback(self, callback):
        self._call('callback')
        self.error_callback = callback

    def cancel_error_callback(self):
        self._call('cancel')
        self.error_callback = None

    def open_port(self, index, name):
        self._call('open')
        assert self.ports[index] == 'Exact MIDI input' and name == 'FrameRudiments controls'
        self.opened = True

    def is_port_open(self):
        self._call('is_open')
        return self.opened

    def get_message(self):
        self._call('get')
        return (self.queue.popleft(), 99999.) if self.queue else None

    def close_port(self):
        self._call('close')
        self.opened = False

    def delete(self):
        self._call('delete')
        self.deleted = True


class Backend:
    def __init__(self):
        self.ports = ['Exact MIDI input']
        self.api = 7
        self.fail = None
        self.instances = []

    def MidiIn(self, **kwargs):
        port = NativePort(self, **kwargs)
        self.instances.append(port)
        return port


@pytest.fixture
def backend(monkeypatch):
    fake = Backend()
    monkeypatch.setattr(protocol, '_module', lambda api: (fake, 7))
    monkeypatch.setattr(protocol, 'backend_version', lambda: 'injected-test-port')
    return fake


def capture_take(source, backend):
    capture = MIDIControlCapture(source, BINDINGS, api='jack', port_name='Exact MIDI input', lease_frames=18)
    assert not backend.instances
    with capture:
        backend.instances[-1].queue.append([0xb0, 7, 32])
        capture.sample(0)
        backend.instances[-1].queue.append([0xb0, 7, 96])
        capture.sample(30)
        capture.sample(source.frame_count - 1)
    assert backend.instances[-1].deleted and capture.receiver.closed
    return capture.freeze(), capture


def attached(source, take, factor=1):
    reference = take.write(source.base / f'midi-take-{factor}.json')
    data = deepcopy(source.data)
    data['midi_control_take'] = reference
    data['render_quality']['temporal_samples'] = factor
    return Project(data, source.base)


@pytest.mark.parametrize(('message', 'expected'), [
    ([0xb0, 7, 0], ('/midi/cc/1/7', 0)),
    ([0xbf, 119, 127], ('/midi/cc/16/119', 127)),
    ([0xe0, 0, 64], ('/midi/pitchbend/1', 8192)),
    ([0xef, 127, 127], ('/midi/pitchbend/16', 16383)),
    ([0xe0, 0, 0], ('/midi/pitchbend/1', 0)),
])
def test_independent_wire_vectors(message, expected):
    assert decode_message(message) == expected


@pytest.mark.parametrize('message', [[], [0xb0, 7], [0xb0, 7, 32, 1], [0x90, 60, 127],
    [0xb0, 120, 0], [0xb0, 127, 0], [0xf0, 7, 32], [0xf8], [0xe0, 128, 0],
    [0xb0, 7, 256], [0xb0, True, 0], [0xb0, 7, -1], b'\xb0\x07\x20'])
def test_unsupported_or_malformed_wire_rejected(message):
    with pytest.raises(ValueError):
        decode_message(message)


@pytest.mark.parametrize('patch', [
    {'message': 'note'}, {'channel': 0}, {'channel': 17}, {'channel': True},
    {'controller': 120}, {'controller': -1}, {'minimum': -1}, {'maximum': 2},
    {'minimum': float('nan')}, {'maximum': float('inf')}, {'property': 'missing'},
    {'node': 'missing'}, {'extra': 1}, {'message': 'pitchbend'},
])
def test_invalid_binding_fails_before_native_allocation(source, backend, patch):
    row = {**BINDINGS[0], **patch}
    with pytest.raises(ValueError):
        MIDIControlCapture(source, [row], api='jack', port_name='Exact MIDI input')
    assert not backend.instances


@pytest.mark.parametrize(('api', 'name'), [('auto', 'Exact MIDI input'), ('jack', ''),
    ('jack', None), ('jack', 'x'*257), ('jack', 'bad\0name')])
def test_explicit_api_and_name_required(source, backend, api, name):
    with pytest.raises(ValueError):
        MIDIControlCapture(source, BINDINGS, api=api, port_name=name)
    assert not backend.instances


def test_explicit_mapping_endpoints_and_integer_rounding(source):
    rows = bindings_for(source, EffectGraph(source.effect_graph_spec), BINDINGS)
    assert rows[0].map_raw(0) == 0 and rows[0].map_raw(127) == 1
    reversed_row = {**BINDINGS[0], 'minimum': 1, 'maximum': 0}
    reverse = bindings_for(source, EffectGraph(source.effect_graph_spec), [reversed_row])[0]
    assert reverse.map_raw(0) == 1 and reverse.map_raw(127) == 0
    integer_binding = MIDIBinding(Binding('/midi/cc/1/1', 'n', 'p', 'integer', -10, 10),
                                  'cc', 1, 1, -10, 10)
    assert [integer_binding.map_raw(raw) for raw in (0, 63, 64, 127)] == [-10, 0, 0, 10]
    with pytest.raises(ValueError):
        integer_binding.map_raw(128)


def test_duplicate_and_native_target_conflicts(source, backend):
    with pytest.raises(ValueError):
        MIDIControlCapture(source, BINDINGS * 2, api='jack', port_name='Exact MIDI input')
    source.data['effect_graph_controls'] = [{'node': 'gain', 'property': 'opacity'}]
    with pytest.raises(ValueError):
        MIDIControlCapture(source, BINDINGS, api='jack', port_name='Exact MIDI input')
    assert not backend.instances


@pytest.mark.parametrize('ports', [[], ['Almost exact MIDI input'], ['Exact MIDI input'] * 2])
def test_exact_unambiguous_name_and_cleanup(source, backend, ports):
    backend.ports = ports
    capture = MIDIControlCapture(source, BINDINGS, api='jack', port_name='Exact MIDI input')
    with pytest.raises(ValueError):
        with capture:
            pytest.fail('An absent or ambiguous input opened')
    assert capture.receiver.closed and backend.instances[-1].deleted
    assert backend.instances[-1].calls.count('delete') == 1


@pytest.mark.parametrize('failure', ['ports', 'ignore', 'callback', 'open', 'name', 'is_open'])
def test_native_open_failure_always_deletes(source, backend, failure):
    backend.fail = failure
    capture = MIDIControlCapture(source, BINDINGS, api='jack', port_name='Exact MIDI input')
    with pytest.raises(OSError):
        with capture:
            pass
    assert backend.instances[-1].deleted and capture.receiver.native is None
    assert backend.instances[-1].calls.count('delete') == 1


def test_no_backend_fallback_and_owned_inspection(backend):
    backend.api = 99
    with pytest.raises(RuntimeError, match='differs'):
        with MIDIInput('jack', 'Exact MIDI input'):
            pass
    assert backend.instances[-1].deleted
    backend.api = 7
    assert input_ports('jack')['connected'] is False
    assert backend.instances[-1].deleted and 'open' not in backend.instances[-1].calls


def test_bounded_queue_last_message_and_source_clock(source, backend):
    with MIDIControlCapture(source, BINDINGS, api='jack', port_name='Exact MIDI input', lease_frames=18) as capture:
        port = backend.instances[-1]
        assert port.kwargs['queue_size_limit'] == protocol.QUEUE_LIMIT
        port.queue.extend(([0xb0, 7, 1], [0xb0, 8, 127], [0x90, 60, 127], [0xb0, 7, 64]))
        assert capture.wait_for_input(0)
        capture.sample(0)
        capture.sample(59)
    take = capture.freeze()
    event = take.document()['events'][0]
    assert event['raw'] == 64 and event['value'] == 64/127
    assert event['source_seconds'] == source.source_time(0)
    assert event['beat'] == source.continuous_beat(0)
    stats = take.evidence['capture']['statistics']
    assert stats['rejected_message'] == stats['unbound_control'] == 1
    assert take.evidence['capture']['arrival_delta_used_as_clock'] is False
    capture.close()
    assert port.calls.count('delete') == 1
    with pytest.raises(RuntimeError):
        capture.receiver.__enter__()


def test_pitchbend_mapping_and_channel_filter(source, backend):
    row = {key: value for key, value in BINDINGS[0].items() if key != 'controller'}
    row['message'], row['channel'] = 'pitchbend', 16
    with MIDIControlCapture(source, [row], api='jack', port_name='Exact MIDI input') as capture:
        backend.instances[-1].queue.extend(([0xe0, 0, 64], [0xef, 127, 127]))
        capture.sample(0)
        capture.sample(59)
    assert capture.freeze().values_at(0) == {('gain', 'opacity'): 1.0}
    assert capture._statistics['unbound_control'] == 1


@pytest.mark.parametrize('failure', ['overflow', 'native_error', 'native_read', 'exception'])
def test_capture_failure_cannot_publish_partial_take(source, backend, failure):
    capture = MIDIControlCapture(source, BINDINGS, api='jack', port_name='Exact MIDI input')
    with pytest.raises((RuntimeError, OSError)):
        with capture:
            port = backend.instances[-1]
            if failure == 'overflow':
                port.queue.extend([[0xb0, 7, 32]] * 65)
            elif failure == 'native_error':
                port.error_callback(1, 'queue overflow', None)
            elif failure == 'native_read':
                port.fail = 'get'
            else:
                raise RuntimeError('Caller failure')
            capture.sample(0)
    assert not capture._history and capture.receiver.closed and port.deleted
    with pytest.raises(ValueError):
        capture.freeze()


def test_event_budget_is_atomic(source, backend, monkeypatch):
    import framerudiments.midi_controls_m15 as module
    monkeypatch.setattr(module, 'MAX_EVENTS', 1)
    capture = MIDIControlCapture(source, BINDINGS, api='jack', port_name='Exact MIDI input')
    with pytest.raises(RuntimeError, match='budget'):
        with capture:
            port = backend.instances[-1]
            port.queue.append([0xb0, 7, 32])
            capture.sample(0)
            port.queue.append([0xb0, 7, 64])
            capture.sample(30)
    assert len(capture._history) == 1 and capture.receiver.closed
    with pytest.raises(ValueError):
        capture.freeze()


def test_freeze_requires_complete_closed_healthy_input(source, backend):
    capture = MIDIControlCapture(source, BINDINGS, api='jack', port_name='Exact MIDI input')
    with capture:
        backend.instances[-1].queue.append([0xb0, 7, 32])
        capture.sample(0)
        with pytest.raises(ValueError):
            capture.freeze()
    with pytest.raises(ValueError):
        capture.freeze()


@pytest.mark.parametrize('factor', [1, 4, 8])
def test_native_random_seek_repeat_subframes_and_no_device_or_network(source, backend, monkeypatch, factor):
    take, _ = capture_take(source, backend)
    project = attached(source, take, factor)
    from framerudiments.midi_control_take_m15 import RecordedMIDIControls
    observed_values = []
    original = RecordedMIDIControls.values

    def observed(owner, context, graph):
        result = original(owner, context, graph)
        observed_values.append((context.frame // owner.factor, result.copy()))
        return result

    imported = builtins.__import__

    def forbidden_device(name, *args, **kwargs):
        if name == 'rtmidi' or name.startswith('rtmidi.'):
            pytest.fail('Replay imported a native MIDI library')
        return imported(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', forbidden_device)
    monkeypatch.setattr(socket, 'socket', lambda *_a, **_kw: pytest.fail('Replay opened a socket'))
    monkeypatch.setattr(RecordedMIDIControls, 'values', observed)
    base_data = deepcopy(source.data)
    base_data['render_quality']['temporal_samples'] = factor
    with_compositors = [Compositor(project), Compositor(Project(base_data, source.base))]
    controlled, baseline = with_compositors
    try:
        first, context = controlled.frame(0)
        controlled.frame(30)
        assert controlled.frame(0)[0].tobytes() == first.tobytes()
        assert context.source_seconds == source.source_time(0)
        assert context.beat == source.continuous_beat(0)
        for frame, values in observed_values:
            assert values[('gain', 'opacity')] == (32/127 if frame < 19 else 96/127)
        evidence = controlled.evidence()['midi_controls']
        assert evidence['native_subframes_per_encoded_frame'] == factor
        assert evidence['replay_device_activity'] is False
        assert evidence['last_used']['encoded_frame'] == 0
        for frame in (19, 29, 49, 59):
            assert controlled.frame(frame)[0].tobytes() == baseline.frame(frame)[0].tobytes()
        assert controlled.frame(0)[0].tobytes() != controlled.frame(1)[0].tobytes()
        assert isinstance(first, Image.Image)
    finally:
        for compositor in with_compositors:
            compositor.close()


@pytest.mark.parametrize('edit', ['raw', 'value', 'source_seconds', 'beat', 'source', 'closed', 'last_frame'])
def test_take_rejects_changed_mapping_clock_source_or_incomplete_capture(source, backend, edit):
    take, _ = capture_take(source, backend)
    doc = take.document()
    if edit in ('raw', 'value', 'source_seconds', 'beat'):
        doc['events'][0][edit] += .1 if edit != 'raw' else 1
    elif edit == 'source':
        doc['source']['fps'] = 30
    elif edit == 'closed':
        doc['capture']['input_closed'] = False
    else:
        doc['capture']['last_frame'] -= 1
    with pytest.raises(ValueError):
        MIDIControlTake(doc, source, EffectGraph(source.effect_graph_spec))


def test_pinned_bytes_atomic_write_and_project_input_ledger(source, backend):
    take, _ = capture_take(source, backend)
    project = attached(source, take)
    pin = project.midi_control_take_ref
    assert pin in project.inputs and project.summary()['midi_control_take'] == pin
    with pytest.raises(FileExistsError):
        take.write(pin['path'])
    raw = ('  '+json.dumps(take.document(), indent=3)+'\n').encode()
    path = source.base/'spaced-midi-take.json'
    path.write_bytes(raw)
    loaded = MIDIControlTake.load({'path': str(path), 'sha256': sha256(raw).hexdigest()}, source,
                                   EffectGraph(source.effect_graph_spec))
    copy = source.base/'unchanged-midi-take.json'
    loaded.write(copy)
    assert loaded.sha256 == sha256(raw).hexdigest() and copy.read_bytes() == raw
    path.write_bytes(raw+b' ')
    with pytest.raises(ValueError):
        MIDIControlTake.load({'path': str(path), 'sha256': loaded.sha256}, source,
                              EffectGraph(source.effect_graph_spec))


def test_osc_midi_take_conflict_and_cli_validate(source, backend, capsys, monkeypatch):
    take, _ = capture_take(source, backend)
    project = attached(source, take)
    data = deepcopy(project.data)
    data['osc_control_take'] = data['midi_control_take']
    with pytest.raises(ValueError, match='one finite'):
        Project(data, source.base)
    path = source.base/'project.json'
    path.write_text(json.dumps(project.data))
    from framerudiments.midi_m15_cli import main
    monkeypatch.setattr(protocol, 'import_module', lambda *_a: pytest.fail('Validation loaded a native MIDI backend'))
    assert main(['validate', str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'validated' and result['replay_device_activity'] is False
    assert Path(project.midi_control_take_ref['path']).exists()


def test_metadata_inspection_never_imports_native(monkeypatch):
    monkeypatch.setattr(protocol, 'import_module', lambda *_a: pytest.fail('Metadata inspection imported native MIDI'))
    result = protocol.inspect()
    assert result['native_library_loaded'] is False and result['ports_enumerated'] is False


def test_unqualified_native_python_fails_before_import(monkeypatch):
    monkeypatch.setattr(protocol.sys, 'version_info', (3, 13, 0))
    monkeypatch.setattr(protocol, 'import_module', lambda *_a: pytest.fail('Unsupported host loaded native MIDI'))
    with pytest.raises(RuntimeError, match='qualified Python'):
        protocol._module('jack')


def test_native_integer_colour_target_and_endpoint_types(source, backend):
    data = deepcopy(source.data)
    data['effect_graph'] = {'nodes': [
        {'id': 'a', 'type': 'source'},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['a'], 'key_color': '#00ff00'},
    ], 'output': 'key'}
    project = Project(data, source.base)
    row = {**BINDINGS[0], 'node': 'key', 'property': 'key_color.r', 'minimum': 0, 'maximum': 255}
    admitted = bindings_for(project, EffectGraph(project.effect_graph_spec), [row])[0]
    assert [admitted.map_raw(raw) for raw in (0, 64, 127)] == [0, 129, 255]
    assert type(admitted.map_raw(64)) is int
    with pytest.raises(ValueError, match='endpoints'):
        bindings_for(project, EffectGraph(project.effect_graph_spec), [{**row, 'maximum': 255.0}])


def test_readiness_is_bounded_and_does_not_supply_media_time(source, backend):
    with MIDIControlCapture(source, BINDINGS, api='jack', port_name='Exact MIDI input') as capture:
        port = backend.instances[-1]
        port.queue.extend(([0xb0, 7, 32], [0xb0, 7, 64]))
        assert capture.wait_for_input(0, minimum_messages=2)
        assert len(capture.receiver._pending) == 2 and capture._last_frame == -1
        capture.sample(0)
        capture.sample(59)
    assert capture.freeze().document()['events'][0]['raw'] == 64


def test_unqualified_native_version_fails_before_import(monkeypatch):
    monkeypatch.setattr(protocol.sys, 'version_info', (3, 12, 0))
    monkeypatch.setattr(protocol, 'backend_version', lambda: '1.4.0')
    monkeypatch.setattr(protocol, 'import_module', lambda *_a: pytest.fail('Unqualified backend imported'))
    with pytest.raises(RuntimeError, match='qualified python-rtmidi'):
        protocol._module('jack')


def test_missing_optional_native_extra_keeps_replay_available(monkeypatch):
    monkeypatch.setattr(protocol.sys, 'version_info', (3, 12, 0))
    def missing():
        raise protocol.metadata.PackageNotFoundError('python-rtmidi')
    monkeypatch.setattr(protocol, 'backend_version', missing)
    with pytest.raises(RuntimeError, match='optional'):
        protocol._module('jack')
    assert protocol.inspect()['native_library_loaded'] is False
