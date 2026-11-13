"""Optional OpenGL driver memory hints, separate from the RGBA8 budget."""
from __future__ import annotations

import ctypes
import sys

_NVX = 'GL_NVX_gpu_memory_info'
_ATI = 'GL_ATI_meminfo'


def is_software_renderer(renderer):
    return any(name in renderer.lower() for name in (
        'llvmpipe', 'softpipe', 'swiftshader', 'gdi generic', 'microsoft basic render driver',
    ))


def _current_context(library, name):
    current = getattr(library, name)
    current.argtypes = ()
    current.restype = ctypes.c_void_p
    return current


def _bind_integer_reader():
    """Return a current-context getter and glGetIntegerv for its GL dispatcher."""
    if sys.platform == 'win32':
        gl = ctypes.WinDLL('opengl32.dll', winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        current = _current_context(gl, 'wglGetCurrentContext')
        get = gl.glGetIntegerv
    elif sys.platform.startswith('linux'):
        try:
            egl = ctypes.CDLL('libEGL.so.1')
            current = _current_context(egl, 'eglGetCurrentContext')
            if current():
                address = egl.eglGetProcAddress
                address.argtypes = (ctypes.c_char_p,)
                address.restype = ctypes.c_void_p
                pointer = address(b'glGetIntegerv')
                if not pointer:
                    return None
                get = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))(pointer)
                return current, get
        except (OSError, AttributeError):
            pass
        gl = ctypes.CDLL('libGL.so.1')
        current = _current_context(gl, 'glXGetCurrentContext')
        get = gl.glGetIntegerv
    else:
        return None
    get.argtypes = (ctypes.c_uint, ctypes.POINTER(ctypes.c_int))
    get.restype = None
    return current, get


class DriverMemoryProbe:
    """Read only advertised queries and only with the original context current."""

    def __init__(self, extensions, *, owned, software=False, bind=_bind_integer_reader):
        self.source = next((name for name in (_NVX, _ATI) if name in extensions), None)
        self._binding = None
        self._context = None
        if software:
            self._reason = 'software_renderer'
        elif not self.source:
            self._reason = 'extension_unavailable'
        elif not owned:
            self._reason = 'context_borrowed'
        else:
            try:
                self._binding = bind()
            except (OSError, AttributeError):
                pass
            if self._binding is None:
                self._reason = 'native_query_unavailable'
            else:
                self._context = self._binding[0]()
                self._reason = None if self._context else 'context_not_current'

    def _read(self, enum, count=1):
        values = (ctypes.c_int * count)(*([-1] * count))
        self._binding[1](enum, values)
        return tuple(values)

    def sample(self, *, closed=False):
        result = {'schema': 'framerudiments.gl-driver-memory.v1', 'extension': self.source,
                  'state': 'unavailable', 'reason': 'backend_closed' if closed else self._reason}
        if result['reason']:
            return result
        if self._binding[0]() != self._context:
            result['reason'] = 'context_not_current'
            return result

        if self.source == _NVX:
            # GL_NVX_gpu_memory_info: sizes in KiB, eviction count in events.
            dedicated, total, free, evictions, evicted = (
                self._read(enum)[0] for enum in range(0x9047, 0x904C))
            if dedicated <= 0 or min(total, free) < 0:
                result['reason'] = 'driver_values_unavailable'
                return result
            values = {'scope': 'device', 'dedicated_bytes': dedicated * 1024,
                      'total_available_bytes': total * 1024, 'available_bytes': free * 1024}
            if evictions >= 0:
                values['eviction_count'] = evictions
            if evicted >= 0:
                values['evicted_bytes'] = evicted * 1024
        else:
            # GL_TEXTURE_FREE_MEMORY_ATI: free and largest block in the
            # texture pool, followed by the same hints for the auxiliary pool.
            free, largest, aux, aux_largest = self._read(0x87FC, 4)
            if min(free, largest, aux, aux_largest) < 0:
                result['reason'] = 'driver_values_unavailable'
                return result
            values = {'scope': 'texture_pool', 'available_bytes': free * 1024,
                      'largest_block_bytes': largest * 1024, 'aux_available_bytes': aux * 1024,
                      'aux_largest_block_bytes': aux_largest * 1024}
        result.update(values, state='available', reason=None)
        return result
