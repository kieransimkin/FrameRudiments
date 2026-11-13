"""GPU per-node memory/dimension safety checks without requiring drivers."""
import pytest
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
from framerudiments.effect_graph_gpu_memory import DriverMemoryProbe


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


@pytest.mark.parametrize('extension,owned,reason', [
    (None, True, 'extension_unavailable'),
    ('GL_NVX_gpu_memory_info', False, 'context_borrowed'),
])
def test_unavailable_memory_query_does_not_touch_native_gl(extension, owned, reason):
    def bind():
        pytest.fail('No native binding should be attempted')

    probe = DriverMemoryProbe({extension} if extension else (), owned=owned, bind=bind)
    assert probe.sample()['reason'] == reason
    assert probe.sample(closed=True)['reason'] == 'backend_closed'


def test_nvx_query_uses_original_context_and_preserves_units():
    context = [3]
    rows = dict(zip(range(0x9047, 0x904C), (8192, 7680, 5120, 17, 60)))
    calls = []

    def get(enum, values):
        calls.append(enum)
        values[0] = rows[enum]

    probe = DriverMemoryProbe({'GL_NVX_gpu_memory_info'}, owned=True, bind=lambda: (lambda: context[0], get))
    context[0] = 4
    assert probe.sample()['reason'] == 'context_not_current'
    assert not calls
    context[0] = 3
    sample = probe.sample()
    assert sample['state'] == 'available' and sample['scope'] == 'device'
    assert sample['dedicated_bytes'] == 8192 * 1024
    assert sample['total_available_bytes'] == 7680 * 1024
    assert sample['available_bytes'] == 5120 * 1024
    assert sample['eviction_count'] == 17 and sample['evicted_bytes'] == 60 * 1024
    rows[0x9049] = -1
    assert probe.sample()['reason'] == 'driver_values_unavailable'


def test_ati_pool_hints_do_not_imply_physical_capacity():
    def get(enum, values):
        assert enum == 0x87FC
        values[:] = (2000, 1024, 700, 400)

    probe = DriverMemoryProbe({'GL_ATI_meminfo'}, owned=True, bind=lambda: (lambda: 1, get))
    sample = probe.sample()
    assert sample['state'] == 'available' and sample['scope'] == 'texture_pool'
    assert sample['available_bytes'] == 2000 * 1024 and sample['largest_block_bytes'] == 1024**2
    assert sample['aux_available_bytes'] == 700 * 1024 and sample['aux_largest_block_bytes'] == 400 * 1024
    assert 'dedicated_bytes' not in sample and 'total_available_bytes' not in sample


def test_advertised_extension_with_no_native_binding_is_unavailable():
    probe = DriverMemoryProbe({'GL_NVX_gpu_memory_info'}, owned=True, bind=lambda: None)
    assert probe.sample()['reason'] == 'native_query_unavailable'
