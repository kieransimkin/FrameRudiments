"""Real-driver parity and lineage for resident and bridged spatial warps."""
from copy import deepcopy
import ctypes
from math import tau
import os
from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.effect_graph_backend_contract import backend_registry, gpu_requirements
from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
from framerudiments.effect_graph_gpu_islands import plan_islands
from framerudiments.effect_graph_gpu_memory import _bind_integer_reader
from framerudiments.effect_graph_ripples import MAX_RIPPLE_SOURCES, RIPPLE_VARIANT_ORDER
from framerudiments.effect_graph_spatial import GPU_WARP_TYPES, render_spatial_warp_with_alpha


@pytest.fixture(scope='module')
def gpu():
    with GLMatteBackend(backend=None if sys.platform == 'win32' else 'egl') as backend:
        yield backend


def _source(size=(83, 59)):
    y, x = np.indices((size[1], size[0]))
    rgba = np.stack(((x * 19 + y * 13) % 256, (x * 5 + y * 17) % 256,
                     (x * 3 + y * 11) % 256, (x * 19 + y * 23) % 256), axis=-1)
    rgba[9:19, 12:25] = [240, 255, 20, 0]
    return Image.fromarray(rgba.astype(np.uint8))


def _graph(*nodes):
    return EffectGraph({'nodes': [{'id': 'a', 'type': 'source'}, *nodes], 'output': nodes[-1]['id']})


def _warp(source='a', **config):
    return {'id': 'warp', 'type': 'spatial_warp', 'inputs': [source],
            'warp_amplitude': [-19, 23], 'warp_scale': [31.8, 27.3],
            'warp_radius': 38.4, 'warp_phase': .8, 'warp_mix': .74, **config}


def _near(reference, actual, *, alpha=2, rgb=3):
    a, b = (np.asarray(image, dtype=np.int16) for image in (reference, actual))
    delta = np.abs(a - b)
    assert delta[..., 3].max() <= alpha
    visible = (a[..., 3] >= 16) & (b[..., 3] >= 16)
    assert delta[..., :3][visible].max(initial=0) <= rgb


@pytest.mark.parametrize('kind', [kind for kind in GPU_WARP_TYPES
                                  if kind not in {'polar_wrap', 'displacement', 'raindrop_ripples'}])
@pytest.mark.parametrize('border', ['transparent', 'clamp', 'repeat', 'mirror'])
def test_warp_residency_matches_cpu(gpu, kind, border):
    source = _source()
    graph = _graph(_warp(warp_type=kind, warp_border=border, warp_seed=764,
                         warp_octaves=6, warp_angle=-115, warp_center=[.51, .48]))
    images = {'a': source}
    before = source.tobytes()
    actual = graph.render(images, gpu_backend=gpu, gpu_strict=True)
    _near(graph.render(images), actual)
    pixels = np.asarray(actual)
    assert not np.any(pixels[pixels[..., 3] == 0, :3])
    report = gpu.last_transport
    assert report['gpu_resident_graph']
    assert report['resident_draws'] == report['uploads'] == report['readbacks'] == 1
    assert report['upload_bytes'] == report['readback_bytes'] == source.width * source.height * 4
    assert gpu.last_dispatch['cpu_fallbacks'] == 0
    assert source.tobytes() == before


@pytest.mark.parametrize('variant', RIPPLE_VARIANT_ORDER)
@pytest.mark.parametrize('border', ['transparent', 'clamp', 'repeat', 'mirror'])
def test_raindrop_variants_match_cpu_on_all_borders(gpu, variant, border):
    graph = _graph(_warp(warp_type='raindrop_ripples', warp_ripple_variant=variant,
                         warp_border=border, warp_segments=MAX_RIPPLE_SOURCES,
                         warp_seed=209, warp_radius=43.7, warp_center=[.50000001, .5],
                         warp_scale=[1 if variant == 'droplets' else 16, 1]))
    source = _source()
    images = {'a': source}
    before = source.tobytes()
    reference = graph.render(images)
    actual = graph.render(images, gpu_backend=gpu, gpu_strict=True)
    _near(reference, actual)
    assert reference.tobytes() != before == source.tobytes()
    pixels = np.asarray(actual)
    assert not np.any(pixels[pixels[..., 3] == 0, :3])
    report = gpu.last_transport
    assert report['gpu_resident_graph']
    assert report['resident_draws'] == report['uploads'] == report['readbacks'] == 1
    assert report['upload_bytes'] == report['readback_bytes'] == source.width * source.height * 4


def test_raindrop_phase_seed_and_variant_resolve_each_sample(gpu):
    graph = _graph(_warp(warp_type='raindrop_ripples', warp_radius=45,
                         warp_center=[.50000001, .5], warp_scale=[16, 1]))
    original = deepcopy(graph.nodes['warp'].config)
    images = {'a': _source()}
    draws = []
    for variant, phase, seed in [
            ('rings', tau - 1e-8, 9), ('rings', tau + 1e-8, 9),
            ('storm', -tau - 1e-8, 41), ('storm', -tau - 1e-8, 42), ('droplets', .2, 41)]:
        values = {('warp', 'warp_ripple_variant'): variant, ('warp', 'warp_phase'): phase,
                  ('warp', 'warp_seed'): seed}
        actual = graph.render(images, gpu_backend=gpu, gpu_strict=True, parameter_values=values)
        _near(graph.render(images, parameter_values=values), actual)
        draws.append((values, actual.tobytes()))
    assert len({pixels for _, pixels in draws}) == len(draws)
    for values, pixels in reversed(draws):
        assert graph.render(images, gpu_backend=gpu, gpu_strict=True,
                            parameter_values=values).tobytes() == pixels
    assert graph.nodes['warp'].config == original


def test_raindrop_gpu_bridge_returns_independent_original_alpha(gpu):
    source = _source()
    original = source.getchannel('A')
    source.putalpha(original.point(lambda value: round(value * .6)))
    config = {'warp_type': 'raindrop_ripples', 'warp_ripple_variant': 'storm',
              'warp_scale': [16, 1], 'warp_amplitude': [27, -15], 'warp_radius': 49,
              'warp_phase': .7, 'warp_seed': 42, 'warp_segments': 17, 'warp_mix': .76}
    reference, expected_alpha = render_spatial_warp_with_alpha(source, config, original_alpha=original)
    gpu.begin_transfer_sample()
    actual, lineage = gpu.spatial_warp([source], config, original_alpha=original)
    _near(reference, actual)
    assert np.abs(np.asarray(expected_alpha, dtype=np.int16) - np.asarray(lineage)).max() <= 2
    assert lineage.tobytes() != actual.getchannel('A').tobytes()
    assert gpu.last_transport['uploads'] == gpu.last_transport['readbacks'] == 2


def test_raindrop_gpu_resident_original_alpha_follows_warp(gpu):
    graph = _graph(
        {'id': 'dim', 'type': 'opacity', 'inputs': ['a'], 'opacity': .64},
        _warp('dim', warp_type='raindrop_ripples', warp_ripple_variant='storm'),
        {'id': 'mask', 'type': 'source'},
        {'id': 'out', 'type': 'matte', 'inputs': ['warp', 'mask'], 'alpha_source': 'original'},
    )
    source = _source()
    images = {'a': source, 'mask': Image.new('RGBA', source.size, 'white')}
    actual = graph.render(images, gpu_backend=gpu, gpu_strict=True)
    _near(graph.render(images), actual, alpha=2, rgb=5)
    assert actual.getchannel('A').tobytes() != source.getchannel('A').tobytes()
    assert gpu.last_transport['resident_draws'] == 5
    assert gpu.last_transport['uploads'] == 2 and gpu.last_transport['readbacks'] == 1


@pytest.mark.parametrize('identity', [{'warp_mix': 0}, {'warp_amplitude': [0, 0]}])
def test_raindrop_identity_needs_no_shader_draw(gpu, identity):
    source = _source()
    graph = _graph(_warp(warp_type='raindrop_ripples', warp_scale=[1, 1], **identity))
    assert graph.render({'a': source}, gpu_backend=gpu, gpu_strict=True).tobytes() == source.tobytes()
    assert gpu.last_transport['resident_draws'] == 0


@pytest.mark.parametrize('border', ['transparent', 'clamp', 'repeat', 'mirror'])
def test_negative_fractional_coordinates_on_a_single_column(gpu, border):
    image = _source((1, 17))
    graph = _graph(_warp(warp_amplitude=[-8.5, 4.3], warp_border=border, warp_mix=1))
    _near(graph.render({'a': image}), graph.render({'a': image}, gpu_backend=gpu, gpu_strict=True))


@pytest.mark.parametrize('override', [0, .37])
def test_warp_opacity_and_fork_preserve_warped_original_alpha(gpu, override):
    source = _source()
    # The first opacity splits current coverage from original coverage. Opacity
    # on the warp itself must also preserve the separately warped silhouette.
    graph = _graph(
        {'id': 'dim', 'type': 'opacity', 'inputs': ['a'], 'opacity': .64},
        _warp('dim', opacity=.72),
        {'id': 'mask', 'type': 'source'},
        {'id': 'restore', 'type': 'matte', 'inputs': ['warp', 'mask'], 'alpha_source': 'original'},
        {'id': 'out', 'type': 'composite', 'inputs': ['warp', 'restore']},
    )
    images = {'a': source, 'mask': Image.new('RGBA', source.size, 'white')}
    values = {('warp', 'warp_mix'): override}
    baseline = deepcopy(graph.nodes['warp'].config)
    actual = graph.render(images, parameter_values=values, gpu_backend=gpu, gpu_strict=True)
    _near(graph.render(images, parameter_values=values), actual, alpha=3, rgb=5)
    assert gpu.last_transport['uploads'] == 2 and gpu.last_transport['readbacks'] == 1
    assert graph.render(images, parameter_values=values, gpu_backend=gpu, gpu_strict=True).tobytes() == (
        actual.tobytes())
    assert graph.nodes['warp'].config == baseline


def test_exact_identity_preserves_hidden_rgb_and_alpha(gpu):
    image = _source()
    graph = _graph(_warp(warp_mix=0))
    result = graph.render({'a': image}, gpu_backend=gpu, gpu_strict=True)
    assert result.tobytes() == image.tobytes()
    assert gpu.last_transport['resident_draws'] == 0


def test_cpu_style_receives_gpu_bridge_lineage(gpu):
    graph = _graph(
        {'id': 'dim', 'type': 'opacity', 'inputs': ['a'], 'opacity': .64},
        _warp('dim', opacity=.72),
        {'id': 'out', 'type': 'layer_style', 'inputs': ['warp'], 'style_type': 'outline',
         'style_color': '#00ffff', 'style_width': 2, 'alpha_source': 'original'},
    )
    images = {'a': _source()}
    islands, rejected = plan_islands(graph, {})
    assert not islands
    assert rejected[0]['reason'] == 'internal_alpha_lineage_exit'
    for diagnostics in (None, {}):
        actual = graph.render(images, gpu_backend=gpu, style_report=diagnostics)
        _near(graph.render(images), actual, alpha=3, rgb=5)
        assert gpu.last_transport['uploads'] == gpu.last_transport['readbacks'] == 2
        assert gpu.last_dispatch == {'gpu_nodes': 1, 'cpu_fallbacks': 1}
    assert 'out' in diagnostics['nodes']


def test_mixed_island_does_not_substitute_boundary_current_alpha(gpu, monkeypatch):
    monkeypatch.setattr(gpu, 'supports_precise_spatial_warps', False)
    graph = _graph(
        {'id': 'dim', 'type': 'opacity', 'inputs': ['a'], 'opacity': .58},
        _warp('dim', id='cpu', warp_type='raindrop_ripples', warp_scale=[8, 23]),
        _warp('cpu'),
        {'id': 'mask', 'type': 'source'},
        {'id': 'out', 'type': 'matte', 'inputs': ['warp', 'mask'], 'alpha_source': 'original'},
    )
    source = _source()
    images = {'a': source, 'mask': Image.new('RGBA', source.size, 'white')}
    actual = graph.render(images, gpu_backend=gpu)
    _near(graph.render(images), actual, alpha=3, rgb=5)
    assert gpu.last_dispatch == {'gpu_nodes': 3, 'cpu_fallbacks': 1}
    assert any(row['reason'] == 'external_alpha_lineage'
               for row in gpu.last_transport['gpu_island_rejections'])


@pytest.mark.parametrize('border', ['transparent', 'clamp', 'mirror'])
def test_polar_wrap_nonperiodic_borders_use_basic_shader(gpu, monkeypatch, border):
    source = _source()
    source.paste((12, 252, 164, 255), (0, 0, 2, source.height))
    source.paste((251, 34, 12, 180), (source.width - 2, 0, source.width, source.height))
    images = {'a': source}
    monkeypatch.setattr(gpu, 'supports_precise_spatial_warps', False)
    graph = _graph(_warp(warp_type='polar_wrap', warp_border=border, warp_radius=16))
    for angle in (-720, 0, 89.9999999, 90, 90.0000001, 180, 270, 720):
        values = {('warp', 'warp_angle'): angle, ('warp', 'warp_center.x'): .5,
                  ('warp', 'warp_center.y'): .5}
        reference = graph.render(images, parameter_values=values)
        actual = graph.render(images, gpu_backend=gpu, gpu_strict=True, parameter_values=values)
        _near(reference, actual)
        assert gpu.last_transport['uploads'] == gpu.last_transport['readbacks'] == 1


@pytest.mark.parametrize('config', [
    {'warp_type': 'polar_wrap', 'warp_border': 'repeat', 'warp_radius': 15.99},
    {'warp_type': 'raindrop_ripples', 'warp_ripple_variant': 'rings', 'warp_scale': [15.99, 64]},
    {'warp_type': 'raindrop_ripples', 'warp_ripple_variant': 'storm', 'warp_scale': [15.99, 64]},
    {'warp_type': 'raindrop_ripples', 'warp_ripple_variant': 'droplets', 'warp_amplitude': [128.01, 0]},
])
def test_precision_limit_uses_cpu_without_upload(gpu, monkeypatch, config):
    monkeypatch.setattr(gpu, 'supports_precise_spatial_warps', False)
    graph = _graph(_warp(**config))
    images = {'a': _source()}
    with pytest.raises(GPUUnsupported, match='warp_precision_limit_exceeded'):
        graph.render(images, gpu_backend=gpu, gpu_strict=True)
    assert gpu.last_transport['uploads'] == 0
    assert graph.render(images, gpu_backend=gpu).tobytes() == graph.render(images).tobytes()
    assert gpu.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}


def test_resolved_precision_limits_are_shared_by_manifest_and_execution(gpu, monkeypatch):
    graph = _graph(_warp(warp_type='polar_wrap', warp_border='repeat'))
    source = _source()
    values = {('warp', 'warp_radius'): .5003}
    assert graph.spatial_manifest()['warp']['gpu_native']
    assert gpu.supports_precise_spatial_warps
    row = graph.backend_plan({'a': source.size}, mode='gpu', gpu_backend=gpu,
                             parameter_values=values)['nodes']['warp']
    assert row['selected_backend'] == 'gpu_bridge'
    _near(graph.render({'a': source}, parameter_values=values),
          graph.render({'a': source}, gpu_backend=gpu, gpu_strict=True, parameter_values=values))
    assert gpu.last_transport['uploads'] == gpu.last_transport['readbacks'] == 1
    monkeypatch.setattr(gpu, 'supports_precise_spatial_warps', False)
    row = graph.backend_plan({'a': source.size}, mode='auto', gpu_backend=gpu,
                             parameter_values=values)['nodes']['warp']
    assert row['selected_backend'] == 'cpu'
    assert row['decision_reason'] == 'warp_precision_limit_exceeded'
    assert graph.render({'a': source}, gpu_backend=gpu, parameter_values=values).tobytes() == (
        graph.render({'a': source}, parameter_values=values).tobytes())
    assert gpu.last_transport['uploads'] == 0


def test_precision_shader_compile_failure_falls_back_before_upload(gpu, monkeypatch):
    graph = _graph(_warp(warp_type='polar_wrap', warp_radius=.5013, warp_border='mirror'))
    images = {'a': _source()}
    assert gpu.supports_precise_spatial_warps

    def fail(_name, _fragment):
        raise gpu._moderngl.Error('synthetic compiler failure')

    monkeypatch.setattr(gpu, '_get_shader', fail)
    with pytest.raises(GPUUnsupported, match='failed to compile or link'):
        graph.render(images, gpu_backend=gpu, gpu_strict=True)
    assert gpu.last_transport['uploads'] == 0
    assert graph.render(images, gpu_backend=gpu).tobytes() == graph.render(images).tobytes()
    assert gpu.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}
    assert gpu.last_transport['uploads'] == 0


@pytest.mark.parametrize('kind,settings', [
    ('wave', {'warp_scale': [1.0000001, 1.017], 'warp_phase': -192.1303}),
    ('turbulence', {'warp_scale': [1, 1.013], 'warp_phase': -192.1303, 'warp_octaves': 6}),
    ('radial', {'warp_amount': -2, 'warp_center': [.500000001, .499999998], 'warp_radius': 8192}),
])
def test_full_authored_range_on_basic_gpu(gpu, monkeypatch, kind, settings):
    monkeypatch.setattr(gpu, 'supports_precise_spatial_warps', False)
    source = _source((5121, 17))
    graph = _graph(_warp(warp_type=kind, warp_amplitude=[4096, -4096],
                         warp_border='repeat', warp_mix=1, **settings))
    actual = graph.render({'a': source}, gpu_backend=gpu, gpu_strict=True)
    _near(graph.render({'a': source}), actual, alpha=3, rgb=5)
    assert gpu.last_transport['resident_draws'] == gpu.last_transport['uploads'] == 1


@pytest.mark.parametrize('variant', RIPPLE_VARIANT_ORDER)
def test_precision_ripple_bridge_carries_original_alpha(gpu, variant):
    assert gpu.supports_precise_spatial_warps
    source = _source((321, 147))
    original = source.getchannel('A')
    source.putalpha(original.point(lambda value: round(value * .6)))
    config = {'warp_type': 'raindrop_ripples', 'warp_ripple_variant': variant,
              'warp_scale': [1.04, 1], 'warp_amplitude': [4096, -4096], 'warp_radius': 142,
              'warp_center': [.5000000001, .5], 'warp_segments': 32, 'warp_seed': 42,
              'warp_phase': tau - 1e-8, 'warp_border': 'mirror'}
    reference, expected_alpha = render_spatial_warp_with_alpha(source, config, original_alpha=original)
    graph = _graph(_warp(**config))
    assert not graph.spatial_manifest()['warp']['gpu_native']
    attached = graph.spatial_manifest(gpu_backend=gpu)['warp']
    assert attached['gpu_native'] and attached['gpu_required_extension'] == 'GL_ARB_gpu_shader5'
    gpu.begin_transfer_sample()
    actual, lineage = gpu.spatial_warp([source], config, original_alpha=original)
    _near(reference, actual, alpha=3, rgb=5)
    assert np.abs(np.asarray(expected_alpha, dtype=np.int16) - np.asarray(lineage)).max() <= 3
    assert gpu.last_transport['uploads'] == gpu.last_transport['readbacks'] == 2


@pytest.mark.parametrize('angle,centre', [
    (0, [.5, .5]), (90.000001, [.50000001, .5]), (180.000001, [.49999999, .5]),
    (225, [.50000001, .5]), (-360, [.41, .58]),
])
def test_polar_wrap_repeat_seam_and_near_pixel_origin(gpu, angle, centre):
    source = _source()
    source.paste((255, 255, 255, 255), (0, 0, 2, source.height))
    source.paste((255, 0, 0, 90), (source.width - 2, 0, source.width, source.height))
    graph = _graph(_warp(warp_type='polar_wrap', warp_border='repeat', warp_radius=35,
                         warp_mix=1, warp_angle=angle, warp_center=centre))
    actual = graph.render({'a': source}, gpu_backend=gpu, gpu_strict=True)
    _near(graph.render({'a': source}), actual)
    assert gpu.last_transport['uploads'] == gpu.last_transport['readbacks'] == 1


def _displacement(map_id='map', **config):
    return {**_warp(warp_type='displacement', **config), 'inputs': ['a', map_id]}


@pytest.mark.parametrize('border', ['transparent', 'clamp', 'repeat', 'mirror'])
@pytest.mark.parametrize('channels', [('r', 'g'), ('b', 'a'), ('luminance', 'r')])
def test_resident_map_warp_matches_cpu(gpu, border, channels):
    graph = _graph({'id': 'map', 'type': 'source'},
                   _displacement(warp_border=border, warp_map_x=channels[0], warp_map_y=channels[1],
                                 warp_neutral=.37, warp_amplitude=[-25, 16]))
    source = _source()
    control = _source()
    images = {'a': source, 'map': control}
    actual = graph.render(images, gpu_backend=gpu, gpu_strict=True)
    _near(graph.render(images), actual)
    report = gpu.last_transport
    assert report['resident_draws'] == 1 and report['uploads'] == 2 and report['readbacks'] == 1
    assert report['upload_bytes'] == 2 * report['readback_bytes'] == 2 * source.width * source.height * 4
    assert gpu.last_dispatch['cpu_fallbacks'] == 0


@pytest.mark.parametrize('alpha,opacity', [(0, 1), (255, 0), (1, .49), (1, .5)])
def test_transparent_direct_map_preserves_all_original_bytes(gpu, alpha, opacity):
    source = _source()
    graph = _graph({'id': 'map', 'type': 'source', 'opacity': opacity}, _displacement())
    images = {'a': source, 'map': Image.new('RGBA', source.size, (255, 20, 145, alpha))}
    actual = graph.render(images, gpu_backend=gpu, gpu_strict=True)
    assert actual.tobytes() == source.tobytes()
    assert actual.tobytes() == graph.render(images).tobytes()
    assert gpu.last_dispatch['gpu_nodes'] == (1 if opacity == 0 else 0)


@pytest.mark.parametrize('strict', [True, False])
def test_fractional_map_source_opacity_keeps_exact_byte_before_displacement(gpu, strict):
    graph = _graph({'id': 'map', 'type': 'source', 'opacity': .5},
                   _displacement(warp_border='repeat', warp_amplitude=[128, 128], warp_map_x='a'))
    images = {'a': _source(), 'map': _source()}
    actual = graph.render(images, gpu_backend=gpu, gpu_strict=strict)
    _near(graph.render(images), actual)
    assert gpu.last_dispatch['cpu_fallbacks'] == 0
    if strict:
        assert gpu.last_transport['uploads'] == 4 and gpu.last_transport['readbacks'] == 2
    else:
        assert gpu.last_transport['uploads'] == 2 and gpu.last_transport['readbacks'] == 1


def test_processed_map_is_bridged_and_keeps_transparent_identity(gpu):
    graph = _graph({'id': 'map', 'type': 'source'},
                   {'id': 'dim', 'type': 'opacity', 'inputs': ['map'], 'opacity': 0},
                   _displacement('dim'))
    images = {'a': _source(), 'map': _source()}
    for strict in (True, False):
        result = graph.render(images, gpu_backend=gpu, gpu_strict=strict)
        assert result.tobytes() == images['a'].tobytes()
        assert gpu.last_transport['uploads'] == gpu.last_transport['readbacks'] == 0
        assert gpu.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 0}


def test_processed_map_bridge_preserves_original_alpha_and_counts_transfers(gpu):
    nodes = (
        {'id': 'map', 'type': 'source'},
        {'id': 'dim', 'type': 'opacity', 'inputs': ['map'], 'opacity': .7},
        _displacement('dim', opacity=.3),
    )
    graph = _graph(*nodes,
                   {'id': 'out', 'type': 'layer_style', 'inputs': ['warp'],
                    'style_type': 'outline', 'style_width': 2, 'alpha_source': 'original'})
    images = {'a': _source(), 'map': _source()}
    warp = _graph(*nodes)
    _near(warp.render(images), warp.render(images, gpu_backend=gpu, gpu_strict=True))
    actual = graph.render(images, gpu_backend=gpu, style_report={})
    expected = graph.render(images)
    # The outline composites straight RGB against low-alpha edges. Test its
    # actual visible contribution separately from the raw warp's RGB parity.
    a, b = (np.asarray(im, dtype=np.float32) for im in (expected, actual))
    assert np.abs(a[..., 3] - b[..., 3]).max() <= 3
    for background in (0, 255):
        a_rgb, b_rgb = (im[..., :3] * (im[..., 3:4] / 255)
                        + background * (1 - im[..., 3:4] / 255) for im in (a, b))
        assert np.abs(a_rgb - b_rgb).max() <= 3
    report = gpu.last_transport
    assert report['uploads'] == 4 and report['readbacks'] == 2
    assert gpu.last_dispatch == {'gpu_nodes': 1, 'cpu_fallbacks': 1}


def test_warp_resident_budget_fails_before_first_upload():
    image = _source()
    graph = _graph(_warp())
    with GLMatteBackend(backend=None if sys.platform == 'win32' else 'egl',
                        max_working_set_bytes=image.width * image.height * 4) as gpu:
        with pytest.raises(GPUUnsupported, match='budget'):
            graph.render({'a': image}, gpu_backend=gpu, gpu_strict=True)
        assert gpu.last_transport['uploads'] == 0
        actual = graph.render({'a': image}, gpu_backend=gpu)
        assert actual.tobytes() == graph.render({'a': image}).tobytes()
        assert gpu.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}


@pytest.mark.parametrize('kind', ['wave', 'raindrop_ripples'])
def test_project_gpu_warp_uses_native_timing_and_reproduces_seeks(tmp_path, kind):
    default_font = ('C:/Windows/Fonts/arialbd.ttf' if sys.platform == 'win32'
                    else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    font = Path(os.environ.get('FRAMERUDIMENTS_TEST_FONT', default_font))
    project = Project.load(create_demo(tmp_path / 'gpu-warp', str(font), width=180, height=320))
    data = dict(
        project.data,
        render_quality={'enabled': False},
        effect_graph={'nodes': [
            {'id': 'scene', 'type': 'source'},
            _warp('scene', warp_type=kind, warp_amplitude=[9, 7], warp_border='repeat',
                  warp_ripple_variant='storm'),
        ], 'output': 'warp'},
        effect_graph_controls=[{'node': 'warp', 'property': 'warp_phase',
                                'motion': {'kind': 'named', 'pattern': 'circle'},
                                'axis': 'x', 'range': [0, 3]}],
    )

    def scene(image, _context):
        draw = ImageDraw.Draw(image)
        draw.ellipse((42, 32, 135, 273), fill=(51, 128, 201, 220))
        draw.rectangle((68, 85, 150, 120), fill=(239, 205, 58, 255))
        return image

    cpu = Compositor(Project(data, project.base), scene=scene)
    device = Compositor(Project({**data, 'effect_graph_backend': 'gpu'}, project.base), scene=scene)
    try:
        frames = (0, 7, 19, 31)
        resolved = [device.effect_graph_controls.values(device.context(frame), device.effect_graph)
                    [('warp', 'warp_phase')] for frame in frames]
        assert cpu.effect_graph is not None
        assert max(resolved) > min(resolved)
        observed = []
        for frame in frames:
            sample = device.frame(frame, apply_quality=False)[0]
            _near(cpu.frame(frame, apply_quality=False)[0].convert('RGBA'), sample.convert('RGBA'))
            observed.append(sample.tobytes())
        assert len(set(observed)) > 1
        for frame, expected in reversed(list(zip(frames, observed))):
            assert device.frame(frame, apply_quality=False)[0].tobytes() == expected
            report = device.evidence()['effect_graph_backend']
            assert report['requested'] == 'gpu'
            assert report['last_dispatch'] == {'gpu_nodes': 1, 'cpu_fallbacks': 0}
            assert report['last_transport']['gpu_resident_graph']
            assert report['last_transport']['resident_target_reuses'] == 1
    finally:
        device.close()
        cpu.close()


def _local_backend(**kwargs):
    return GLMatteBackend(backend=None if sys.platform == 'win32' else 'egl', **kwargs)


def test_reused_targets_match_disabled_pool_across_changed_inputs_and_shapes():
    graph = _graph(_warp())
    sources = [_source((31, 23)), _source((31, 23)), _source((23, 31)), _source((23, 31))]
    for i, image in enumerate(sources):
        ImageDraw.Draw(image).rectangle((2, 3, 15, 12), fill=(61 * i, 191, 13, 240))
    payload_bytes = 31 * 23 * 4
    with _local_backend(max_pooled_target_bytes=0) as gpu:
        expected = [graph.render({'a': image}, gpu_backend=gpu, gpu_strict=True).tobytes() for image in sources]
        assert gpu.last_transport['resident_target_allocations'] == 1
        assert gpu.last_transport['retained_target_count'] == 0
    assert len(set(expected)) == len(sources)
    with _local_backend(max_working_set_bytes=2 * payload_bytes,
                        max_pooled_target_bytes=payload_bytes) as gpu:
        for i, image in enumerate(sources):
            assert graph.render({'a': image}, gpu_backend=gpu, gpu_strict=True).tobytes() == expected[i]
            row = gpu.last_transport
            assert row['resident_target_allocations'] == 1 - (i % 2)
            assert row['resident_target_reuses'] == i % 2
            assert row['resident_estimated_texture_bytes'] == 2 * payload_bytes
            assert row['retained_target_payload_bytes'] == payload_bytes
            assert row['uploads'] == row['readbacks'] == 1
            assert not row['measured_vram']
    assert gpu.last_transport['retained_target_count'] == 0


def test_resident_and_bridge_budgets_trim_idle_targets():
    image = _source((31, 23))
    one = _graph(_warp())
    two = _graph(_warp(id='first'), _warp('first'))
    surface_bytes = image.width * image.height * 4
    with _local_backend(max_pooled_target_bytes=2 * surface_bytes) as gpu:
        two.render({'a': image}, gpu_backend=gpu, gpu_strict=True)
        assert gpu.last_transport['retained_target_count'] == 2
        gpu.max_working_set_bytes = 2 * surface_bytes
        one.render({'a': image}, gpu_backend=gpu, gpu_strict=True)
        assert gpu.last_transport['retained_target_count'] == gpu.last_transport['resident_target_reuses'] == 1
        with pytest.raises(GPUUnsupported, match='budget'):
            two.render({'a': image}, gpu_backend=gpu, gpu_strict=True)
        assert gpu.last_transport['uploads'] == gpu.last_transport['resident_target_reuses'] == 0
        assert two.render({'a': image}, gpu_backend=gpu).tobytes() == two.render({'a': image}).tobytes()
        assert gpu.last_transport['uploads'] == gpu.last_transport['readbacks'] == 0
        config = one.nodes['warp'].config
        requirement = gpu_requirements('spatial_warp', config, 1)
        gpu.max_working_set_bytes = requirement.texture_count * surface_bytes
        gpu.begin_transfer_sample()
        output, _ = gpu.spatial_warp([image], config)
        _near(one.render({'a': image}), output)
        assert gpu.last_transport['retained_target_count'] == gpu.last_transport['resident_target_reuses'] == 0


def test_target_pool_caps_object_count_on_small_canvases():
    nodes = [_warp(id=f'fork{i}', warp_phase=i + 1) for i in range(12)]
    graph = _graph(*nodes, {'id': 'out', 'type': 'composite', 'inputs': [node['id'] for node in nodes]})
    sources = {'a': _source((31, 23))}
    with _local_backend() as gpu:
        first = graph.render(sources, gpu_backend=gpu, gpu_strict=True).tobytes()
        allocations = gpu.last_transport['resident_target_allocations']
        assert allocations > 8
        assert gpu.last_transport['retained_target_count'] == 8
        assert graph.render(sources, gpu_backend=gpu, gpu_strict=True).tobytes() == first
        assert gpu.last_transport['resident_target_reuses'] == 8
        assert gpu.last_transport['resident_target_allocations'] == allocations - 8


@pytest.mark.parametrize('where', ['_get_shader', '_readback_rgba'])
def test_failed_frame_discards_its_leases(monkeypatch, where):
    graph = _graph(_warp())
    images = {'a': _source((31, 23))}

    def failed(*_args):
        raise RuntimeError('forced frame failure')

    with _local_backend() as gpu:
        previous = graph.render(images, gpu_backend=gpu, gpu_strict=True).tobytes()
        with monkeypatch.context() as patch:
            patch.setattr(gpu, where, failed)
            with pytest.raises(RuntimeError, match='forced frame failure'):
                graph.render(images, gpu_backend=gpu, gpu_strict=True)
        assert gpu.last_transport['resident_target_reuses'] == 1
        assert gpu.last_transport['readbacks'] == gpu.last_transport['retained_target_count'] == 0
        assert graph.render(images, gpu_backend=gpu, gpu_strict=True).tobytes() == previous
        assert gpu.last_transport['resident_target_allocations'] == 1
        assert gpu.last_transport['resident_target_reuses'] == 0


def test_real_driver_memory_snapshot_and_device_discovery(monkeypatch):
    def fail():
        pytest.fail('Capability discovery must not sample memory')

    with _local_backend() as gpu:
        sample = gpu.sample_driver_memory()
        extensions = gpu.ctx.extensions
        if 'llvmpipe' in gpu.capabilities.renderer.lower():
            # Mesa may advertise NVX even on its software driver. Do not
            # interpret that renderer's host memory as physical video memory.
            assert sample['reason'] == 'software_renderer', sample
            current, get = _bind_integer_reader()
            value = ctypes.c_int()
            assert current()
            get(0x0D33, ctypes.byref(value))  # GL_MAX_TEXTURE_SIZE
            assert value.value == gpu.ctx.info['GL_MAX_TEXTURE_SIZE']
        elif 'GL_NVX_gpu_memory_info' in extensions:
            assert sample['state'] == 'available' and sample['scope'] == 'device', sample
            assert sample['dedicated_bytes'] > 0 and sample['available_bytes'] >= 0
        elif 'GL_ATI_meminfo' in extensions:
            assert sample['state'] == 'available' and sample['scope'] == 'texture_pool'
        else:
            assert sample['reason'] == 'extension_unavailable'
        with monkeypatch.context() as patch:
            patch.setattr(gpu, 'sample_driver_memory', fail)
            device = backend_registry(gpu)['backends']['moderngl']['device']
            assert device['driver_memory_extension'] == sample['extension']
            assert 0 < device['max_retained_target_payload_bytes'] <= gpu.max_working_set_bytes
        with GLMatteBackend(ctx=gpu.ctx) as borrowed:
            graph = _graph(_warp())
            graph.render({'a': _source((31, 23))}, gpu_backend=borrowed, gpu_strict=True)
            assert borrowed.last_transport['retained_target_count'] == 0
            assert borrowed.sample_driver_memory()['state'] == 'unavailable'
    assert gpu.sample_driver_memory()['reason'] == 'backend_closed'
