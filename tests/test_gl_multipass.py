"""M9.3: deterministic bounded shader-chain preflight and project pinning."""
from hashlib import sha256
from pathlib import Path

from PIL import Image
import pytest

from framerudiments.gl_transitions import (
    MAX_SHADER_PASSES, _shader_chain, prepare_project_gl_transitions,
    render_gl_transition, shader_pass_plan, validate_gl_transition,
)
from framerudiments.effect_graph_backend_contract import gpu_requirements

ROOT = Path(__file__).resolve().parents[1]
FIRST = ROOT / 'examples/gl-transition-crossfade.glsl'
SECOND = ROOT / 'examples/gl-transition-reinforce.glsl'


def ref(path):
    return {'shader_path': str(path), 'shader_sha256': sha256(path.read_bytes()).hexdigest()}


def config(**overrides):
    cfg = {'transition_style': 'gl_transition', 'trusted_shader': True,
           'progress': .5, **ref(FIRST), 'shader_passes': [ref(SECOND)]}
    return {**cfg, **overrides}


def test_bound_and_exact_resident_plan():
    cfg = config()
    validate_gl_transition(cfg)
    plan = shader_pass_plan(cfg, (8, 6))
    assert plan == {'passes': 2, 'textures': 4, 'working_rgba8_bytes': 8 * 6 * 16,
                    'max_pass_bytes': 128 * 1024**2, 'eligible': True}
    requirement = gpu_requirements('transition', cfg, 2)
    assert (requirement.texture_count, requirement.upload_count,
            requirement.readback_count, requirement.draw_passes) == (4, 2, 1, 2)
    assert shader_pass_plan(config(shader_passes=[]), (8, 6))['textures'] == 3
    assert len(_shader_chain(cfg)) == 2
    assert MAX_SHADER_PASSES == 4


@pytest.mark.parametrize('value,match', [
    ('two', 'shader_passes'),
    ([ref(SECOND)] * 4, 'at most'),
    ([{}], 'requires only'),
    ([{'shader_path': str(SECOND), 'shader_sha256': '0'*64, 'label': 'extra'}], 'requires only'),
    ([{'shader_path': str(SECOND), 'shader_sha256': '0'*64}], 'SHA-256 mismatch'),
    ([1], 'requires only'),
])
def test_rejects_malformed_or_unpinned_passes(value, match):
    with pytest.raises(ValueError, match=match):
        validate_gl_transition(config(shader_passes=value))


@pytest.mark.parametrize('limit', [0, -1, 10.5, True, 256 * 1024**2 + 1])
def test_rejects_invalid_memory_limit(limit):
    with pytest.raises(ValueError, match='max_pass_bytes'):
        validate_gl_transition(config(max_pass_bytes=limit))


def test_preflight_is_allocation_free_and_rejects_before_bridge(monkeypatch):
    from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
    from framerudiments import effect_graph_gpu_kernels
    backend = object.__new__(GLMatteBackend)
    backend.closed = False
    a = Image.new('RGBA', (8, 6), (220, 4, 18, 133))
    b = Image.new('RGBA', (8, 6), (5, 40, 200, 255))
    monkeypatch.setattr(effect_graph_gpu_kernels, '_execute',
                        lambda *_args, **_kwargs: pytest.fail('GPU allocated before preflight'))
    with pytest.raises(GPUUnsupported, match='max_pass_bytes'):
        render_gl_transition(backend, a, b, config(max_pass_bytes=8 * 6 * 16 - 1))
    assert shader_pass_plan(config(max_pass_bytes=1), a.size)['eligible'] is False
    assert a.getpixel((0, 0)) == (220, 4, 18, 133)


def test_chain_dispatch_and_telemetry_use_one_readback(monkeypatch):
    from framerudiments.effect_graph_gpu import GLMatteBackend
    from framerudiments import effect_graph_gpu_kernels
    a = Image.new('RGBA', (8, 6), (255, 0, 0, 120))
    b = Image.new('RGBA', (8, 6), (0, 40, 220, 255))
    before = a.tobytes(), b.tobytes()
    backend = object.__new__(GLMatteBackend)
    backend.closed = False
    backend.last_transport = {}
    observed = {}

    def fake_execute(owner, inputs, make_steps, size, *, budget):
        observed.update(owner=owner, inputs=inputs, steps=make_steps([]), size=size, budget=budget)
        return a.copy()
    monkeypatch.setattr(effect_graph_gpu_kernels, '_execute', fake_execute)
    out = render_gl_transition(backend, a, b, config())
    assert out.tobytes() == a.tobytes()
    assert (a.tobytes(), b.tobytes()) == before
    assert observed['owner'] is backend and observed['budget'] == 4
    assert len(observed['steps']) == 2
    assert [step[2] for step in observed['steps']] == [
        {'from_tex': 0, 'to_tex': 1}, {'from_tex': 2, 'to_tex': 1}]
    assert all(step[3]['progress'] == .5 for step in observed['steps'])
    assert backend.last_transport['external_shader_passes'] == 2
    assert backend.last_transport['external_shader_peak_payload_bytes'] == 8 * 6 * 16
    render_gl_transition(backend, a, b, config())
    assert backend.last_transport['external_shader_passes'] == 4


def test_pin_is_validated_even_for_endpoint_and_other_shaders(tmp_path):
    a = Image.new('RGBA', (4, 4), (1, 2, 3, 4))
    b = Image.new('RGBA', (4, 4), (5, 6, 7, 8))
    assert render_gl_transition(None, a, b, config(progress=0)).tobytes() == a.tobytes()
    assert render_gl_transition(None, a, b, config(progress=1)).tobytes() == b.tobytes()
    path = tmp_path / 'secondary.glsl'
    path.write_bytes(SECOND.read_bytes())
    c = config(progress=0, shader_passes=[ref(path)])
    path.write_text('modified shader', encoding='utf8')
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        render_gl_transition(None, a, b, c)


def test_project_resolves_every_shader_without_mutating_authored_graph(tmp_path):
    for p in (FIRST, SECOND):
        (tmp_path / p.name).write_bytes(p.read_bytes())
    cfg = config(shader_path=FIRST.name,
                 shader_passes=[{**ref(tmp_path / SECOND.name), 'shader_path': SECOND.name}])
    graph = {'nodes': [
        {'id': 'a', 'type': 'source'}, {'id': 'b', 'type': 'source'},
        {'id': 'cross', 'type': 'transition', 'inputs': ['a', 'b'], **cfg},
    ], 'output': 'cross'}
    class Project:
        data = {'effect_graph': graph}
        inputs = []
        def ref(self, reference):
            path = tmp_path / reference['path']
            if sha256(path.read_bytes()).hexdigest() != reference['sha256']:
                raise ValueError('Input hash mismatch')
            self.inputs.append(str(path))
            return {'path': str(path), 'sha256': reference['sha256']}
    project = Project()
    prepared = prepare_project_gl_transitions(project)
    assert prepared is not graph
    assert prepared['nodes'][-1]['shader_path'] == str(tmp_path / FIRST.name)
    assert prepared['nodes'][-1]['shader_passes'][0]['shader_path'] == str(tmp_path / SECOND.name)
    assert graph['nodes'][-1]['shader_passes'][0]['shader_path'] == SECOND.name
    assert len(project.inputs) == 2
