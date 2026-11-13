"""Real-driver parity and lineage for resident and bridged spatial warps."""
from copy import deepcopy
import os
from pathlib import Path
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from framerudiments import Project
from framerudiments.compositor import Compositor
from framerudiments.demo import create_demo
from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
from framerudiments.effect_graph_gpu_islands import plan_islands
from framerudiments.effect_graph_spatial import GPU_WARP_TYPES


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


@pytest.mark.parametrize('kind', GPU_WARP_TYPES)
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


def test_mixed_island_does_not_substitute_boundary_current_alpha(gpu):
    graph = _graph(
        {'id': 'dim', 'type': 'opacity', 'inputs': ['a'], 'opacity': .58},
        {'id': 'cpu', 'type': 'spatial_warp', 'inputs': ['dim'], 'warp_type': 'raindrop_ripples'},
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


@pytest.mark.parametrize('kind', ['polar_wrap', 'raindrop_ripples', 'displacement'])
def test_unsupported_warps_remain_explicit_fallbacks(gpu, kind):
    source = _source()
    nodes = [_warp(warp_type=kind)]
    images = {'a': source}
    if kind == 'displacement':
        nodes.insert(0, {'id': 'map', 'type': 'source'})
        nodes[-1]['inputs'].append('map')
        images['map'] = _source()
    graph = _graph(*nodes)
    with pytest.raises(GPUUnsupported, match='warp_type_not_implemented'):
        graph.render(images, gpu_backend=gpu, gpu_strict=True)
    assert gpu.last_transport['uploads'] == 0
    assert graph.render(images, gpu_backend=gpu).tobytes() == graph.render(images).tobytes()
    assert gpu.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}


@pytest.mark.parametrize('config', [
    {'warp_type': 'wave', 'warp_amplitude': [128.01, 0]},
    {'warp_type': 'turbulence', 'warp_scale': [15.99, 16]},
    {'warp_type': 'radial', 'warp_amount': -2, 'warp_center': [.50000001, .5], 'warp_radius': 256},
])
def test_precision_limit_uses_cpu_without_upload(gpu, config):
    graph = _graph(_warp(**config))
    images = {'a': _source()}
    with pytest.raises(GPUUnsupported, match='warp_precision_limit_exceeded'):
        graph.render(images, gpu_backend=gpu, gpu_strict=True)
    assert gpu.last_transport['uploads'] == 0
    assert graph.render(images, gpu_backend=gpu).tobytes() == graph.render(images).tobytes()
    assert gpu.last_dispatch == {'gpu_nodes': 0, 'cpu_fallbacks': 1}


def test_resolved_precision_limits_are_shared_by_manifest_and_execution(gpu):
    graph = _graph(_warp(warp_type='radial'))
    source = _source()
    values = {('warp', 'warp_amount'): -.2}
    assert graph.spatial_manifest()['warp']['gpu_native']
    row = graph.backend_plan({'a': source.size}, mode='auto', gpu_backend=gpu,
                             parameter_values=values)['nodes']['warp']
    assert row['selected_backend'] == 'cpu'
    assert row['decision_reason'] == 'warp_precision_limit_exceeded'
    assert graph.render({'a': source}, gpu_backend=gpu, parameter_values=values).tobytes() == (
        graph.render({'a': source}, parameter_values=values).tobytes())
    assert gpu.last_transport['uploads'] == 0


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


def test_project_gpu_warp_uses_native_timing_and_reproduces_seeks(tmp_path):
    default_font = ('C:/Windows/Fonts/arialbd.ttf' if sys.platform == 'win32'
                    else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    font = Path(os.environ.get('FRAMERUDIMENTS_TEST_FONT', default_font))
    project = Project.load(create_demo(tmp_path / 'gpu-warp', str(font), width=180, height=320))
    data = dict(
        project.data,
        render_quality={'enabled': False},
        effect_graph={'nodes': [
            {'id': 'scene', 'type': 'source'},
            _warp('scene', warp_amplitude=[9, 7], warp_border='repeat'),
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
            assert device.effect_graph_gpu.last_transport['gpu_resident_graph']
    finally:
        device.close()
        cpu.close()
