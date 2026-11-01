"""M8.6 closed-DAG spectral residency, resource planning and safe failure paths."""
from types import SimpleNamespace

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_gpu_resident import resident_plan, render_resident, supported


def _bands(count=2):
    return [
        {'id': f'band{i}', 'source_color': '#ff0000' if i % 2 else '#00ff00',
         'target_color': '#3366cc', 'space': ('rgb', 'hsv', 'oklab')[i % 3],
         'offset': [i + .25, -i - .5], 'recolor': .65,
         'tolerance': .08, 'softness': .12, 'opacity': .8}
        for i in range(count)
    ]


def _graph(*, count=2, mode='replace', mix=.8, fork=False):
    nodes = [{'id': 's', 'type': 'source'},
             {'id': 'spectral', 'type': 'spectral_displacement', 'inputs': ['s'],
              'bands': _bands(count), 'spectral_mode': mode, 'alpha_mode': 'source',
              'mix': mix}]
    if fork:
        nodes += [{'id': 'clean', 'type': 'copy', 'inputs': ['s']},
                  {'id': 'mix', 'type': 'transition', 'inputs': ['spectral', 'clean'],
                   'progress': .45}]
    return EffectGraph({'nodes': nodes, 'output': 'mix' if fork else 'spectral'})


def test_plan_is_bounded_independent_of_band_count_and_colour_space():
    for mode in ('replace', 'overlay'):
        for count in (1, 2, 8):
            graph = _graph(count=count, mode=mode)
            assert supported(graph, {})
            plan = resident_plan(graph)
            assert plan['resident_draws'] == count + 2
            assert plan['resident_peak_allocated_surfaces'] == 3  # 1 input + 2 reused targets
            assert plan['resident_peak_live_surfaces'] <= 3
            assert plan == resident_plan(graph)


def test_exact_zero_mix_alias_and_fork_immutability():
    graph = _graph(count=8, mix=0)
    assert supported(graph, {})
    assert resident_plan(graph)['resident_draws'] == 0
    assert resident_plan(graph)['resident_peak_allocated_surfaces'] == 1
    forked = _graph(fork=True)
    assert resident_plan(forked)['resident_draws'] == 5  # 2 bands + base/final + transition
    assert resident_plan(forked)['resident_peak_allocated_surfaces'] <= 4


def test_invalid_spectral_override_fails_before_gpu_work():
    graph = _graph()
    malformed = {'spectral': {**graph.nodes['spectral'].config, 'mix': float('nan')}}
    with pytest.raises(ValueError, match='spectral mix'):
        resident_plan(graph, malformed)


class _Resource:
    def __init__(self):
        self.released = False

    def release(self):
        assert not self.released, 'double resource release'
        self.released = True

    def use(self, **_):
        assert not self.released


class _Uniform:
    def __init__(self):
        self.value = None
        self.data = None

    def write(self, data):
        self.data = data


class _Program(dict):
    def __missing__(self, name):
        self[name] = _Uniform()
        return self[name]

    def __contains__(self, name):
        return True


class _FakeGPU:
    def __init__(self, max_surfaces=32, fail_on=None):
        self.max_surfaces = max_surfaces
        self.fail_on = fail_on
        self.resources = []
        self.programs = {}
        self.drawn = []
        self.uploaded = 0
        self.last_transport = {}
        self.last_dispatch = {}
        self._moderngl = SimpleNamespace(BLEND=1, TRIANGLES=2)
        self.ctx = SimpleNamespace(viewport=None, disable=lambda _: None,
                                   framebuffer=self._framebuffer)

    def _framebuffer(self, *, color_attachments):
        framebuffer = _Resource()
        self.resources.append(framebuffer)
        return framebuffer

    def _input(self, image):
        tex = _Resource()
        self.resources.append(tex)
        self.uploaded += 1
        return tex

    def _target(self, size):
        tex, fb = _Resource(), _Resource()
        self.resources.extend((tex, fb))
        return tex, fb

    def _check_budget(self, size, count):
        if count > self.max_surfaces:
            raise GPUUnsupported('insufficient simulated texture budget')

    def _get_shader(self, name, source):
        if name == self.fail_on:
            raise RuntimeError('synthetic shader failure')
        if name not in self.programs:
            program = _Program()
            def render(**_):
                self.drawn.append((name, {key: (val.value, val.data)
                                          for key, val in program.items()}))
            self.programs[name] = (program, SimpleNamespace(render=render))
        return self.programs[name]

    def _readback_rgba(self, framebuffer, size):
        return Image.new('RGBA', size)


def test_real_scheduler_fake_gpu_shader_metadata_and_release():
    graph = _graph(count=3, fork=True)
    fake = _FakeGPU()
    source = Image.new('RGBA', (13, 9), (23, 56, 245, 180))
    unchanged = source.tobytes()
    result = render_resident(fake, graph, {'s': source}, {})
    assert result.size == source.size
    assert source.tobytes() == unchanged
    assert fake.uploaded == 1
    assert all(resource.released for resource in fake.resources)
    assert fake.last_transport['resident_draws'] == 6
    assert len(fake.drawn) == 6
    assert fake.last_transport['resident_peak_allocated_surfaces'] == resident_plan(graph)[
        'resident_peak_allocated_surfaces']
    base = fake.programs['m86_spectral_base'][0]
    assert len(base['source_colours'].data) == 128
    assert len(base['controls'].data) == 128
    band_passes = [row for name, row in fake.drawn if name == 'm86_spectral_band']
    assert len(band_passes) == 3
    assert [p['offset'][0] for p in band_passes] == [(.25, -.5), (1.25, -1.5), (2.25, -2.5)]
    assert fake.last_transport['resident_estimated_texture_bytes'] == (
        fake.last_transport['resident_peak_allocated_surfaces'] * 13 * 9 * 4)


def test_zero_mix_no_spectral_shader_and_budget_rejection_before_upload():
    graph = _graph(mix=0)
    fake = _FakeGPU(max_surfaces=1)
    render_resident(fake, graph, {'s': Image.new('RGBA', (4, 4))}, {})
    assert fake.uploaded == 1 and fake.drawn == []
    assert all(r.released for r in fake.resources)
    high = _graph(count=8)
    rejected = _FakeGPU(max_surfaces=2)
    with pytest.raises(GPUUnsupported, match='budget'):
        render_resident(rejected, high, {'s': Image.new('RGBA', (4, 4))}, {})
    assert rejected.uploaded == 0 and rejected.resources == []


@pytest.mark.parametrize('failure', ['m86_spectral_base', 'm86_spectral_band',
                                     'm86_spectral_final'])
def test_shader_failure_releases_all_resources(failure):
    graph = _graph()
    fake = _FakeGPU(fail_on=failure)
    with pytest.raises(RuntimeError, match='synthetic shader failure'):
        render_resident(fake, graph, {'s': Image.new('RGBA', (4, 4))}, {})
    assert fake.resources and all(r.released for r in fake.resources)


def test_native_band_control_is_resolved_before_resident_dispatch():
    graph = _graph()
    source = Image.new('RGBA', (4, 3), (255, 0, 0, 255))

    class Spy:
        last_dispatch = {}
        last_transport = {}

        def begin_transfer_sample(self):
            self.last_transport = {}

        def render_resident_graph(self, supplied, sources, configs):
            assert configs['spectral']['bands'][0]['offset'][0] == 2.5
            assert supplied is graph
            assert sources['s'] is source
            return source.copy()

    observed = graph.render({'s': source}, gpu_backend=Spy(),
                            parameter_values={('spectral', 'bands.band0.offset.x'): 2.5})
    assert observed.tobytes() == source.tobytes()
    assert graph.nodes['spectral'].config['bands'][0]['offset'][0] == .25
