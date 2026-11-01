"""M8.6 real ModernGL/EGL parity and residency gates (required, never skip in CI)."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
from framerudiments.effect_graph_gpu_resident import resident_plan


def _sample():
    image = Image.new('RGBA', (83, 59), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rectangle((2, 3, 25, 37), fill=(250, 8, 4, 230))
    d.ellipse((28, 9, 62, 48), fill=(20, 244, 16, 175))
    d.polygon(((65, 2), (79, 41), (57, 57)), fill=(20, 5, 244, 235))
    # Fully transparent hidden ink must not leak onto visible results.
    image.putpixel((47, 53), (250, 250, 13, 0))
    return image


def _bands(space='rgb', count=2):
    colours = ('#ff0000', '#00ff00', '#0000ff')
    return [
        {'id': f'b{i}', 'source_color': colours[i % 3],
         'target_color': '#d923bc', 'space': space, 'offset': [i + 1.25, -.5 - i],
         'recolor': .6, 'opacity': .72, 'tolerance': .11, 'softness': .14}
        for i in range(count)
    ]


def _graph(mode='overlay', space='rgb', count=2, *, fork=True):
    nodes = [
        {'id': 'a', 'type': 'source'},
        {'id': 'colours', 'type': 'spectral_displacement', 'inputs': ['a'],
         'spectral_mode': mode, 'bands': _bands(space, count),
         'alpha_mode': 'source', 'mix': .8},
    ]
    if fork:
        nodes += [
            {'id': 'unmodified', 'type': 'copy', 'inputs': ['a']},
            {'id': 'out', 'type': 'transition', 'inputs': ['unmodified', 'colours'],
             'progress': .6, 'feather': .1, 'transition_style': 'radial'},
        ]
    return EffectGraph({'nodes': nodes, 'output': 'out' if fork else 'colours'})


def _near(cpu, gpu, *, visible_limit=13, alpha_limit=6):
    a = np.asarray(cpu, dtype=np.int16)
    b = np.asarray(gpu, dtype=np.int16)
    delta = np.abs(a - b)
    assert int(delta[..., 3].max()) <= alpha_limit
    visible = (a[..., 3] > 8) & (b[..., 3] > 8)
    assert int(delta[..., :3][visible].max(initial=0)) <= visible_limit


@pytest.mark.parametrize('space', ['rgb', 'hsv', 'oklab'])
@pytest.mark.parametrize('mode', ['overlay', 'replace'])
def test_real_egl_spectral_graph_forks_and_single_readback(space, mode):
    graph = _graph(mode, space)
    source = _sample()
    original_bytes = source.tobytes()
    expected = graph.render({'a': source})
    with GLMatteBackend(backend='egl') as gpu:
        actual = graph.render({'a': source}, gpu_backend=gpu, gpu_strict=True)
        _near(expected, actual)
        assert source.tobytes() == original_bytes
        report = gpu.last_transport
        assert report['gpu_resident_graph'] is True
        assert report['uploads'] == 1
        assert report['readbacks'] == 1
        assert report['resident_draws'] == resident_plan(graph)['resident_draws']
        assert report['resident_peak_allocated_surfaces'] == resident_plan(graph)[
            'resident_peak_allocated_surfaces']
        assert graph.render({'a': source}, gpu_backend=gpu, gpu_strict=True).tobytes() == actual.tobytes()


def test_eight_bands_bounded_ping_pong_and_native_controls():
    graph = _graph('replace', 'oklab', count=8, fork=False)
    source = _sample()
    overrides = {('colours', 'bands.b3.offset.x'): -3.25,
                 ('colours', 'bands.b4.recolor'): .25}
    cpu = graph.render({'a': source}, parameter_values=overrides)
    with GLMatteBackend(backend='egl') as gpu:
        observed = graph.render({'a': source}, parameter_values=overrides,
                                gpu_backend=gpu, gpu_strict=True)
        _near(cpu, observed)
        assert gpu.last_transport['uploads'] == 1
        assert gpu.last_transport['readbacks'] == 1
        assert gpu.last_transport['resident_draws'] == 10
        assert gpu.last_transport['resident_peak_allocated_surfaces'] == 3
        assert graph.nodes['colours'].config['bands'][3]['offset'][0] == 4.25


def test_spectral_budget_strict_preflight_and_auto_fallback():
    graph = _graph(fork=False)
    source = _sample()
    # A spectral render needs one immutable source and two alternating targets.
    pixels = source.width * source.height * 4
    with GLMatteBackend(backend='egl', max_working_set_bytes=pixels * 2) as gpu:
        with pytest.raises(GPUUnsupported):
            graph.render({'a': source}, gpu_backend=gpu, gpu_strict=True)
        assert gpu.last_transport['uploads'] == 0
        assert gpu.last_transport['readbacks'] == 0
        cpu = graph.render({'a': source})
        automatic = graph.render({'a': source}, gpu_backend=gpu)
        assert automatic.tobytes() == cpu.tobytes()
        assert gpu.last_transport['uploads'] == 0
        assert gpu.last_dispatch['cpu_fallbacks'] == 1


def test_spectral_shader_failure_does_not_poison_context(monkeypatch):
    graph = _graph(fork=False)
    image = _sample()
    with GLMatteBackend(backend='egl') as gpu:
        original = gpu._get_shader
        def fail(name, fragment):
            if name == 'm86_spectral_band':
                raise RuntimeError('injected spectral compilation failure')
            return original(name, fragment)
        monkeypatch.setattr(gpu, '_get_shader', fail)
        with pytest.raises(RuntimeError, match='injected spectral'):
            graph.render({'a': image}, gpu_backend=gpu, gpu_strict=True)
        monkeypatch.setattr(gpu, '_get_shader', original)
        output = graph.render({'a': image}, gpu_backend=gpu, gpu_strict=True)
        _near(graph.render({'a': image}), output)
        assert gpu.last_transport['uploads'] == 1
        assert gpu.last_transport['readbacks'] == 1
