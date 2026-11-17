"""M9.3: mandatory real OpenGL multipass acceptance (no skips)."""
from hashlib import sha256
from pathlib import Path

from PIL import Image

from framerudiments.effect_graph_gpu import GLMatteBackend, GPUUnsupported
from framerudiments.gl_transitions import render_gl_transition

ROOT = Path(__file__).resolve().parents[1] / 'examples'
FIRST = ROOT / 'gl-transition-crossfade.glsl'
SECOND = ROOT / 'gl-transition-reinforce.glsl'


def pin(path):
    return {'shader_path': str(path), 'shader_sha256': sha256(path.read_bytes()).hexdigest()}


def test_two_gpu_passes_keep_inputs_and_produce_expected_pixels():
    a = Image.new('RGBA', (4, 4), (255, 0, 0, 120))
    b = Image.new('RGBA', (4, 4), (0, 40, 220, 255))
    before = a.tobytes(), b.tobytes()
    cfg = {'transition_style': 'gl_transition', 'trusted_shader': True, 'progress': .5,
           **pin(FIRST), 'shader_passes': [pin(SECOND)]}
    with GLMatteBackend(backend='egl') as backend:
        backend.begin_transfer_sample()
        result = render_gl_transition(backend, a, b, cfg)
        # Both passes write RGBA8 UNORM: the intermediate alpha is rounded
        # before the second blend. Mesa/EGL gives 222, not the idealised 221.
        assert result.getpixel((1, 1)) == (64, 30, 165, 222)
        assert backend.last_transport['uploads'] == 2
        assert backend.last_transport['readbacks'] == 1
        assert backend.last_transport['external_shader_passes'] == 2
        assert backend.last_transport['external_shader_peak_payload_bytes'] == 4*4*4*4
        # Per-device budget rejection occurs before any upload.
        constrained = GLMatteBackend(ctx=backend.ctx, max_working_set_bytes=4*4*4*3)
        try:
            try:
                render_gl_transition(constrained, a, b, cfg)
            except GPUUnsupported:
                assert constrained.last_transport['uploads'] == 0
            else:
                raise AssertionError('Required strict GPU budget rejection missing')
        finally:
            constrained.close()
    assert (a.tobytes(), b.tobytes()) == before


def test_external_shader_program_cache_is_bounded(tmp_path):
    a = Image.new('RGBA', (4, 4), (255, 0, 0, 255))
    b = Image.new('RGBA', (4, 4), (0, 20, 40, 255))
    with GLMatteBackend(backend='egl') as backend:
        for index in range(20):
            shader = tmp_path / f'shader{index}.glsl'
            shader.write_text('vec4 transition(vec2 uv) { return mix(getFromColor(uv), '
                              f'getToColor(uv), progress + {index}.0 * 0.0); }}\n', encoding='utf8')
            cfg = {'transition_style': 'gl_transition', 'trusted_shader': True,
                   'progress': 0.5, **pin(shader)}
            render_gl_transition(backend, a, b, cfg)
            assert sum(k.startswith('m92_gl_transition_') for k in backend._programs) <= 16


def test_m94_guard_approved_glsl_compiles_and_invalid_source_never_reaches_device(tmp_path):
    """Mandatory no-skip EGL proof; rejecting untrusted operations does not skip GL."""
    good = tmp_path / 'restricted.glsl'
    good.write_text('''// Bounded loop-free GLSL supported by the M9.4 gate.
        uniform float progress;
        vec4 transition(vec2 uv) {
            vec4 original = getFromColor(uv);
            vec4 next_frame = getToColor(uv);
            return mix(original, next_frame, clamp(progress, 0.0, 1.0));
        }
    ''', encoding='utf-8')
    a = Image.new('RGBA', (4, 4), (0, 0, 0, 255))
    b = Image.new('RGBA', (4, 4), (255, 255, 255, 255))
    cfg = {'transition_style': 'gl_transition', 'trusted_shader': True,
           'progress': 0.5, **pin(good)}
    with GLMatteBackend(backend='egl') as backend:
        backend.begin_transfer_sample()
        out = render_gl_transition(backend, a, b, cfg)
        assert out.getpixel((0, 0)) == (128, 128, 128, 255)
        assert backend.last_transport['uploads'] == 2
        assert backend.last_transport['readbacks'] == 1
        bad = tmp_path / 'bad.glsl'
        bad.write_text('vec4 transition(vec2 uv){while(true){} return vec4(1.);}', encoding='utf-8')
        cfg.update(pin(bad))
        existing = len(backend._programs)
        backend.begin_transfer_sample()
        import pytest
        with pytest.raises(ValueError, match='unsupported control flow'):
            render_gl_transition(backend, a, b, cfg)
        assert len(backend._programs) == existing
        assert backend.last_transport['uploads'] == 0
