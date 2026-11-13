"""Pinned, single-pass GL Transitions on the RGBA8 effect graph bridge."""
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files

from .effect_graph_transition_timing import validate_transition_timing


SHADERS = {
    'circular-reveal': '4c9bf3b99ff6eaf4bc6cecaad26693132ad7e3fcce3c955487b4a75d0216c95b',
    'ripple-wipe': '5a6288afaee47133df22b0e5a1d31fce03a0f2d49af1903a10b40075ee6740e2',
}

GL_TRANSITION_FIELDS = {'shader', 'progress', 'timing', 'opacity', 'id', 'type', 'inputs'}
MAX_SHADER_SECONDS = 3.4028234663852886e38

_HEADER = """#version 330
uniform sampler2D from_texture;
uniform sampler2D to_texture;
uniform vec2 frame_size;
uniform float progress;
uniform float ratio;
uniform float time;
out vec4 out_color;

// The bridge stores the first Pillow row at texture y=0.
vec2 bridgeUV(vec2 uv) { return vec2(uv.x, 1.0 - uv.y); }
vec4 getFromColor(vec2 uv) { return texture(from_texture, bridgeUV(uv)); }
vec4 getToColor(vec2 uv) { return texture(to_texture, bridgeUV(uv)); }
"""

_FOOTER = """
void main() {
    vec2 uv = bridgeUV(gl_FragCoord.xy / frame_size);
    out_color = transition(uv);
}
"""


def shader_catalogue():
    """List the exact project pins without opening OpenGL or reading user files."""
    return [{'id': name, 'sha256': digest} for name, digest in SHADERS.items()]


def validate_gl_transition(config, input_count):
    if config.keys() - GL_TRANSITION_FIELDS:
        raise ValueError('unsupported gl_transition property')
    if input_count != 2:
        raise ValueError('gl_transition requires exactly two inputs')
    shader = config.get('shader')
    if (not isinstance(shader, dict) or shader.keys() != {'id', 'sha256'}
            or not isinstance(shader['id'], str) or shader['id'] not in SHADERS
            or shader['sha256'] != SHADERS[shader['id']]):
        raise ValueError('gl_transition requires a bundled shader ID and its matching SHA-256')
    progress = config.get('progress', 0)
    if type(progress) not in (int, float) or not 0 <= progress <= 1:
        raise ValueError('gl_transition progress must be in [0, 1]')
    if 'timing' in config:
        validate_transition_timing(config['timing'])


@lru_cache(maxsize=len(SHADERS))
def _fragment(shader_id):
    source = files(__package__).joinpath('gl_transition_shaders', shader_id + '.glsl').read_bytes()
    if sha256(source).hexdigest() != SHADERS[shader_id]:
        raise ValueError(f'Bundled GL transition shader SHA-256 mismatch: {shader_id}')
    return _HEADER + source.decode('ascii') + _FOOTER


def render_gl_transition(backend, outgoing, incoming, config, *, output_seconds):
    from .effect_graph_gpu_kernels import _execute

    validate_gl_transition(config, 2)
    if outgoing.mode != 'RGBA' or incoming.mode != 'RGBA' or outgoing.size != incoming.size:
        raise ValueError('gl_transition requires equally sized RGBA images')
    progress = config.get('progress', 0)
    if progress == 0:
        return outgoing.copy()
    if progress == 1:
        return incoming.copy()
    if type(output_seconds) not in (int, float) or not 0 <= output_seconds <= MAX_SHADER_SECONDS:
        raise ValueError('gl_transition output time must be a finite nonnegative float32')
    width, height = outgoing.size
    if min(width, height) <= 0:
        raise ValueError('gl_transition canvas must be nonempty')
    shader_id = config['shader']['id']
    fragment = _fragment(shader_id)
    uniforms = {'progress': progress, 'ratio': width / height,
                'time': output_seconds, 'frame_size': outgoing.size}
    steps = [('gl_transition_' + shader_id, fragment, {'from_texture': 0, 'to_texture': 1}, uniforms)]
    return _execute(backend, [outgoing, incoming], lambda _: steps, outgoing.size, budget=3)
