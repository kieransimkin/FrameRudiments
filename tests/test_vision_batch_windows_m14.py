"""M14.3 Windows peak-RSS regression: correctly typed 64-bit PSAPI handles."""
import ctypes
from types import SimpleNamespace


from framerudiments.vision_batch_m14 import _windows_peak_rss_bytes


class WinApiCall:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


def test_windows_64bit_pseudohandle_uses_void_pointer_not_32bit_int(monkeypatch):
    import ctypes as windows

    handle = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
    get_current = WinApiCall(lambda: handle)
    seen = []

    def fake_memory_info(received, pointer, size):
        seen.append(received)
        assert info.argtypes[0] == ctypes.c_void_p
        assert info.argtypes[2] == ctypes.c_ulong
        assert info.restype == ctypes.c_int
        assert get_current.restype == ctypes.c_void_p
        assert received == handle
        counters_type = info.argtypes[1]._type_
        record = ctypes.cast(pointer, ctypes.POINTER(counters_type)).contents
        assert size == ctypes.sizeof(counters_type)
        record.PeakWorkingSetSize = 1_234_567_890
        return 1

    info = WinApiCall(fake_memory_info)
    windll = SimpleNamespace(kernel32=SimpleNamespace(GetCurrentProcess=get_current),
                             psapi=SimpleNamespace(GetProcessMemoryInfo=info))
    monkeypatch.setattr(windows, "windll", windll, raising=False)
    assert _windows_peak_rss_bytes() == 1_234_567_890
    assert seen == [handle]


def test_winapi_failure_is_unavailable_metric_not_inference_failure(monkeypatch):
    current = WinApiCall(lambda: 1234)
    unavailable = WinApiCall(lambda *_args: 0)
    fake = SimpleNamespace(kernel32=SimpleNamespace(GetCurrentProcess=current),
                           psapi=SimpleNamespace(GetProcessMemoryInfo=unavailable))
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)
    assert _windows_peak_rss_bytes() is None


def test_winapi_argument_error_is_unavailable_metric(monkeypatch):
    current = WinApiCall(lambda: 1234)

    def bad(*_args):
        raise ctypes.ArgumentError("unavailable process memory counters")

    fake = SimpleNamespace(kernel32=SimpleNamespace(GetCurrentProcess=current),
                           psapi=SimpleNamespace(GetProcessMemoryInfo=WinApiCall(bad)))
    monkeypatch.setattr(ctypes, "windll", fake, raising=False)
    assert _windows_peak_rss_bytes() is None
