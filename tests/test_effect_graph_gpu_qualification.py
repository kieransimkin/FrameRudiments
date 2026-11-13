"""Device-name gates for the portable qualification command; no GL context needed."""
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def qualification(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'integration'))
    return importlib.import_module('qualify_effect_graph_m8_9')


@pytest.mark.parametrize('vendor,renderer,expected', [
    ('ATI Technologies Inc.', 'AMD Radeon RX 7600', 'amd'),
    ('AMD', 'AMD Radeon RX 7800 XT (radeonsi, navi32, LLVM 20.1.2, DRM 3.64)', 'amd'),
    ('Intel', 'Mesa Intel(R) Arc(tm) A770 Graphics (DG2)', 'intel'),
    ('NVIDIA Corporation', 'NVIDIA GeForce RTX 4060 Ti/PCIe/SSE2', 'nvidia'),
    ('Mesa', 'llvmpipe (LLVM 20.1.2, 256 bits)', 'software'),
    ('AMD', 'softpipe', 'software'),
    ('Google Inc. (Intel)', 'ANGLE (Intel, Intel Arc A770 Direct3D11)', 'translated_or_virtual'),
    ('Microsoft Corporation', 'D3D12 (Intel(R) UHD Graphics)', 'translated_or_virtual'),
    ('Mesa', 'zink Vulkan 1.4 (AMD Radeon RX 7600)', 'translated_or_virtual'),
    ('Mesa', 'virgl (AMD Radeon RX 7600)', 'translated_or_virtual'),
    ('VMware, Inc.', 'SVGA3D; build: RELEASE', 'translated_or_virtual'),
    ('Mesa', 'Unidentified renderer', 'unknown'),
])
def test_renderer_classification_checks_active_driver(qualification, vendor, renderer, expected):
    assert qualification.renderer_vendor({'vendor': vendor, 'renderer': renderer}) == expected


def test_same_vendor_still_requires_the_intended_renderer_and_precision(qualification):
    device = {'vendor': 'Intel', 'renderer': 'Mesa Intel(R) UHD Graphics', 'precise_spatial_warps': False}
    qualification.require_device(device, vendor='intel', renderer='uhd', precision='unavailable')
    with pytest.raises(RuntimeError, match='Expected renderer containing'):
        qualification.require_device(device, vendor='intel', renderer='Arc')
    with pytest.raises(RuntimeError, match='Expected GL_ARB_gpu_shader5'):
        qualification.require_device(device, vendor='intel', precision='available')
