"""Required real EGL parity gates for M8.7 matte/chroma GPU residency.

These tests intentionally do not skip missing drivers: the dedicated GPU CI
runner installs Mesa/EGL and must exercise actual GLSL rather than mocks.
"""
import numpy as np
import pytest
import sys
from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
from framerudiments.effect_graph_gpu_resident import resident_plan


def _gpu(**kwargs):
    # Linux CI requires Mesa/EGL. Windows' glcontext wheel exposes the native
    # standalone backend instead, which still provides a real-driver gate.
    return GLMatteBackend(backend=None if sys.platform == 'win32' else 'egl', **kwargs)


def _sources(count=2):
    size = (83, 59)
    y, x = np.indices((size[1], size[0]))
    a = np.stack(((x * 7 + y * 13) % 256, (x * 5 + y * 17) % 256,
                  (x * 3 + y * 11) % 256, (x * 19 + y * 23) % 256), -1).astype('uint8')
    a[9:19, 12:25, :3] = [240, 255, 20]
    a[9:19, 12:25, 3] = 0  # transparent ink must not affect masked pixels
    images = {'a': Image.fromarray(a, 'RGBA')}
    for i in range(count):
        im = Image.new('RGBA', size, (50 + i * 8, 30 + i * 6, 80, 0))
        draw = ImageDraw.Draw(im)
        draw.ellipse((4 + i, 3, 52 + i, 51), fill=(170, 120 + i * 7, 20, 160))
        draw.rectangle((25, 9 + i, 72, 42), fill=(245, 230, 240, 100 + i * 10))
        images[f'm{i}'] = im
    return images


def _near(expected, actual, *, alpha=7, rgb=8):
    a = np.asarray(expected, dtype=np.int16)
    b = np.asarray(actual, dtype=np.int16)
    difference = np.abs(a - b)
    assert difference[..., 3].max() <= alpha
    # Colours inside an entirely transparent surface are not observable.
    visible = (a[..., 3] > 16) & (b[..., 3] > 16)
    assert difference[..., :3][visible].max(initial=0) <= rgb


def _graph(nodes, output):
    return EffectGraph({'nodes': nodes, 'output': output})


@pytest.mark.parametrize('channel,combine,expand,feather,alpha_limit', [
    ('alpha', 'multiply', 0, 0, 7),
    ('luminance', 'screen', 0, 0, 7),
    ('alpha', 'subtract', 0, 0, 7),
    ('luminance', 'max', 1.5, 0, 10),
    ('alpha', 'difference', -1, 1.5, 24),
])
def test_resident_matte_two_masks_and_original_alpha(
        channel, combine, expand, feather, alpha_limit):
    images = _sources()
    nodes = [
        {'id': key, 'type': 'source'} for key in images
    ] + [
        {'id': 'fade', 'type': 'opacity', 'inputs': ['a'], 'opacity': .7},
        {'id': 'blurred', 'type': 'filter', 'inputs': ['fade'],
         'filter_radius': 1.15},
        {'id': 'matte', 'type': 'matte', 'inputs': ['blurred', 'm0', 'm1'],
         'alpha_source': 'original', 'matte_channel': channel,
         'mask_combine': combine, 'expand': expand, 'feather': feather,
         'invert': True, 'mask_opacity': .65},
    ]
    graph = _graph(nodes, 'matte')
    before = {key: im.tobytes() for key, im in images.items()}
    reference = graph.render(images)
    with _gpu() as gpu:
        actual = graph.render(images, gpu_backend=gpu, gpu_strict=True)
        _near(reference, actual, alpha=alpha_limit, rgb=12)
        assert gpu.last_transport['gpu_resident_graph'] is True
        assert gpu.last_transport['uploads'] == 3
        assert gpu.last_transport['readbacks'] == 1
        assert gpu.last_transport['resident_draws'] == resident_plan(graph)['resident_draws']
        assert graph.render(images, gpu_backend=gpu, gpu_strict=True).tobytes() == actual.tobytes()
    assert before == {key: im.tobytes() for key, im in images.items()}


def test_eight_track_masks_share_one_input_upload_each():
    images = _sources(8)
    nodes = [{'id': key, 'type': 'source'} for key in images]
    nodes.append({'id': 'out', 'type': 'matte',
                  'inputs': ['a', *(f'm{i}' for i in range(8))],
                  'mask_combine': 'screen', 'matte_channel': 'alpha'})
    graph = _graph(nodes, 'out')
    with _gpu() as gpu:
        actual = graph.render(images, gpu_backend=gpu, gpu_strict=True)
        _near(graph.render(images), actual, alpha=7, rgb=3)
        assert gpu.last_transport['uploads'] == 9
        assert gpu.last_transport['readbacks'] == 1
        assert gpu.last_transport['resident_draws'] == 2


@pytest.mark.parametrize('space,refinement,alpha_limit', [
    ('rgb', {}, 8),
    ('oklab', {'despill': .75}, 12),
    ('rgb', {'edge_expand': 1.5}, 12),
    ('rgb', {'edge_feather': 1.5}, 24),
    ('oklab', {'edge_expand': -1, 'edge_feather': 1.5, 'despill': .45}, 26),
])
def test_resident_chroma_key_refine_and_alpha_lineage(space, refinement, alpha_limit):
    images = _sources(1)
    graph = _graph([
        {'id': key, 'type': 'source'} for key in images
    ] + [
        {'id': 'blur', 'type': 'filter', 'inputs': ['a'], 'filter_radius': 1.1},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['blur'],
         'alpha_source': 'original', 'key_space': space,
         'key_colors': ['#00ff00', '#ffff00'], 'tolerance': .12,
         'softness': .20, **refinement},
        {'id': 'copy', 'type': 'copy', 'inputs': ['a']},
        {'id': 'out', 'type': 'transition', 'inputs': ['key', 'copy'],
         'transition_style': 'dissolve', 'progress': .4},
    ], 'out')
    with _gpu() as gpu:
        actual = graph.render(images, gpu_backend=gpu, gpu_strict=True)
        _near(graph.render(images), actual, alpha=alpha_limit, rgb=16)
        assert gpu.last_transport['uploads'] == 1
        assert gpu.last_transport['readbacks'] == 1
        assert gpu.last_transport['resident_draws'] == resident_plan(graph)['resident_draws']


def test_eight_key_colours_and_native_control_override():
    images = _sources(1)
    colours = ['#00ff00', '#ff0000', '#0000ff', '#00ffff', '#ff00ff',
               '#ffff00', '#ffffff', '#101010']
    graph = _graph([
        {'id': 'a', 'type': 'source'},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['a'],
         'key_colors': colours, 'softness': .22, 'alpha_source': 'current'},
    ], 'key')
    override = {('key', 'tolerance'): .15}
    reference = graph.render(images, parameter_values=override)
    with _gpu() as gpu:
        actual = graph.render(images, gpu_backend=gpu, gpu_strict=True,
                              parameter_values=override)
        _near(reference, actual, alpha=10, rgb=2)
        assert gpu.last_transport['uploads'] == 1
        assert gpu.last_transport['readbacks'] == 1
    assert graph.nodes['key'].config.get('tolerance', .1) == .1


def test_bounded_edge_decontamination_has_cpu_gpu_parity():
    graph = _graph([
        {'id': 'a', 'type': 'source'},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['a'],
         'key_color': '#00ff00', 'tolerance': .12, 'softness': .2,
         'edge_decontaminate': .65, 'edge_radius': 3},
    ], 'key')
    sources = {'a': _sources(1)['a']}
    with _gpu() as gpu:
        actual = graph.render(sources, gpu_backend=gpu, gpu_strict=True)
        _near(graph.render(sources), actual, alpha=10, rgb=22)
        assert gpu.last_transport['uploads'] == 1
        assert gpu.last_transport['readbacks'] == 1
        assert gpu.last_transport['resident_draws'] == 6


def test_oversize_edge_decontamination_rejects_before_upload():
    graph = _graph([
        {'id': 'a', 'type': 'source'},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['a'],
         'edge_decontaminate': .5, 'edge_radius': 16},
    ], 'key')
    sources = {'a': _sources(1)['a']}
    with _gpu() as gpu:
        with pytest.raises(GPUUnsupported):
            graph.render(sources, gpu_backend=gpu, gpu_strict=True)
        assert gpu.last_transport['uploads'] == 0
        automatic = graph.render(sources, gpu_backend=gpu)
        assert automatic.tobytes() == graph.render(sources).tobytes()
        assert gpu.last_transport['uploads'] == 0


def test_mixed_cpu_gpu_graph_executes_two_islands_with_transfer_telemetry():
    images = {'a': _sources(1)['a']}
    graph = _graph([
        {'id': 'a', 'type': 'source'},
        {'id': 'blur', 'type': 'filter', 'inputs': ['a'], 'filter_radius': 1.1},
        {'id': 'warp', 'type': 'spatial_warp', 'inputs': ['blur'],
         'warp_type': 'wave', 'warp_amplitude': [2, 0], 'warp_scale': [23, 23]},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['warp'],
         'key_color': '#00ff00', 'tolerance': .1, 'softness': .2},
        {'id': 'out', 'type': 'opacity', 'inputs': ['key'], 'opacity': .8},
    ], 'out')
    reference = graph.render(images)
    with _gpu() as gpu:
        actual = graph.render(images, gpu_backend=gpu)
        _near(reference, actual, alpha=12, rgb=14)
        report = gpu.last_transport
        assert report['gpu_resident_graph'] is False
        assert report['gpu_island_count'] == 2
        assert [row['members'] for row in report['gpu_islands']] == [
            ['blur'], ['key', 'out']]
        assert report['uploads'] == report['readbacks'] == 2
        assert report['gpu_island_nodes'] == 3
        assert gpu.last_dispatch['cpu_fallbacks'] == 1


def test_resident_budget_rejected_without_upload():
    graph = _graph([
        {'id': 'a', 'type': 'source'},
        {'id': 'mask', 'type': 'source'},
        {'id': 'out', 'type': 'matte', 'inputs': ['a', 'mask'],
         'feather': 2.5},
    ], 'out')
    images = {'a': _sources(1)['a'], 'mask': _sources(1)['m0']}
    with _gpu(max_working_set_bytes=83 * 59 * 4 * 2) as gpu:
        with pytest.raises(GPUUnsupported):
            graph.render(images, gpu_backend=gpu, gpu_strict=True)
        assert gpu.last_transport['uploads'] == 0
        observed = graph.render(images, gpu_backend=gpu)
        assert observed.tobytes() == graph.render(images).tobytes()
        assert gpu.last_transport['readbacks'] == 0
