"""Explicit optional RtMidi input with bounded queues and deterministic ownership."""
from collections import deque
from importlib import import_module, metadata
import sys
from threading import Event
from time import monotonic, sleep

from .io import integer, number
from .midi_control_take_m15 import control_key

MAX_POLL_MESSAGES = 64
QUEUE_LIMIT = MAX_POLL_MESSAGES + 2
APIS = {'alsa': 'API_LINUX_ALSA', 'jack': 'API_UNIX_JACK',
        'coremidi': 'API_MACOSX_CORE', 'windows-mm': 'API_WINDOWS_MM'}


def selection(api, port_name=None):
    if not isinstance(api, str) or api not in APIS:
        raise ValueError('MIDI API must be explicitly selected: ' + ', '.join(APIS))
    if port_name is not None:
        if (not isinstance(port_name, str) or not 1 <= len(port_name) <= 256 or
                any(ord(c) < 32 or 0xd800 <= ord(c) <= 0xdfff for c in port_name)):
            raise ValueError('MIDI needs an exact bounded input port name')
    return api, port_name


def decode_message(message):
    # RtMidi supplies complete messages, so no competing running-status parser,
    # SysEx buffer, MIDI clock, transport or timestamp clock is introduced.
    if (not isinstance(message, (list, tuple)) or len(message) != 3 or
            any(type(b) is not int or not 0 <= b <= 255 for b in message) or
            any(b > 127 for b in message[1:])):
        raise ValueError('MIDI numeric control needs a complete three-byte message')
    status, first, second = message
    channel = (status & 15) + 1
    if status >> 4 == 0xb and first < 120:
        return control_key('cc', channel, first), second
    if status >> 4 == 0xe:
        return control_key('pitchbend', channel), first | second << 7
    raise ValueError('Unsupported MIDI numeric control message')


def backend_version():
    return metadata.version('python-rtmidi')


def _module(api):
    if sys.version_info >= (3, 13):
        raise RuntimeError('MIDI capture requires a qualified Python 3.11/3.12 host; replay supports 3.13+')
    try:
        version = backend_version()
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError('Install the optional framerudiments[midi] extra to capture MIDI') from exc
    if version != '1.5.8':
        raise RuntimeError('Native MIDI capture requires qualified python-rtmidi 1.5.8')
    try:
        module = import_module('rtmidi')
    except ImportError as exc:
        raise RuntimeError('Install the optional framerudiments[midi] extra to capture MIDI') from exc
    requested = getattr(module, APIS[api], None)
    if requested is None or requested not in module.get_compiled_api():
        raise RuntimeError('Requested MIDI API was not compiled; no fallback is allowed')
    return module, requested


def inspect():
    # Metadata-only inspection must stay safe even when an unrelated native
    # extension was built against an incompatible interpreter. Never load it.
    try:
        version = backend_version()
    except metadata.PackageNotFoundError:
        version = None
    supported = (3, 11) <= sys.version_info < (3, 13)
    return {'python_rtmidi': version, 'qualified_capture_python': '3.11 or 3.12',
            'capture_host_supported': supported, 'native_library_loaded': False,
            'ports_enumerated': False,
            'note': 'Explicit ports command probes the selected compiled API; no automatic source build'}


def input_ports(api):
    selection(api)
    module, requested = _module(api)
    owned = module.MidiIn(rtapi=requested, name='FrameRudiments MIDI inspection', queue_size_limit=QUEUE_LIMIT)
    try:
        if owned.get_current_api() != requested:
            raise RuntimeError('Native MIDI backend differs from explicit selection')
        ports = owned.get_ports()
        return {'api': api, 'input_names': ports, 'connected': False}
    finally:
        try:
            owned.close_port()
        finally:
            owned.delete()


class MIDIInput:
    def __init__(self, api, port_name):
        selection(api, port_name)
        if port_name is None:
            raise ValueError('MIDI capture requires an exact input port name')
        self.api, self.port_name = api, port_name
        self.native = None
        self.closed = False
        self._used = False
        self._pending = deque()
        self._error = Event()
        self.error_count = 0
        self.first_error = None
        self.backend_version = None

    def _on_error(self, error_type, message, _data):
        # This callback is owned by the native backend. It does not publish or
        # advance time and never raises through a native callback boundary.
        self.error_count += 1
        if self.first_error is None:
            self.first_error = {'type': int(error_type), 'message': str(message)[:256]}
        self._error.set()

    def _healthy(self):
        if self.native is None or self.closed:
            raise RuntimeError('MIDI input is closed')
        if self._error.is_set():
            self.close()
            raise RuntimeError('Native MIDI input error or queue overflow; capture stopped')

    def __enter__(self):
        if self._used:
            raise RuntimeError('A MIDI input can only be entered once')
        self._used = True
        module, requested = _module(self.api)
        self.backend_version = backend_version()
        try:
            self.native = module.MidiIn(rtapi=requested, name='FrameRudiments MIDI capture',
                                        queue_size_limit=QUEUE_LIMIT)
            if self.native.get_current_api() != requested:
                raise RuntimeError('Native MIDI backend differs from explicit selection')
            # Discovery is explicit and delayed until all project bindings pass.
            names = self.native.get_ports()
            matches = [i for i, name in enumerate(names) if name == self.port_name]
            if len(matches) != 1:
                raise ValueError('MIDI input name is absent or ambiguous; select its exact current name')
            self.native.ignore_types(sysex=True, timing=True, active_sense=True)
            self.native.set_error_callback(self._on_error)
            self.native.open_port(matches[0], 'FrameRudiments controls')
            # Fence ordinary enumeration reordering where the API exposes names.
            if (self.native.get_port_name(matches[0]) != self.port_name or
                    not self.native.is_port_open()):
                raise RuntimeError('Selected MIDI input changed while connecting')
            self._healthy()
            return self
        except BaseException:
            self.close()
            raise

    def _get(self):
        self._healthy()
        try:
            result = self.native.get_message()
        except BaseException:
            self.close()
            raise
        self._healthy()
        return result

    def poll(self):
        self._healthy()
        batch = []
        for _ in range(MAX_POLL_MESSAGES + 1):
            item = self._pending.popleft() if self._pending else self._get()
            if item is None:
                return batch
            if len(batch) == MAX_POLL_MESSAGES:
                self.close()
                raise RuntimeError('MIDI messages per source frame exceeded; capture stopped')
            # Native arrival delta is deliberately discarded. Only the existing
            # media owner assigns a source frame, time and continuous beat.
            batch.append(item[0])
        raise AssertionError('Bounded MIDI poll did not terminate')

    def wait_ready(self, timeout=1, *, minimum_messages=1):
        timeout = number(timeout, 'MIDI input wait', minimum=0, maximum=2)
        integer(minimum_messages, 'MIDI minimum pending messages', minimum=1, maximum=MAX_POLL_MESSAGES)
        deadline = monotonic() + timeout
        self._healthy()
        while len(self._pending) < minimum_messages:
            item = self._get()
            if item is not None:
                self._pending.append(item)
                continue
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            sleep(min(.005, remaining))
        return True

    def close(self):
        owned, self.native = self.native, None
        self._pending = deque()
        if owned is None:
            return
        # Exactly one delete, with no native method calls afterwards. delete is
        # required to release JACK/CoreMIDI virtual-port client ownership.
        try:
            try:
                owned.close_port()
            finally:
                owned.cancel_error_callback()
        finally:
            owned.delete()
        self.closed = True

    def __exit__(self, *_exc):
        self.close()

    @property
    def evidence(self):
        return {'api': self.api, 'input_name': self.port_name, 'python_rtmidi': self.backend_version,
                'queue_size_limit': QUEUE_LIMIT, 'max_poll_messages': MAX_POLL_MESSAGES,
                'native_errors': self.error_count, 'first_native_error': self.first_error,
                'input_closed': self.closed and self.native is None,
                'arrival_delta_used_as_clock': False}
