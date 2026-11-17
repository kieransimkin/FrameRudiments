"""M9.2: explicitly trusted, pinned GL Transitions on the existing RGBA8 bridge.

This accepts the GL Transitions `vec4 transition(vec2 uv)` convention, not ISF.
It is not a sandbox for untrusted GLSL; M9.4 will add a shader safety gate.
"""

from copy import deepcopy
from hashlib import sha256
from math import isfinite
from pathlib import Path
import re

from PIL import Image

MAX_SHADER_BYTES = 64 * 1024
GL_TRANSITION_FIELDS = frozenset({
    "shader_path", "shader_sha256", "trusted_shader", "time_seconds",
    "time_delta_seconds", "frame_index",
})
_HOST_UNIFORMS = {
    "progress": "float", "ratio": "float", "resolution": "vec2", "RENDERSIZE": "vec2",
    "TIME": "float", "TIMEDELTA": "float", "FRAMEINDEX": "int",
}
_HOST_DECLARATION = re.compile(r"\buniform\s+(float|int|vec2|vec3|vec4|bool|sampler2D)\s+([A-Za-z_]\w*)\s*;")
_TRANSITION_FUNCTION = re.compile(r"\bvec4\s+transition\s*\(\s*vec2\s+[A-Za-z_]\w*\s*\)")

_FRAGMENT_HEADER = """#version 330
uniform sampler2D from_tex;
uniform sampler2D to_tex;
uniform float progress;
uniform float ratio;
uniform vec2 resolution;
uniform vec2 RENDERSIZE;
uniform float TIME;
uniform float TIMEDELTA;
uniform int FRAMEINDEX;
out vec4 out_color;
vec4 getFromColor(vec2 uv) { return texture(from_tex, clamp(uv, vec2(0.0), vec2(1.0))); }
vec4 getToColor(vec2 uv) { return texture(to_tex, clamp(uv, vec2(0.0), vec2(1.0))); }
"""
_FRAGMENT_FOOTER = """
void main() {
    // Top-first host rows are transported unchanged; the first pixel centre is (0.5, 0.5).
    vec2 uv = gl_FragCoord.xy / resolution;
    out_color = clamp(transition(uv), 0.0, 1.0);
}
"""


def _finite(value, label, maximum):
    if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(f"GL transition {label} must be a finite number between 0 and {maximum}")
    return float(value)


def _local_fragment(config):
    """Verify source bytes on *every* use, including exact endpoint frames."""
    path = config.get("shader_path")
    digest = config.get("shader_sha256")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("GL transition requires shader_path")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ValueError("GL transition requires a 64-hex shader_sha256 pin")
    if config.get("trusted_shader") is not True:
        raise ValueError("GL transition requires explicit trusted_shader: true (GLSL is executable)")
    file = Path(path)
    if not file.is_file():
        raise ValueError("GL transition shader_path must point to a local regular file")
    with file.open("rb") as stream:
        raw = stream.read(MAX_SHADER_BYTES + 1)
    if len(raw) > MAX_SHADER_BYTES:
        raise ValueError(f"GL transition exceeds {MAX_SHADER_BYTES} source bytes")
    actual = sha256(raw).hexdigest()
    if actual != digest.lower():
        raise ValueError("GL transition shader SHA-256 mismatch")
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("GL transition shader must be UTF-8") from exc
    if not source.strip() or "\x00" in source:
        raise ValueError("GL transition source is empty or contains a NUL byte")
    # This is an explicitly limited subset, not comprehensive security analysis.
    # Only opt-in, locally reviewed shaders are admitted; never accept remote GLSL.
    if re.search(r"^\s*#", source, flags=re.MULTILINE):
        raise ValueError("GL transition preprocessor directives are not supported in M9.2")
    if re.search(r"\bvoid\s+main\s*\(", source):
        raise ValueError("GL transition must expose transition(vec2), not main()")
    if not _TRANSITION_FUNCTION.search(source):
        raise ValueError("GL transition needs a vec4 transition(vec2 uv) function")
    def remove_host_uniform(match):
        kind, name = match.groups()
        if name in _HOST_UNIFORMS:
            if kind != _HOST_UNIFORMS[name]:
                raise ValueError(f"GL transition host uniform {name} must have type {_HOST_UNIFORMS[name]}")
            return ""
        if name in {"from_tex", "to_tex"}:
            raise ValueError("GL transition texture samplers are host-managed")
        # Other uniforms need an explicit typed binding (future milestone).
        raise ValueError(f"GL transition unsupported shader uniform: {name}")
    source = _HOST_DECLARATION.sub(remove_host_uniform, source)
    # Catch declarations the supported uniform grammar could not consume.
    if re.search(r"\buniform\b", source):
        raise ValueError("GL transition has an unsupported uniform declaration")
    return actual, _FRAGMENT_HEADER + source + _FRAGMENT_FOOTER


def validate_gl_transition(config):
    """Validate a graph transition without constructing a device context."""
    style = config.get("transition_style", "dissolve")
    if style != "gl_transition":
        if GL_TRANSITION_FIELDS.intersection(config):
            raise ValueError("GL transition shader/time fields require gl_transition style")
        return
    allowed = {"id", "type", "inputs", "transition_style", "progress", "timing", "opacity", "mode"}
    if set(config) - allowed - GL_TRANSITION_FIELDS:
        raise ValueError("gl_transition does not accept native matte/geometry/spectral settings")
    _finite(config.get("progress", 0), "progress", 1)
    _finite(config.get("time_seconds", 0), "time_seconds", 1e8)
    _finite(config.get("time_delta_seconds", 0), "time_delta_seconds", 3600)
    index = config.get("frame_index", 0)
    if type(index) is not int or not 0 <= index <= 2**31 - 1:
        raise ValueError("GL transition frame_index must be an integer from 0 to 2^31-1")
    _local_fragment(config)


def render_gl_transition(backend, outgoing: Image.Image, incoming: Image.Image, config):
    """Render one deterministic, pinned source via GLMatteBackend's bounded bridge.

    Progress, clock and frame values are supplied by the caller: no wall-clock or
    prior-frame dependency. Exact endpoints are copied without a GPU upload.
    """
    validate_gl_transition(config)
    if (not isinstance(outgoing, Image.Image) or not isinstance(incoming, Image.Image)
            or outgoing.mode != "RGBA" or incoming.mode != "RGBA" or outgoing.size != incoming.size):
        raise ValueError("GL transition requires two equal-sized RGBA images")
    progress = float(config.get("progress", 0))
    if progress == 0:
        return outgoing.copy()
    if progress == 1:
        return incoming.copy()
    if backend is None or getattr(backend, "closed", False):
        raise RuntimeError("GL transition needs an attached OpenGL 3.3+ backend; there is no CPU fallback")
    # Retain the existing bridge implementation, telemetry and cleanup, rather
    # than inventing another texture/context lifetime or data transport.
    from .effect_graph_gpu import GLMatteBackend
    from .effect_graph_gpu_kernels import _execute
    if not isinstance(backend, GLMatteBackend):
        raise ValueError("GL transition requires the FrameRudiments GLMatteBackend")
    digest, fragment = _local_fragment(config)
    width, height = outgoing.size
    uniforms = {
        "progress": progress, "ratio": width / height,
        "resolution": (float(width), float(height)),
        "RENDERSIZE": (float(width), float(height)),
        "TIME": float(config.get("time_seconds", 0)),
        "TIMEDELTA": float(config.get("time_delta_seconds", 0)),
        "FRAMEINDEX": int(config.get("frame_index", 0)),
    }
    return _execute(
        backend, [outgoing, incoming],
        lambda _: [(f"m92_gl_transition_{digest}", fragment,
                    {"from_tex": 0, "to_tex": 1}, uniforms)],
        outgoing.size, budget=3,
    )


def prepare_project_gl_transitions(project):
    """Resolve project-relative GLSL and register pins in the normal input ledger.

    Never change the authored project dict. The existing Project.ref() performs
    full-file SHA verification and records the canonical source identity so
    work-cache preflight and verify_inputs cover the shader as ordinary media.
    """
    spec = project.data["effect_graph"]
    if not isinstance(spec, dict) or not isinstance(spec.get("nodes"), list):
        return spec  # Existing EffectGraph validation will reject malformed specs.
    prepared = None
    for index, authored in enumerate(spec["nodes"]):
        if not isinstance(authored, dict):
            continue
        if authored.get("type") == "transition":
            entries = [(None, authored)]
        elif authored.get("type") == "transition_chain" and isinstance(authored.get("stages"), list):
            entries = [(i, stage) for i, stage in enumerate(authored["stages"])
                       if isinstance(stage, dict)]
        else:
            continue
        for stage_index, entry in entries:
            if entry.get("transition_style") != "gl_transition":
                continue
            if prepared is None:
                prepared = deepcopy(spec)
            target = prepared["nodes"][index]
            if stage_index is not None:
                target = target["stages"][stage_index]
            pinned = project.ref({"path": target["shader_path"], "sha256": target["shader_sha256"]})
            target["shader_path"] = pinned["path"]
            validate_gl_transition(target)
    return prepared if prepared is not None else spec
