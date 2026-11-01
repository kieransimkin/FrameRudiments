"""M8.2 real ModernGL byte-transport/parity checks. Required by GPU CI."""
import numpy as np
from PIL import Image
import pytest

from framerudiments.effect_graph_gpu import GLMatteBackend
from framerudiments.effect_graph_gpu_spectral import COPY
from framerudiments.effect_graph_mattes import apply_matte


@pytest.fixture(scope='module')
def gl():
    pytest.importorskip('moderngl')
    try:
        gpu = GLMatteBackend(backend='egl')
    except Exception as exc:
        pytest.skip(f'Real EGL OpenGL 3.3 unavailable: {exc}')
    yield gpu
    gpu.close()


def sample():
    image = Image.new('RGBA', (53, 29))
    for y in range(image.height):
        for x in range(image.width):
            image.putpixel((x, y), ((x * 31 + y * 7) % 256,
                                      (x * 3 + y * 17) % 256,
                                      (x * 11 + y * 13) % 256,
                                      (x * 5 + y * 23) % 256))
    return image


def test_raw_gl_upload_download_uses_same_tight_row_order(gl):
    src = sample()
    gl.begin_transfer_sample()
    allocations = []
    try:
        tex = gl._input(src)
        allocations.append(tex)
        _, fb = gl._draw('m8-2-copy', COPY, {'original_tex': tex}, src.size, {}, allocations)
        returned = gl._readback_rgba(fb, src.size)
        assert np.max(np.abs(np.asarray(src, dtype=np.int16) - np.asarray(returned, dtype=np.int16))) <= 1
        assert gl.last_transport['upload_bytes'] == src.width * src.height * 4
        assert gl.last_transport['readback_bytes'] == src.width * src.height * 4
        assert gl.last_transport['uploads'] == 1 and gl.last_transport['readbacks'] == 1
        assert src.tobytes() == sample().tobytes()
    finally:
        for resource in reversed(allocations):
            resource.release()


def test_original_alpha_matte_transfer_counts_match_v31_plan(gl):
    src = sample()
    mask = Image.new('RGBA', src.size, (100, 80, 140, 190))
    original_alpha = Image.new('L', src.size, 210)
    config = {'alpha_source': 'original', 'mask_combine': 'multiply'}
    gl.begin_transfer_sample()
    actual = gl.apply_matte([src, mask], config, original_alpha=original_alpha)
    expected = apply_matte([src, mask], config, original_alpha=original_alpha)
    error = np.max(np.abs(np.asarray(actual, dtype=np.int16) - np.asarray(expected, dtype=np.int16)))
    assert error <= 2
    assert gl.last_transport['upload_bytes'] == src.width * src.height * 4 * 3
    assert gl.last_transport['readback_bytes'] == src.width * src.height * 4
    assert gl.last_transport['uploads'] == 3 and gl.last_transport['readbacks'] == 1
    assert gl.last_transport['row_order'] == 'top-first'
