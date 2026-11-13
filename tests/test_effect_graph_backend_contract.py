"""Allocation-free M8.1 contracts; fake context metadata is NOT GPU parity."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from framerudiments.effect_graph import EffectGraph, KINDS
from framerudiments.effect_graph_backend_contract import (
    BackendPlanError, KNOWN_KINDS, PlanLimits, SurfaceContract,
    backend_registry, gpu_requirements, plan_effect_graph, rgba_surface,
)
from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
from framerudiments.effect_graph_integration import create_gpu_backend


def device(*, budget=768 * 1024**2, maximum=16384, version=330, closed=False):
    obj = object.__new__(GLMatteBackend)
    obj.ctx = SimpleNamespace(version_code=version,
                              info={'GL_MAX_TEXTURE_SIZE': maximum, 'GL_RENDERER': 'test-metadata-only'})
    obj.max_working_set_bytes = budget
    obj.closed = closed
    return obj


def unary(kind='chroma_key', **config):
    return EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
                                 {'id': 'out', 'type': kind, 'inputs': ['s'], **config}], 'output': 'out'})


def matte(**config):
    return EffectGraph({'nodes': [{'id': 's', 'type': 'source'}, {'id': 'm', 'type': 'source'},
                                 {'id': 'out', 'type': 'matte', 'inputs': ['s', 'm'], **config}], 'output': 'out'})


@pytest.mark.parametrize('size', [(1, 1), (1920, 1080), (3840, 2160), (7680, 4320)])
def test_surface_payloads(size):
    s = SurfaceContract(*size)
    assert s.nbytes == size[0] * size[1] * 4
    assert s.lineage().nbytes == size[0] * size[1]
    assert json.loads(json.dumps(s.describe()))['format'] == 'rgba8'
    assert rgba_surface(list(size)) == s
    with pytest.raises(FrozenInstanceError):
        s.width = 2


@pytest.mark.parametrize('w,h', [(0, 1), (-1, 2), (True, 2), (1.0, 2), (2, False),
                                  (float('nan'), 1), (16385, 1), (16384, 16384)])
def test_invalid_surface_dimensions(w, h):
    with pytest.raises(ValueError):
        SurfaceContract(w, h)


@pytest.mark.parametrize('kw', [dict(format='rgba16f'), dict(alpha='premultiplied'),
                                dict(colour_space='scene-linear'), dict(row_order='bottom-first'),
                                dict(residency='cuda'), dict(format='r8')])
def test_unsupported_surface_abi_is_not_invented(kw):
    with pytest.raises(ValueError):
        SurfaceContract(8, 8, **kw)


@pytest.mark.parametrize('value', [None, {}, [1], [1, 2, 3], '8x8', SurfaceContract(8, 8).lineage(),
                                   SurfaceContract(8, 8, residency='device')])
def test_current_ports_are_host_rgba(value):
    with pytest.raises(ValueError):
        rgba_surface(value)


@pytest.mark.parametrize('name', ['max_retained_host_bytes', 'max_transfer_bytes'])
@pytest.mark.parametrize('value', [0, -1, True, 3.5, float('inf'), '500'])
def test_bad_plan_limits(name, value):
    with pytest.raises(ValueError):
        PlanLimits(**{name: value})


def test_registry_covers_every_registered_kind_and_is_detached():
    assert KNOWN_KINDS == KINDS
    first = backend_registry()
    assert first['backends']['moderngl']['device']['state'] == 'not_attached'
    assert first['backends']['moderngl']['gpu_resident_graph']
    assert first['backends']['moderngl']['mixed_cpu_gpu_islands']
    first['node_kinds'].clear()
    assert backend_registry()['node_kinds']
    json.dumps(backend_registry(), allow_nan=False)


def test_registry_does_not_import_or_probe_moderngl():
    previous = sys.modules.get('moderngl')
    backend_registry()
    assert sys.modules.get('moderngl') is previous


def test_attached_context_is_not_shader_qualification():
    d = device()
    r = backend_registry(d)['backends']['moderngl']['device']
    assert r['available'] and r['shader_validation'] == 'not_performed'
    assert not d.closed
    assert backend_registry(device(closed=True))['backends']['moderngl']['device']['state'] == 'closed'
    assert not backend_registry(device(version=320))['backends']['moderngl']['device']['available']
    with pytest.raises(ValueError, match='unsupported GPU backend'):
        backend_registry(SimpleNamespace())


@pytest.mark.parametrize('kind,n,original', [('matte', 2, False), ('matte', 9, True),
                                           ('chroma_key', 1, False), ('chroma_key', 1, True)])
@pytest.mark.parametrize('morph,blur', [(0, 0), (1, 0), (0, 2), (-.5, 1.5), (16, 12)])
def test_all_live_gpu_pass_textures_are_counted(kind, n, original, morph, blur):
    cfg = ({'expand': morph, 'feather': blur} if kind == 'matte'
           else {'edge_expand': morph, 'edge_feather': blur})
    req = gpu_requirements(kind, cfg, n, original_alpha=original)
    passes = 2 + 2 * bool(morph) + 2 * bool(blur)
    assert req.supported
    assert req.texture_count == n + int(original) + passes
    assert req.upload_count == n + int(original)
    assert req.readback_count == 1 and req.draw_passes == passes


@pytest.mark.parametrize('kind,cfg,n,reason', [
    ('matte', {'expand': 16.01}, 2, 'radius_limit_exceeded'),
    ('matte', {'feather': 12.01}, 2, 'radius_limit_exceeded'),
    ('chroma_key', {'edge_expand': -17}, 1, 'radius_limit_exceeded'),
    ('chroma_key', {'edge_decontaminate': .1, 'edge_radius': 16}, 1,
     'edge_decontamination_radius_limit_exceeded'),
    ('spatial_warp', {'warp_type': 'raindrop_ripples'}, 1, 'warp_type_not_implemented'),
])
def test_shared_gpu_rejections(kind, cfg, n, reason):
    req = gpu_requirements(kind, cfg, n)
    assert not req.supported and req.reason == reason
    if kind in {'matte', 'chroma_key'}:
        with pytest.raises(GPUUnsupported, match=reason):
            GLMatteBackend._require_supported(cfg, kind)


def test_spectral_uses_four_textures_independent_of_band_count():
    for n in (1, 4, 8):
        cfg = {'bands': [{'id': f'b{i}', 'source_color': '#ff0000'} for i in range(n)]}
        req = gpu_requirements('spectral_displacement', cfg, 1)
        assert req.texture_count == 4 and req.upload_count == 1
        assert req.draw_passes == n + 2
        assert gpu_requirements('spectral_displacement', {**cfg, 'mix': 0}, 1).texture_count == 0


def test_invalid_gpu_configs_do_not_become_fallbacks():
    with pytest.raises(ValueError):
        gpu_requirements('matte', {'expand': float('nan')}, 2)
    with pytest.raises(ValueError):
        gpu_requirements('chroma_key', {}, 2)
    with pytest.raises(ValueError):
        gpu_requirements('matte', {}, 1)
    with pytest.raises(ValueError):
        gpu_requirements('matte', {}, True)


def test_cpu_plan_does_not_render_or_touch_pixels(monkeypatch):
    g = unary()
    monkeypatch.setattr(Image, 'new', lambda *a, **k: pytest.fail('image allocation during planning'))
    monkeypatch.setattr(g, 'render', lambda *a, **k: pytest.fail('planner rendered'))
    report = g.backend_plan({'s': (7680, 4320)})
    assert report['eligible'] and report['resources']['peak_device_texture_payload_bytes'] == 0
    assert report['nodes']['out']['surface']['alpha'] == 'straight'
    json.dumps(report, allow_nan=False)


def test_source_only_graph_needs_no_numpy_contract():
    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'}], 'output': 's'})
    assert g.backend_plan({'s': (8, 4)})['nodes']['s']['cpu_implementation'] == 'pillow'


def test_each_auto_fallback_and_strict_rejection():
    g = matte(expand=32)
    source = {'s': (4, 4), 'm': (4, 4)}
    auto = g.backend_plan(source, mode='auto', gpu_backend=device())
    assert auto['eligible'] and auto['nodes']['out']['decision_reason'] == 'radius_limit_exceeded'
    strict = g.backend_plan(source, mode='gpu', gpu_backend=device())
    assert not strict['eligible']
    with pytest.raises(BackendPlanError) as exc:
        g.backend_plan(source, mode='gpu', gpu_backend=device(), require=True)
    assert exc.value.report['errors'][0]['node'] == 'out'


@pytest.mark.parametrize('kind,params', [('layer_style', {}), ('spatial_warp', {'warp_type': 'raindrop_ripples'}),
                                      ('raster_effect', {}), ('procedural_field', {})])
def test_cpu_only_families_report_actual_legacy_policy(kind, params):
    g = unary(kind, **params)
    assert g.backend_plan({'s': (5, 5)}, mode='auto', gpu_backend=device())['eligible']
    assert not g.backend_plan({'s': (5, 5)}, mode='gpu', gpu_backend=device())['eligible']


def test_spatial_bridge_plan_accounts_for_original_alpha_transfers():
    g = unary('spatial_warp', warp_type='wave')
    plan = g.backend_plan({'s': (5, 7)}, mode='gpu', gpu_backend=device(), require=True)
    row = plan['nodes']['out']
    assert row['selected_backend'] == 'gpu_bridge'
    assert row['gpu_kernel']['draw_passes'] == 2
    assert row['upload_bytes'] == row['readback_bytes'] == row['gpu_texture_payload_bytes'] == 5 * 7 * 4 * 2


@pytest.mark.parametrize('kind', ['copy', 'opacity', 'chromatic_displacement'])
def test_legacy_host_operations_are_not_falsely_labelled_gpu(kind):
    p = unary(kind).backend_plan({'s': (4, 4)}, mode='gpu', gpu_backend=device())
    assert p['eligible']
    assert p['nodes']['out']['selected_backend'] == 'cpu'
    assert p['resources']['upload_bytes'] == 0 and not p['gpu_resident_graph']


@pytest.mark.parametrize('style,strict_ok', [('dissolve', True), ('wipe', True), ('radial', True),
                                          ('liquid', False), ('rotate', False)])
def test_transition_backend_policy_matches_existing_executor(style, strict_ok):
    g = EffectGraph({'nodes': [{'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
                              {'id': 'out', 'type': 'transition', 'inputs': ['a', 'b'],
                               'transition_style': style}], 'output': 'out'})
    p = g.backend_plan({'a': (9, 8), 'b': (9, 8)}, mode='gpu', gpu_backend=device())
    assert p['eligible'] == strict_ok
    assert p['nodes']['out']['selected_backend'] == 'cpu'


def test_unavailable_and_closed_devices_report_honestly():
    g = unary()
    for d in (None, device(closed=True), device(version=320)):
        assert g.backend_plan({'s': (8, 8)}, mode='auto', gpu_backend=d)['eligible']
        assert not g.backend_plan({'s': (8, 8)}, mode='gpu', gpu_backend=d)['eligible']


def test_texture_dimension_and_payload_limits():
    g = matte(expand=1, feather=1)
    sizes = {'s': (4, 4), 'm': (4, 4)}
    for d, reason in [(device(maximum=3), 'texture_dimension_limit'),
                      (device(budget=8 * 4 * 4 * 4), 'texture_payload_budget')]:
        p = g.backend_plan(sizes, mode='auto', gpu_backend=d)
        assert p['nodes']['out']['decision_reason'] == reason
        assert p['resources']['upload_bytes'] == 0
    p = g.backend_plan(sizes, mode='auto', gpu_backend=device(budget=9 * 4 * 4 * 4))
    assert p['nodes']['out']['selected_backend'] == 'gpu_bridge'
    assert p['resources']['peak_device_texture_payload_bytes'] == 9 * 4 * 4 * 4
    assert p['resources']['upload_bytes'] == 3 * 4 * 4 * 4
    assert p['resources']['readback_bytes'] == 4 * 4 * 4


def test_multiple_gpu_nodes_include_redundant_transfers_not_invented_caching():
    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
                              {'id': 'a', 'type': 'chroma_key', 'inputs': ['s']},
                              {'id': 'b', 'type': 'chroma_key', 'inputs': ['a']}], 'output': 'b'})
    p = g.backend_plan({'s': (8, 8)}, mode='auto', gpu_backend=device())
    assert p['resources']['upload_bytes'] == 4 * 8 * 8 * 4
    assert p['resources']['readback_bytes'] == 2 * 8 * 8 * 4
    assert not p['resources']['texture_pooling']


def test_forks_and_alpha_alias_lifetimes():
    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
                              {'id': 'a', 'type': 'copy', 'inputs': ['s']},
                              {'id': 'b', 'type': 'opacity', 'inputs': ['s'], 'opacity': .5},
                              {'id': 'out', 'type': 'composite', 'inputs': ['a', 'b']}], 'output': 'out'})
    p = g.backend_plan({'s': (10, 10)})
    assert p['nodes']['s']['lifetime']['consumer_edges'] == 2
    assert p['nodes']['s']['lifetime']['last_consumer_step'] == 2
    assert p['alpha_lineages']['s']['last_consumer_step'] == 3
    assert p['alpha_lineages']['s']['aliases'] == ['s', 'a', 'b']
    assert p['resources']['retained_lineage_bytes'] == 200
    assert p['resources']['retained_host_payload_bytes'] == 2600
    assert p['nodes']['out']['lifetime']['last_consumer_step'] == 4


@pytest.mark.parametrize('kind', ['spatial_warp', 'raster_effect', 'procedural_field'])
def test_geometry_and_generated_alpha_are_new_owners(kind):
    p = unary(kind).backend_plan({'s': (8, 8)})
    assert p['nodes']['out']['alpha_owner'] == 'out'
    assert len(p['alpha_lineages']) == 2


def test_duplicate_edges_count_for_uploads_and_consumers():
    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
                              {'id': 'out', 'type': 'matte', 'inputs': ['s', 's', 's']}], 'output': 'out'})
    p = g.backend_plan({'s': (4, 4)}, mode='auto', gpu_backend=device())
    assert p['nodes']['s']['lifetime']['consumer_edges'] == 3
    assert p['nodes']['out']['gpu_kernel']['upload_count'] == 4


def test_topology_dimensions_and_unreachable_sources():
    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'}, {'id': 'unused', 'type': 'source'}], 'output': 's'})
    p = g.backend_plan({'s': (4, 4)})
    assert p['unreachable_nodes'] == ['unused']
    with pytest.raises(ValueError, match='missing source'):
        g.backend_plan({})
    with pytest.raises(ValueError, match='dimensions'):
        matte().backend_plan({'s': (4, 4), 'm': (5, 4)})


def test_texture_overlay_allows_a_different_size():
    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'}, {'id': 't', 'type': 'source'},
                              {'id': 'out', 'type': 'layer_style', 'style_type': 'pattern_overlay',
                               'pattern_kind': 'texture', 'inputs': ['s', 't']}], 'output': 'out'})
    assert g.backend_plan({'s': (16, 8), 't': (2, 2)})['nodes']['out']['surface']['width'] == 16


def test_memory_and_transfer_budget_errors_are_detached():
    g = matte()
    p = g.backend_plan({'s': (8, 8), 'm': (8, 8)}, mode='auto', gpu_backend=device(),
                       limits=PlanLimits(max_retained_host_bytes=1, max_transfer_bytes=1))
    assert {x['code'] for x in p['errors']} == {'retained_host_payload_budget', 'transfer_payload_budget'}
    p['nodes'].clear()
    assert g.backend_plan({'s': (8, 8), 'm': (8, 8)})['nodes']


def test_native_resolved_parameters_change_the_decision_not_authored_graph():
    g = matte(expand=1)
    before = deepcopy(g.nodes['out'].config)
    p = g.backend_plan({'s': (8, 8), 'm': (8, 8)}, mode='auto', gpu_backend=device(),
                       parameter_values={('out', 'expand'): 32})
    assert p['nodes']['out']['selected_backend'] == 'cpu'
    assert p['parameters_resolved']
    assert g.nodes['out'].config == before
    with pytest.raises(ValueError):
        g.backend_plan({'s': (8, 8), 'm': (8, 8)}, parameter_values={('out', 'expand'): 999})


def test_control_resolution_and_render_share_timing():
    g = EffectGraph({'nodes': [{'id': 's', 'type': 'source'},
                              {'id': 'out', 'type': 'transition', 'inputs': ['s', 's'],
                               'timing': {'duration_beats': 4}}], 'output': 'out'})
    p = g.backend_plan({'s': (8, 8)}, context=SimpleNamespace(beat=2))
    assert p['nodes']['out']['parameters']['progress'] == .5
    p = g.backend_plan({'s': (8, 8)}, context=SimpleNamespace(beat=2), parameter_values={('out', 'progress'): .9})
    assert p['nodes']['out']['parameters']['progress'] == .9


def test_planner_leaves_render_report_and_pixels_unchanged():
    g = unary('layer_style', style_type='outline', style_width=2)
    s = Image.new('RGBA', (10, 10))
    s.paste((200, 30, 10, 128), (2, 2, 8, 8))
    before = g.render({'s': s}).tobytes()
    g._style_render_report = {'sentinel': 1}
    g.backend_plan({'s': s.size})
    assert g._style_render_report == {'sentinel': 1}
    assert g.render({'s': s}).tobytes() == before


def test_factory_retains_missing_context_reason(monkeypatch):
    import framerudiments.effect_graph_gpu as module
    def fail():
        raise RuntimeError('EGL unavailable for test')
    monkeypatch.setattr(module, 'GLMatteBackend', fail)
    r = {'stale': True}
    assert create_gpu_backend('cpu', report=r) is None and r['state'] == 'cpu_requested'
    assert create_gpu_backend('auto', report=r) is None
    assert r['state'] == 'unavailable' and r['fallback_reason'] == 'EGL unavailable for test'
    with pytest.raises(RuntimeError):
        create_gpu_backend('gpu', report=r)
    assert r['error_type'] == 'RuntimeError'


def test_factory_success_does_not_own_caller_report(monkeypatch):
    import framerudiments.effect_graph_gpu as module
    obj = object()
    monkeypatch.setattr(module, 'GLMatteBackend', lambda: obj)
    r = {}
    assert create_gpu_backend('auto', report=r) is obj and r['state'] == 'attached'
    with pytest.raises(ValueError):
        create_gpu_backend('cpu', report=[])


@pytest.mark.parametrize('size,count', [((0, 2), 1), ((2.0, 2), 1), ((2, 2), True), ((2, 2), 0)])
def test_gpu_budget_input_validation(size, count):
    with pytest.raises(ValueError):
        device()._check_budget(size, count)


def test_closed_gpu_and_invalid_constructor_budget():
    with pytest.raises(GPUUnsupported, match='closed'):
        device(closed=True)._check_budget((2, 2), 1)
    with pytest.raises(ValueError, match='budget'):
        GLMatteBackend(max_working_set_bytes=0)


def test_bad_plan_arguments():
    g = unary()
    with pytest.raises(ValueError):
        plan_effect_graph({}, {})
    for kw in [dict(mode='cuda'), dict(limits={}), dict(require=1), dict(gpu_backend=device())]:
        with pytest.raises(ValueError):
            g.backend_plan({'s': (8, 8)}, **kw)


def test_missing_numpy_is_dependency_failure_not_gpu_failure(monkeypatch):
    import framerudiments.effect_graph_backend_contract as mod
    real = mod._package
    monkeypatch.setattr(mod, '_package', lambda d, m: {'module_discoverable': False, 'version': None}
                        if m == 'numpy' else real(d, m))
    p = unary('spatial_warp').backend_plan({'s': (8, 8)})
    assert p['errors'][0]['code'] == 'cpu_dependency_missing'


class _Uniform:
    value = None
    def write(self, value):
        self.value = value


class _Program(dict):
    def __missing__(self, key):
        self[key] = _Uniform()
        return self[key]


class _Allocation:
    def __init__(self, owner, size, texture):
        self.owner, self.size, self.texture = owner, size, texture
        self.released = False
        owner.resources.append(self)
        if texture:
            owner.live_textures += 1
            owner.peak_textures = max(owner.peak_textures, owner.live_textures)
    def use(self, **kwargs):
        pass
    def release(self):
        assert not self.released
        self.released = True
        if self.texture:
            self.owner.live_textures -= 1
    def read(self, **kwargs):
        return bytes(self.size[0] * self.size[1] * 4)


class _AllocationBackend(GLMatteBackend):
    """Exercise real wrapper allocation/control flow, without compiling a shader."""
    def __init__(self, budget=10**9):
        self.ctx = SimpleNamespace(info={'GL_MAX_TEXTURE_SIZE': 16384}, disable=lambda *a: None)
        self.max_working_set_bytes = budget
        self._moderngl = SimpleNamespace(BLEND=1, TRIANGLES=4)
        self.closed = False
        self.live_textures = self.peak_textures = 0
        self.resources = []
        self.planned_count = None
    def _input(self, image):
        return _Allocation(self, image.size, True)
    def _target(self, size):
        return _Allocation(self, size, True), _Allocation(self, size, False)
    def _get_shader(self, *args):
        return _Program(), SimpleNamespace(render=lambda **kwargs: None)
    def _check_budget(self, size, texture_count):
        self.planned_count = texture_count
        super()._check_budget(size, texture_count)


@pytest.mark.parametrize('kind', ['matte', 'chroma_key'])
@pytest.mark.parametrize('morph,blur', [(0, 0), (1, 0), (0, 1), (1, 1)])
@pytest.mark.parametrize('original', [True, False])
def test_budget_matches_actual_wrapper_texture_lifetime(kind, morph, blur, original):
    b = _AllocationBackend()
    image = Image.new('RGBA', (7, 5), 'white')
    alpha = image.getchannel('A') if original else None
    if kind == 'matte':
        b.apply_matte([image, image], {'expand': morph, 'feather': blur}, original_alpha=alpha)
    else:
        b.chroma_key(image, {'edge_expand': morph, 'edge_feather': blur}, original_alpha=alpha)
    assert b.peak_textures == b.planned_count
    assert not b.live_textures
    assert all(r.released for r in b.resources)


@pytest.mark.parametrize('kind', ['matte', 'chroma_key'])
def test_combined_pass_over_budget_rejected_before_allocating(kind):
    image = Image.new('RGBA', (7, 5), 'white')
    # Old matte/key budget underestimated combined morph+blur by one RGBA8 surface.
    required = 9 if kind == 'matte' else 8
    b = _AllocationBackend(budget=7 * 5 * 4 * (required - 1))
    with pytest.raises(GPUUnsupported, match='working-set'):
        if kind == 'matte':
            b.apply_matte([image, image], {'expand': 1, 'feather': 1}, original_alpha=image.getchannel('A'))
        else:
            b.chroma_key(image, {'edge_expand': 1, 'edge_feather': 1}, original_alpha=image.getchannel('A'))
    assert not b.resources


@pytest.mark.parametrize('kwargs', [{'format': []}, {'residency': {}}, {'format': None}])
def test_nonstring_surface_contract_tags_are_rejected(kwargs):
    with pytest.raises(ValueError):
        SurfaceContract(8, 8, **kwargs)


def test_cpu_matte_does_not_require_numpy(monkeypatch):
    import framerudiments.effect_graph_backend_contract as mod
    real = mod._package
    monkeypatch.setattr(mod, '_package', lambda d, m: {'module_discoverable': False, 'version': None}
                        if m == 'numpy' else real(d, m))
    assert matte().backend_plan({'s': (8, 8), 'm': (8, 8)})['eligible']
