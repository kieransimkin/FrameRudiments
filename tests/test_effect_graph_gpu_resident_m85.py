"""M8.5 device-free scheduling, budget and fallback regression checks."""
from types import SimpleNamespace

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_gpu_resident import resident_plan, render_resident, supported


def _graph(*nodes, output):
    return EffectGraph({'nodes': list(nodes), 'output': output})


def _source(key):
    return {'id': key, 'type': 'source'}


def _inputs(graph, size=(16, 12)):
    return {key: Image.new('RGBA', size, (40, 60, 90, 210)) for key in graph.source_ids}


def test_identity_aliases_do_not_allocate_targets():
    graph = _graph(_source('a'),
                   {'id': 'copy1', 'type': 'copy', 'inputs': ['a']},
                   {'id': 'copy2', 'type': 'copy', 'inputs': ['copy1']},
                   {'id': 'out', 'type': 'chromatic_displacement', 'inputs': ['copy2']},
                   output='out')
    assert supported(graph, {})
    assert resident_plan(graph)['resident_draws'] == 0
    assert resident_plan(graph)['resident_peak_allocated_surfaces'] == 1


def test_multipass_filter_and_fanout_bounded_allocation():
    graph = _graph(
        _source('a'), _source('b'),
        {'id': 'blur', 'type': 'filter', 'inputs': ['a'],
         'filter_type': 'blur', 'filter_radius': 2},
        {'id': 'bloom', 'type': 'filter', 'inputs': ['a'],
         'filter_type': 'bloom', 'filter_radius': 1.5},
        {'id': 'move', 'type': 'chromatic_displacement', 'inputs': ['blur'],
         'red_offset': [1.5, -.5]},
        {'id': 'mix', 'type': 'transition', 'inputs': ['move', 'bloom'], 'progress': .4},
        {'id': 'out', 'type': 'composite', 'inputs': ['mix', 'b']},
        output='out',
    )
    p = resident_plan(graph)
    assert supported(graph, {})
    # Three blur passes, four bloom passes, displacement, transition, composite.
    assert p['resident_draws'] == 10
    assert p['resident_peak_allocated_surfaces'] >= p['resident_peak_live_surfaces']
    assert p['resident_peak_pool_surfaces'] >= 1
    assert p == resident_plan(graph)  # deterministic planning, no GPU required


def test_unsupported_node_never_impersonates_gpu_execution():
    graph = _graph(_source('a'),
                   {'id': 'key', 'type': 'chroma_key', 'inputs': ['a']}, output='key')
    assert not supported(graph, {})
    with pytest.raises(GPUUnsupported):
        resident_plan(graph)


class _Texture:
    def __init__(self):
        self.released = False

    def use(self, *, location):
        assert not self.released

    def release(self):
        assert not self.released, 'resource double release'
        self.released = True


class _Framebuffer(_Texture):
    def use(self):
        assert not self.released


class _FakeBackend:
    def __init__(self, *, budget=64, fail_shader=False):
        self.budget = budget
        self.fail_shader = fail_shader
        self.last_transport = {}
        self.last_dispatch = {}
        self.resources = []
        self._moderngl = SimpleNamespace(BLEND=1, TRIANGLES=2)
        self.ctx = SimpleNamespace(viewport=None, disable=lambda _: None,
                                   framebuffer=self._framebuffer)

    def _input(self, image):
        tex = _Texture()
        self.resources.append(tex)
        return tex

    def _target(self, size):
        tex, fb = _Texture(), _Framebuffer()
        self.resources.extend((tex, fb))
        return tex, fb

    def _framebuffer(self, *, color_attachments):
        fb = _Framebuffer()
        self.resources.append(fb)
        return fb

    def _check_budget(self, size, texture_count):
        if texture_count > self.budget:
            raise GPUUnsupported('test budget exceeded')

    def _get_shader(self, name, fragment):
        if self.fail_shader:
            raise RuntimeError('simulated shader compilation failure')
        class Shader(dict):
            def __contains__(self, key):
                return True

            def __getitem__(self, key):
                return SimpleNamespace(value=None)
        return Shader(), SimpleNamespace(render=lambda **_: None)

    def _readback_rgba(self, framebuffer, size):
        return Image.new('RGBA', size)


def test_real_scheduler_matches_dry_plan_and_releases_every_resource():
    graph = _graph(
        _source('a'), _source('b'), _source('c'),
        {'id': 'blur', 'type': 'filter', 'inputs': ['a']},
        {'id': 'clone', 'type': 'copy', 'inputs': ['a']},
        {'id': 'out', 'type': 'composite',
         'inputs': ['blur', 'b', 'clone', 'c'], 'opacity': .8},
        output='out',
    )
    backend = _FakeBackend()
    output = render_resident(backend, graph, _inputs(graph), {})
    assert output.size == (16, 12)
    assert backend.last_transport['resident_draws'] == resident_plan(graph)['resident_draws']
    assert backend.last_transport['resident_peak_allocated_surfaces'] == resident_plan(graph)[
        'resident_peak_allocated_surfaces']
    assert backend.last_transport['resident_estimated_texture_bytes'] == (
        backend.last_transport['resident_peak_allocated_surfaces'] * 16 * 12 * 4)
    assert backend.resources and all(r.released for r in backend.resources)


def test_budget_preflight_rejects_before_upload_or_shader_compilation():
    graph = _graph(_source('a'),
                   {'id': 'blur', 'type': 'filter', 'inputs': ['a']}, output='blur')
    backend = _FakeBackend(budget=1)
    with pytest.raises(GPUUnsupported, match='budget'):
        render_resident(backend, graph, _inputs(graph), {})
    assert not backend.resources


def test_shader_failure_releases_partial_resources():
    graph = _graph(_source('a'),
                   {'id': 'blur', 'type': 'filter', 'inputs': ['a']}, output='blur')
    backend = _FakeBackend(fail_shader=True)
    with pytest.raises(RuntimeError, match='shader'):
        render_resident(backend, graph, _inputs(graph), {})
    assert backend.resources and all(r.released for r in backend.resources)


def test_auto_resident_budget_failure_falls_back_to_cpu_without_uploads():
    graph = _graph(_source('a'),
                   {'id': 'out', 'type': 'opacity', 'inputs': ['a'], 'opacity': .5},
                   output='out')
    class Reject:
        def __init__(self):
            self.last_dispatch = {}
            self.last_transport = {}

        def begin_transfer_sample(self):
            self.last_transport = {'uploads': 0, 'readbacks': 0}

        def render_resident_graph(self, *_):
            raise GPUUnsupported('resident budget rejected')

    backend = Reject()
    sources = _inputs(graph)
    expected = graph.render(sources)
    assert graph.render(sources, gpu_backend=backend).tobytes() == expected.tobytes()
    assert backend.last_transport['uploads'] == 0
    assert backend.last_dispatch['cpu_fallbacks'] == 1
    with pytest.raises(GPUUnsupported):
        graph.render(sources, gpu_backend=backend, gpu_strict=True)
