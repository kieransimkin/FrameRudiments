"""M9.4 conservative external shader validation with no GL context or downloads."""
from hashlib import sha256
from pathlib import Path

from PIL import Image
import pytest

from framerudiments.gl_shader_guard import (
    MAX_TOKENS, POLICY_VERSION, inspect_restricted_gl_transition,
)
from framerudiments.gl_transitions import _local_fragment, render_gl_transition, validate_gl_transition

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/gl-transition-crossfade.glsl'


def fixture(tmp_path, source, **overrides):
    path = tmp_path / 'shader.glsl'
    path.write_bytes(source.encode('utf-8'))
    cfg = {'transition_style': 'gl_transition', 'shader_path': str(path),
           'shader_sha256': sha256(path.read_bytes()).hexdigest(),
           'trusted_shader': True, 'progress': .5}
    return path, {**cfg, **overrides}


def test_checked_sample_remains_usable_without_gl():
    original = EXAMPLE.read_text(encoding='utf-8')
    sanitized, receipt = inspect_restricted_gl_transition(original)
    assert receipt['policy'] == POLICY_VERSION
    assert receipt['calls'] >= 3
    assert 'uniform float progress;' not in sanitized
    assert 'uniform float TIME;' not in sanitized
    assert 'vec4 transition(vec2 uv)' in sanitized
    assert 'getFromColor(uv)' in sanitized
    assert original == EXAMPLE.read_text(encoding='utf-8')


def test_comments_do_not_hide_directives_or_declarations(tmp_path):
    body = '''// #include "ignored.glsl"\n/* uniform sampler2D external; for(;;){} */
        uniform float progress; /* safe */
        vec4 transition(vec2 uv) { // print "not executable"
            return mix(getFromColor(uv), getToColor(uv), progress);
        }
    '''
    path, cfg = fixture(tmp_path, body)
    validate_gl_transition(cfg)
    digest, fragment = _local_fragment(cfg)
    assert digest == sha256(path.read_bytes()).hexdigest()
    assert '#include' not in fragment and 'sampler2D external' not in fragment
    assert fragment.count('uniform float progress;') == 1  # host only
    assert _local_fragment(cfg)[1] == fragment


@pytest.mark.parametrize('source,message', [
    ('#include "other.glsl"\nvec4 transition(vec2 uv){return vec4(1.);}', 'preprocessor'),
    ('#version 330\nvec4 transition(vec2 uv){return vec4(1.);}', 'preprocessor'),
    ('vec4 transition(vec2 uv){ return texture(from_tex, uv); }', 'unsupported function'),
    ('vec4 transition(vec2 uv){ return texelFetch(to_tex, ivec2(0), 0); }', 'unsupported function'),
    ('vec4 transition(vec2 uv){ return imageLoad(foo, ivec2(0)); }', 'unsupported function'),
    ('vec4 transition(vec2 uv){ imageStore(foo, ivec2(0), vec4(1.)); return vec4(1.); }', 'unsupported standalone'),
    ('vec4 transition(vec2 uv){ atomicAdd(x, 1); return vec4(1.); }', 'unsupported standalone'),
    ('vec4 transition(vec2 uv){ while(true){} return vec4(1.); }', 'unsupported control flow'),
    ('vec4 transition(vec2 uv){ for(int i=0;i<100;i++){} return vec4(1.); }', 'unsupported control flow'),
    ('vec4 transition(vec2 uv){ if(progress > .5) { return vec4(1.); } return vec4(0.); }', 'unsupported control flow'),
    ('vec4 transition(vec2 uv){ discard; return vec4(1.); }', 'unsupported control flow'),
    ('vec4 transition(vec2 uv){ return transition(uv); }', 'unsupported function'),
    ('vec4 sampleExtra(vec2 uv){return vec4(1.);} vec4 transition(vec2 uv){return vec4(1.);}', 'transition\\(vec2'),
    ('vec4 transition(vec2 uv){return vec4(1.);} vec4 extra(vec2 uv){return vec4(0.);}', 'extra functions'),
    ('vec4 transition(vec2 uv){return vec4(1.);} void main(){}', 'extra functions'),
    ('uniform sampler2D other; vec4 transition(vec2 uv){return vec4(1.);}', 'unsupported shader uniform'),
    ('layout(binding=0) uniform sampler2D other; vec4 transition(vec2 uv){return vec4(1.);}', 'transition\\(vec2'),
    ('uniform vec4 progress; vec4 transition(vec2 uv){return vec4(1.);}', 'host uniform progress'),
    ('uniform float progress; uniform float progress; vec4 transition(vec2 uv){return vec4(1.);}', 'duplicate host uniform'),
    ('uniform float from_tex; vec4 transition(vec2 uv){return vec4(1.);}', 'unsupported shader uniform'),
    ('vec4 transition(vec2 uv){ return vec4(gl_FragCoord.xy, 0., 1.); }', 'unknown or inaccessible identifier'),
    ('vec4 transition(vec2 uv){ return vec4(1e999); }', 'numeric literal'),
    ('vec4 transition(vec2 uv){ return vec4(10000000000.); }', 'numeric literal'),
    ('vec4 transition(vec2 uv){ return vec4(.5) + vec4(0.)[0]; }', 'unsupported expression operation'),
    ('vec4 transition(vec2 uv){ float x = 0.; x++; return vec4(x); }', 'expected'),
    ('vec4 transition(vec2 uv){ progress = 2.; return vec4(1.); }', 'mutable locals'),
    ('vec4 transition(vec2 uv){ const float x = 2.; x = 3.; return vec4(x); }', 'mutable locals'),
    ('vec4 transition(vec2 uv){ float z; return vec4(z); }', 'expected'),
    ('vec4 transition(vec2 uv){ float gl_FragColor = 1.; return vec4(gl_FragColor); }', 'reserved local'),
    ('vec4 transition(vec2 uv){ float progress = 2.; return vec4(progress); }', 'reserved local'),
    ('const float from_tex = 0.; vec4 transition(vec2 uv){return vec4(from_tex);}', 'reserved local'),
    ('vec4 transition(vec2 uv){ return vec4(1.); } \\ \n', 'line splicing'),
    ('vec4 transition(vec2 uv){ return vec4("oops"); }', 'strings'),
    ('/* unfinished comment', 'unterminated block comment'),
    ('vec4 transition(vec2 uv){ return vec4(1.);', 'incomplete declaration'),
    ('vec4 transition(vec2 uv){ return vec4(1.); }\nΩ', 'printable ASCII'),
])
def test_reject_untrusted_operations_before_compilation(tmp_path, source, message):
    _, cfg = fixture(tmp_path, source)
    with pytest.raises(ValueError, match=message):
        validate_gl_transition(cfg)


def test_rejects_unknown_source_with_explicit_trust_flag(tmp_path):
    _, cfg = fixture(tmp_path, 'vec4 transition(vec2 uv){return vec4(1.);}', trusted_shader=False)
    with pytest.raises(ValueError, match='trusted_shader'):
        validate_gl_transition(cfg)


def test_rejects_oversized_token_or_statement_program(tmp_path):
    first = 'vec4 transition(vec2 uv){ float x = 0.;' + ' x = x + 1.;' * 60 + ' return vec4(x); }'
    _, cfg = fixture(tmp_path, first)
    with pytest.raises(ValueError, match='statement budget'):
        validate_gl_transition(cfg)
    second = 'vec4 transition(vec2 uv){ return vec4(' + '1.+ ' * MAX_TOKENS + '1.); }'
    _, cfg = fixture(tmp_path, second)
    with pytest.raises(ValueError, match='token limit'):
        validate_gl_transition(cfg)


def test_bounded_straightline_helpers_and_ternary_are_supported(tmp_path):
    body = '''const float PI = 3.14159265;
        uniform float progress;
        vec4 transition(vec2 uv) {
            vec2 xy = vec2(uv.x, uv.y);
            float wave = sin(xy.x * PI);
            float weight = clamp(progress + wave * 0.1, 0.0, 1.0);
            weight = weight > 0.5 ? weight : 0.5;
            return mix(getFromColor(uv), getToColor(uv), weight);
        }'''
    _, cfg = fixture(tmp_path, body)
    validate_gl_transition(cfg)
    assert 'weight = weight > 0.5 ? weight : 0.5;' in _local_fragment(cfg)[1]


def test_unsafe_second_pass_rejected_at_exact_endpoints_before_gpu(tmp_path):
    good = EXAMPLE
    malicious, extra = fixture(tmp_path, 'vec4 transition(vec2 uv){while(true){} return vec4(1.);}')
    cfg = {'transition_style': 'gl_transition', 'trusted_shader': True, 'progress': 0,
           'shader_path': str(good), 'shader_sha256': sha256(good.read_bytes()).hexdigest(),
           'shader_passes': [{'shader_path': str(malicious), 'shader_sha256': extra['shader_sha256']}]}
    a = Image.new('RGBA', (4, 4), 'red')
    b = Image.new('RGBA', (4, 4), 'blue')
    with pytest.raises(ValueError, match='unsupported control flow'):
        render_gl_transition(None, a, b, cfg)


def test_modified_source_is_rejected_before_static_inspection(tmp_path):
    source, cfg = fixture(tmp_path, 'vec4 transition(vec2 uv){return vec4(1.);}')
    source.write_text('vec4 transition(vec2 uv){for(;;){} return vec4(1.);}', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        validate_gl_transition(cfg)
