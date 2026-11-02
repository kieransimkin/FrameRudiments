"""M8.2 host byte-contract tests. No graphics context required."""
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_backend_contract import backend_registry
from framerudiments.effect_graph_gpu import GLMatteBackend
from framerudiments.effect_graph_surface_transport import (
    TransportContract, TransportPayload, bridge_contract, pack_image,
    transport_capabilities, unpack_image,
)


def image():
    result = Image.new('RGBA', (5, 3))
    points = [(10, 20, 30, 255), (200, 110, 15, 128), (255, 0, 128, 64),
              (90, 80, 75, 0), (3, 240, 120, 250)]
    for y in range(3):
        for x in range(5):
            result.putpixel((x, y), points[(x + y) % len(points)])
    return result


@pytest.mark.parametrize('rows', ['top-first', 'bottom-first'])
@pytest.mark.parametrize('alignment', [1, 2, 4, 8])
def test_rgba_straight_bytes_are_exact_including_hidden_rgb(rows, alignment):
    src = image()
    contract = TransportContract(5, 3, row_order=rows, alignment=alignment)
    payload = pack_image(src, contract)
    assert len(payload.data) == contract.nbytes
    assert unpack_image(payload).tobytes() == src.tobytes()
    assert src.getpixel((3, 0)) == (90, 80, 75, 0)
    if rows == 'bottom-first' and alignment == 1:
        assert payload.data[:20] == src.crop((0, 2, 5, 3)).tobytes()
    if contract.row_stride != contract.row_bytes:
        assert payload.data[contract.row_bytes:contract.row_stride] == b'\0' * (contract.row_stride - 20)
    assert json.loads(json.dumps(payload.describe(), allow_nan=False))['row_order'] == rows


@pytest.mark.parametrize('rows', ['top-first', 'bottom-first'])
@pytest.mark.parametrize('alignment', [1, 4, 8])
def test_coverage_plane_strides_and_bottom_first(rows, alignment):
    alpha = image().getchannel('A')
    contract = TransportContract(5, 3, 'r8', 'coverage', 'data', rows, alignment)
    payload = pack_image(alpha, contract)
    assert payload.data != b''
    assert unpack_image(payload).tobytes() == alpha.tobytes()
    assert contract.row_stride % alignment == 0
    with pytest.raises(ValueError, match='L for coverage'):
        pack_image(image(), contract)


@pytest.mark.parametrize('rows', ['top-first', 'bottom-first'])
@pytest.mark.parametrize('alignment', [1, 4, 8])
def test_premultiplication_preserves_alpha_and_canonicalises_hidden_rgb(rows, alignment):
    original = image()
    cfg = TransportContract(5, 3, alpha='premultiplied', row_order=rows, alignment=alignment)
    encoded = pack_image(original, cfg)
    after = unpack_image(encoded)
    assert after.getchannel('A').tobytes() == original.getchannel('A').tobytes()
    assert after.getpixel((3, 0)) == (0, 0, 0, 0)
    for before, observed in zip(original.getdata(), after.getdata()):
        if before[3] > 60:
            assert max(abs(before[k] - observed[k]) for k in range(3)) <= 3
    assert original.getpixel((3, 0)) == (90, 80, 75, 0)


def test_linear_srgb_requires_explicit_source_and_destination_encoding():
    c = TransportContract(1, 1, 'rgba32f', 'straight', 'linear-srgb')
    im = Image.new('RGBA', (1, 1), (128, 128, 128, 255))
    with pytest.raises(ValueError, match='explicit source_encoding'):
        pack_image(im, c)
    payload = pack_image(im, c, source_encoding='srgb')
    floats = np.frombuffer(payload.data, dtype='<f4')
    assert np.allclose(floats[:3], .21586, atol=.0001)
    assert floats[3] == 1
    with pytest.raises(ValueError, match='explicit output_encoding'):
        unpack_image(payload)
    assert unpack_image(payload, output_encoding='srgb').tobytes() == im.tobytes()


@pytest.mark.parametrize('association', ['straight', 'premultiplied'])
@pytest.mark.parametrize('rows', ['top-first', 'bottom-first'])
def test_linear_srgb_float_conversion_and_alpha(association, rows):
    original = image()
    contract = TransportContract(5, 3, 'rgba32f', association, 'linear-srgb', rows, 8)
    payload = pack_image(original, contract, source_encoding='srgb')
    assert payload.contract.nbytes == 5 * 3 * 16
    back = unpack_image(payload, output_encoding='srgb')
    assert back.getchannel('A').tobytes() == original.getchannel('A').tobytes()
    for before, after in zip(original.getdata(), back.getdata()):
        if before[3] > 60:
            assert max(abs(before[k] - after[k]) for k in range(3)) <= 2
    if association == 'premultiplied':
        assert back.getpixel((3, 0))[:3] == (0, 0, 0)


def test_float_payload_nonfinite_or_bad_range_fails_before_pillow_conversion():
    c = TransportContract(1, 1, 'rgba32f', 'straight', 'linear-srgb')
    for bad in (float('nan'), float('inf'), -0.01, 1.01):
        raw = np.array([bad, .2, .1, 1], dtype='<f4').tobytes()
        with pytest.raises(ValueError, match='non-finite/out-of-range'):
            unpack_image(TransportPayload(c, raw), output_encoding='srgb')


@pytest.mark.parametrize('bad', [dict(pixel_format='rgba16f'), dict(alpha='coverage'),
                                dict(colour_space='linear-srgb'), dict(row_order='sideways'),
                                dict(alignment=3), dict(alignment=True),
                                dict(pixel_format='r8'), dict(pixel_format='rgba32f')])
def test_unsupported_formats_and_implicit_colour_assumptions_rejected(bad):
    with pytest.raises(ValueError):
        TransportContract(4, 4, **bad)


@pytest.mark.parametrize('size', [(0, 3), (1, -1), (1.0, 2), (True, 2),
                                  (16385, 1), (16384, 16384)])
def test_invalid_surface_shape(size):
    with pytest.raises(ValueError):
        bridge_contract(size)


def test_strict_length_and_contract_validation():
    c = bridge_contract((3, 2))
    with pytest.raises(ValueError, match='length'):
        TransportPayload(c, b'\0' * (c.nbytes - 1))
    with pytest.raises(ValueError):
        TransportPayload(c, bytearray(c.nbytes))
    with pytest.raises(ValueError):
        pack_image(image(), c)
    with pytest.raises(ValueError):
        unpack_image(c)
    with pytest.raises(FrozenInstanceError):
        c.width = 4


def test_metadata_claims_rgba8_residency_but_not_float_conversion_shaders():
    caps = transport_capabilities()
    assert not caps['gpu_conversion_shaders'] and not caps['gpu_r8_lineage']
    assert caps['device_upload'] == ['rgba8/straight/top-first/tight']
    registry = backend_registry()
    assert registry['surface_transport'] == caps
    assert registry['backends']['moderngl']['gpu_resident_graph']
    assert registry['backends']['moderngl']['mixed_cpu_gpu_islands']
    json.dumps(registry, allow_nan=False)


class Texture:
    def __init__(self):
        self.filter = None
        self.repeat_x = None
        self.repeat_y = None


class Context:
    def __init__(self):
        self.received = []

    def texture(self, size, components, data, *, alignment):
        self.received.append((size, components, data, alignment))
        return Texture()


class Framebuffer:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def read(self, **kwargs):
        self.calls.append(kwargs)
        return self.data


def fake_backend():
    gpu = object.__new__(GLMatteBackend)
    gpu.ctx = Context()
    gpu._moderngl = SimpleNamespace(NEAREST=9728)
    gpu.begin_transfer_sample()
    return gpu


def test_real_gl_bridge_entry_points_preserve_bytes_and_record_actual_payloads():
    gpu = fake_backend()
    original = image()
    tex = gpu._input(original)
    size, components, raw, alignment = gpu.ctx.received[-1]
    assert (size, components, alignment) == (original.size, 4, 1)
    assert raw == original.tobytes()
    assert tex.filter == (9728, 9728) and not tex.repeat_x and not tex.repeat_y
    readback = Framebuffer(raw)
    recovered = gpu._readback_rgba(readback, original.size)
    assert recovered.tobytes() == original.tobytes()
    assert readback.calls == [{'components': 4, 'alignment': 1}]
    assert gpu.last_transport['upload_bytes'] == gpu.last_transport['readback_bytes'] == 5 * 3 * 4
    assert gpu.last_transport['uploads'] == gpu.last_transport['readbacks'] == 1
    assert not gpu.last_transport['gpu_resident_graph']
    gpu.begin_transfer_sample()
    assert gpu.last_transport['upload_bytes'] == gpu.last_transport['readback_bytes'] == 0


def test_malformed_gl_readback_rejected_before_accounting():
    gpu = fake_backend()
    with pytest.raises(ValueError, match='length'):
        gpu._readback_rgba(Framebuffer(b'bad'), (5, 3))
    assert gpu.last_transport['readbacks'] == 0


def test_per_graph_sample_resets_receipts_without_touching_legacy_fakes():
    class FakeGPU:
        def __init__(self):
            self.calls = []

        def begin_transfer_sample(self):
            self.calls.clear()

        def chroma_key(self, source, config, **kw):
            self.calls.append(source.size)
            return source.copy()

    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
                              {'id': 'key', 'type': 'chroma_key', 'inputs': ['s']}], 'output': 'key'})
    fake = FakeGPU()
    for _ in range(3):
        g.render({'s': image()}, gpu_backend=fake)
        assert fake.calls == [(5, 3)]
        assert fake.last_dispatch['gpu_nodes'] == 1
