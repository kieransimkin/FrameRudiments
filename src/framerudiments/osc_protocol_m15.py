"""Bounded OSC 1.0 numeric messages and an explicitly owned loopback socket.

Protocol reference: Matt Wright, https://opensoundcontrol.stanford.edu/spec-1_0.html .
This subset has no bundles, timetags, address patterns, clock or worker thread.
"""
import ipaddress
import math
import re
import select
import socket
import struct

MAX_PACKET_BYTES = 1024
MAX_POLL_PACKETS = 64
MAX_SOCKET_BUFFER = 65536
_ADDRESS = re.compile(r'/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z')


def literal_address(value):
    if not isinstance(value, str) or len(value) > 128 or not _ADDRESS.fullmatch(value):
        raise ValueError('OSC address must be a bounded literal ASCII path')
    return value


def _string(packet, offset):
    end = packet.find(b'\0', offset)
    if end < 0:
        raise ValueError('OSC string is unterminated')
    following = (end + 4) & ~3
    if following > len(packet) or any(packet[end:following]):
        raise ValueError('OSC string padding is invalid')
    try:
        return packet[offset:end].decode('ascii'), following
    except UnicodeDecodeError as exc:
        raise ValueError('OSC strings must be ASCII') from exc


def decode_message(packet):
    if not isinstance(packet, bytes) or not 12 <= len(packet) <= MAX_PACKET_BYTES or len(packet) % 4:
        raise ValueError('OSC packet size is invalid')
    address, offset = _string(packet, 0)
    literal_address(address)
    tag, offset = _string(packet, offset)
    if tag not in (',i', ',f') or offset + 4 != len(packet):
        raise ValueError('OSC supports exactly one int32 or float32 argument')
    value, = struct.unpack('>i' if tag == ',i' else '>f', packet[offset:])
    if not math.isfinite(value):
        raise ValueError('OSC numeric value must be finite')
    return address, value


def endpoint(value, *, ephemeral=False):
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError('OSC endpoint needs an explicit host and port')
    host, port = value
    try:
        address = ipaddress.ip_address(host) if isinstance(host, str) else None
    except ValueError:
        address = None
    if not isinstance(address, ipaddress.IPv4Address) or not address.is_loopback:
        raise ValueError('OSC requires a literal IPv4 loopback endpoint')
    if type(port) is not int or not (0 if ephemeral else 1) <= port <= 65535:
        raise ValueError('OSC endpoint port is invalid')
    return str(address), port


class LoopbackReceiver:
    """No socket is opened until enter; exit closes the exact owned descriptor."""
    def __init__(self, bind, peer):
        self.bind, self.peer = endpoint(bind, ephemeral=True), endpoint(peer)
        self.socket = None
        self.bound = None
        self.started = self.closed = False

    def __enter__(self):
        if self.started:
            raise RuntimeError('OSC receiver cannot be reopened')
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                receiver.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16384)
            if receiver.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) > MAX_SOCKET_BUFFER:
                raise RuntimeError('OSC receive buffer exceeds its declared bound')
            receiver.bind(self.bind)
            receiver.setblocking(False)
            self.bound = receiver.getsockname()
        except BaseException:
            receiver.close()
            raise
        self.socket, self.started = receiver, True
        return self

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if self.started:
            self.closed = True

    def __exit__(self, *_exc):
        self.close()

    def wait_ready(self, timeout=1):
        if self.socket is None:
            raise RuntimeError('OSC receiver is not open')
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 <= timeout <= 2:
            raise ValueError('OSC I/O wait must be between zero and two seconds')
        return bool(select.select([self.socket], [], [], timeout)[0])

    def poll(self):
        if self.socket is None:
            raise RuntimeError('OSC receiver is not open')
        messages, rejected_peer, rejected_message = [], 0, 0
        for _ in range(MAX_POLL_PACKETS):
            try:
                # Full UDP payload read avoids Windows WSAEMSGSIZE truncation.
                packet, peer = self.socket.recvfrom(65535)
            except BlockingIOError:
                break
            if peer != self.peer:
                rejected_peer += 1
                continue
            try:
                messages.append(decode_message(packet))
            except ValueError:
                rejected_message += 1
        if select.select([self.socket], [], [], 0)[0]:
            self.close()
            raise RuntimeError('OSC packet budget exceeded; stopped before partial publication')
        return messages, rejected_peer, rejected_message
