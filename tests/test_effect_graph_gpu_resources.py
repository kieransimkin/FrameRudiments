"""GPU per-node memory/dimension safety checks without requiring drivers."""
import pytest
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported


class Context:
    info = {'GL_MAX_TEXTURE_SIZE':8192}


def test_gpu_working_set_limit():
    backend = object.__new__(GLMatteBackend)
    backend.ctx = Context()
    backend.max_working_set_bytes = 100 * 1024**2
    backend._check_budget((1920,1080),3)
    with pytest.raises(GPUUnsupported,match='working-set'):
        backend._check_budget((3840,2160),4)


def test_gpu_device_max_texture_size_limit():
    backend = object.__new__(GLMatteBackend)
    backend.ctx = Context()
    backend.max_working_set_bytes = 20 * 1024**3
    with pytest.raises(GPUUnsupported,match='texture limit'):
        backend._check_budget((9000,4000),2)
