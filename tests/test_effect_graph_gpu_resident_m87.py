"""M8.7 CPU-only device-independent resident matte/key and lineage regressions."""
from types import SimpleNamespace

import pytest
from PIL import Image

from framerudiments.effect_graph import EffectGraph
from framerudiments.effect_graph_gpu import GPUUnsupported
from framerudiments.effect_graph_gpu_resident import (
    KEY_FINAL_RESIDENT, MATTE_FINAL_RESIDENT, resident_plan, render_resident, supported,
)


def _graph(*nodes, output):
    return EffectGraph({'nodes': list(nodes), 'output': output})


def _src(name):
    return {'id': name, 'type': 'source'}


def _images(graph, size=(29, 19)):
    return {name: Image.new('RGBA', size, (28, 151, 34, 194))
            for name in graph.source_ids}


def _matte_graph(mask_count=2, **config):
    masks = [f'm{i}' for i in range(mask_count)]
    return _graph(
        _src('ink'), *(_src(name) for name in masks),
        {'id': 'cutout', 'type': 'matte', 'inputs': ['ink', *masks], **config},
        output='cutout',
    )


def _key_graph(**config):
    return _graph(
        _src('ink'),
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['ink'], **config},
        output='key',
    )


@pytest.mark.parametrize('count', [1, 2, 8])
@pytest.mark.parametrize('channel', ['alpha', 'luminance'])
def test_multi_input_matte_has_bounded_no_transfer_plan(count, channel):
    graph = _matte_graph(count, matte_channel=channel, mask_combine='screen',
                         alpha_source='original', expand=1.5, feather=1.3)
    assert supported(graph, {})
    plan = resident_plan(graph)
    assert plan['resident_draws'] == 6  # mask, morph H/V, feather H/V, final
    assert plan['resident_peak_allocated_surfaces'] <= count + 4
    assert plan == resident_plan(graph)


@pytest.mark.parametrize('space', ['rgb', 'oklab'])
def test_key_supported_with_refinement_and_despill(space):
    graph = _key_graph(key_space=space, key_colors=['#00ff00', '#ff00ff'],
                       despill=.65, edge_expand=-1.5, edge_feather=1.5)
    assert supported(graph, {})
    assert resident_plan(graph)['resident_draws'] == 6
    assert 'texelFetch(original_tex,pos,0).a' in KEY_FINAL_RESIDENT
    assert 'texelFetch(original_tex,pos,0).a' in MATTE_FINAL_RESIDENT


def test_rejected_refinement_does_not_masquerade_as_gpu_residency():
    graphs = [
        _key_graph(edge_decontaminate=.8, edge_radius=16),
        _key_graph(edge_feather=16),
        _key_graph(edge_expand=20),
        _matte_graph(expand=-20),
        _matte_graph(feather=32),
    ]
    for graph in graphs:
        assert not supported(graph, {})
        with pytest.raises(GPUUnsupported, match='unsupported'):
            resident_plan(graph)


def test_bounded_decontamination_adds_four_resident_passes():
    graph = _key_graph(edge_decontaminate=.8, edge_radius=3)
    assert supported(graph, {})
    assert resident_plan(graph)['resident_draws'] == 6


class _Texture:
    def __init__(self, backend):
        self.backend = backend
        self.released = False
        self.generation = 0

    def use(self, *, location):
        assert not self.released
        self.backend.bound[location] = self

    def release(self):
        assert not self.released, 'resource double-release'
        self.released = True


class _Framebuffer(_Texture):
    def __init__(self, backend, texture=None):
        super().__init__(backend)
        self.texture = texture

    def use(self):
        assert not self.released
        self.backend.active_target = self.texture


class _Uniform:
    def __init__(self):
        self.value = None
        self.data = None

    def write(self, data):
        self.data = data


class _Program(dict):
    def __contains__(self, key):
        return True

    def __missing__(self, key):
        self[key] = _Uniform()
        return self[key]


class _Backend:
    def __init__(self, max_surfaces=50, fail_on=None):
        self.max_surfaces = max_surfaces
        self.fail_on = fail_on
        self.resources = []
        self.sources = {}
        self.programs = {}
        self.passes = []
        self.bound = {}
        self.active_target = None
        self.last_transport = {}
        self.last_dispatch = {}
        self._moderngl = SimpleNamespace(BLEND=1, TRIANGLES=2)
        self.ctx = SimpleNamespace(viewport=None, disable=lambda _: None,
                                   framebuffer=self._framebuffer)

    def _check_budget(self, size, count):
        if count > self.max_surfaces:
            raise GPUUnsupported('simulated GPU budget insufficient')

    def _input(self, image):
        texture = _Texture(self)
        texture.generation = 1
        self.resources.append(texture)
        self.sources[id(texture)] = image
        return texture

    def _target(self, size):
        texture = _Texture(self)
        fb = _Framebuffer(self, texture)
        self.resources.extend((texture, fb))
        return texture, fb

    def _framebuffer(self, *, color_attachments):
        fb = _Framebuffer(self)
        self.resources.append(fb)
        return fb

    def _get_shader(self, name, code):
        if name == self.fail_on:
            raise RuntimeError('injected shader error')
        if name not in self.programs:
            program = _Program()

            def draw(**_):
                # Track the exact input texture/generation at shader time.
                self.passes.append((name, {
                    unit: (tex, tex.generation)
                    for unit, tex in self.bound.items()
                }, self.active_target))
                self.active_target.generation += 1

            vao = SimpleNamespace(render=draw)
            self.programs[name] = program, vao
        program, vao = self.programs[name]
        return program, vao

    def _readback_rgba(self, framebuffer, size):
        return Image.new('RGBA', size)


def test_source_alpha_lineage_through_multiple_branches_and_mattes():
    graph = _graph(
        _src('a'), _src('mask'),
        {'id': 'blur', 'type': 'filter', 'inputs': ['a'], 'filter_radius': 1.5},
        {'id': 'cut', 'type': 'matte', 'inputs': ['blur', 'mask'],
         'alpha_source': 'original'},
        {'id': 'key', 'type': 'chroma_key', 'inputs': ['cut'],
         'alpha_source': 'original', 'despill': .6},
        {'id': 'fork', 'type': 'copy', 'inputs': ['a']},
        {'id': 'out', 'type': 'transition', 'inputs': ['key', 'fork'], 'progress': .3},
        output='out',
    )
    backend = _Backend()
    sources = _images(graph)
    original = {key: value.tobytes() for key, value in sources.items()}
    output = render_resident(backend, graph, sources, {})
    assert output.size == (29, 19)
    assert backend.last_transport['resident_draws'] == resident_plan(graph)['resident_draws']
    for shader, bindings, _ in backend.passes:
        if shader in {'m87_matte_final', 'm87_key_final'}:
            # Sampler 2 must be the untouched source-alpha surface, even after
            # blur, matte processing and downstream chroma operations.
            assert backend.sources[id(bindings[2][0])] is sources['a']
            assert bindings[2][1] == 1
    assert all(resource.released for resource in backend.resources)
    assert {key: value.tobytes() for key, value in sources.items()} == original


def test_source_opacity_then_matte_uses_universal_alpha_lineage():
    graph = _graph(
        _src('a'), _src('mask'),
        {'id': 'soft', 'type': 'opacity', 'inputs': ['a'], 'opacity': .3},
        {'id': 'blur', 'type': 'filter', 'inputs': ['soft'], 'filter_radius': 1.5},
        {'id': 'out', 'type': 'matte', 'inputs': ['blur', 'mask'],
         'alpha_source': 'original'},
        output='out',
    )
    plan = resident_plan(graph)
    assert plan['resident_draws'] == 6  # source opacity, blur x3, mask+final
    backend = _Backend()
    render_resident(backend, graph, _images(graph), {})
    assert backend.last_transport['resident_peak_allocated_surfaces'] == (
        plan['resident_peak_allocated_surfaces'])
    assert all(r.released for r in backend.resources)


def test_gpu_budget_fails_before_upload_and_error_cleans_up():
    graph = _matte_graph(8, feather=2)
    low = _Backend(max_surfaces=2)
    with pytest.raises(GPUUnsupported, match='budget'):
        render_resident(low, graph, _images(graph), {})
    assert low.resources == []
    broken = _Backend(fail_on='m87_matte_final')
    with pytest.raises(RuntimeError, match='shader error'):
        render_resident(broken, graph, _images(graph), {})
    assert broken.resources and all(r.released for r in broken.resources)


def test_keys_are_uploaded_as_padded_uniform_vectors():
    graph = _key_graph(key_space='oklab', key_color='#00ff00',
                       key_colors=['#ff0000', '#0000ff'], despill=.7)
    backend = _Backend()
    render_resident(backend, graph, _images(graph), {})
    for key in ('m87_key_init', 'm87_key_final'):
        program, _ = backend.programs[key]
        assert len(program['keys'].data) == 128
        assert program['nkey'].value == 3
    assert all(r.released for r in backend.resources)


def test_fake_pool_never_reuses_referenced_original_alpha():
    graph = _graph(
        _src('a'), _src('b'),
        {'id': 'mix', 'type': 'transition', 'inputs': ['a', 'b'], 'progress': .7},
        {'id': 'blur', 'type': 'filter', 'inputs': ['mix'], 'filter_radius': 1.5},
        {'id': 'out', 'type': 'chroma_key', 'inputs': ['blur'],
         'alpha_source': 'original'}, output='out',
    )
    backend = _Backend()
    render_resident(backend, graph, _images(graph), {})
    transition = next(pair for kind, _, pair in backend.passes
                      if kind == 'm85_transition')
    key_final = next(bindings for kind, bindings, _ in backend.passes
                     if kind == 'm87_key_final')
    assert key_final[2][0] is transition
    assert key_final[2][1] == 1  # no intervening draw overwrote original alpha
    assert all(resource.released for resource in backend.resources)
    assert backend.last_transport['resident_peak_allocated_surfaces'] == (
        resident_plan(graph)['resident_peak_allocated_surfaces'])
