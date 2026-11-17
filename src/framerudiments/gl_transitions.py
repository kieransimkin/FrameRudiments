"""M9.2–M9.4 pinned GL Transitions with preflight and a restricted GLSL subset.

Only reviewed, SHA-256-pinned sources may run. Static source checks are not a
GPU execution sandbox; use process/device isolation for untrusted material.
"""

from copy import deepcopy
from hashlib import sha256
from math import isfinite
from pathlib import Path
import re

from PIL import Image

from .gl_shader_guard import inspect_restricted_gl_transition

MAX_SHADER_BYTES = 64 * 1024
MAX_SHADER_PASSES = 4
MAX_SHADER_WORKING_BYTES = 256 * 1024 * 1024
DEFAULT_SHADER_WORKING_BYTES = 128 * 1024 * 1024
GL_TRANSITION_FIELDS = frozenset({
    "shader_path", "shader_sha256", "trusted_shader", "time_seconds",
    "time_delta_seconds", "frame_index", "shader_passes", "max_pass_bytes",
})

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
    # Validate all untrusted operations *before* shader compilation or texture
    # allocation. Trust is still a separate explicit opt-in and not a sandbox.
    safe_source, _receipt = inspect_restricted_gl_transition(source)
    return actual, _FRAGMENT_HEADER + safe_source + _FRAGMENT_FOOTER


def _shader_chain(config):
    """Immutable, ordered full-file pin descriptors (first shader plus 0..3 more)."""
    additional = config.get("shader_passes", [])
    if not isinstance(additional, list) or len(additional) > MAX_SHADER_PASSES - 1:
        raise ValueError(f"GL transition shader_passes must have at most {MAX_SHADER_PASSES - 1} entries")
    for index, pass_spec in enumerate(additional):
        if (not isinstance(pass_spec, dict)
                or set(pass_spec) != {"shader_path", "shader_sha256"}):
            raise ValueError(f"GL transition shader_passes[{index}] requires only shader_path and shader_sha256")
    return [{"shader_path": config.get("shader_path"),
             "shader_sha256": config.get("shader_sha256"),
             "trusted_shader": config.get("trusted_shader")},
            *[{**entry, "trusted_shader": config.get("trusted_shader")} for entry in additional]]


def shader_pass_plan(config, size):
    """Pure, allocation-free RGBA8 payload estimate for the existing GPU bridge.

    This counts two immutable input textures and one intermediate per pass.
    It intentionally excludes GL program/driver allocations and CPU buffers.
    """
    shaders = _shader_chain(config)
    limit = config.get("max_pass_bytes", DEFAULT_SHADER_WORKING_BYTES)
    if (type(limit) is not int or not 1 <= limit <= MAX_SHADER_WORKING_BYTES):
        raise ValueError(f"GL transition max_pass_bytes must be 1..{MAX_SHADER_WORKING_BYTES}")
    if (not isinstance(size, (tuple, list)) or len(size) != 2
            or any(type(v) is not int or v < 1 or v > 16384 for v in size)):
        raise ValueError("GL transition surface dimensions must be positive and at most 16384")
    if size[0] * size[1] > 67_108_864:
        raise ValueError("GL transition surface exceeds the pixel contract")
    texture_count = len(shaders) + 2
    required = size[0] * size[1] * 4 * texture_count
    return {"passes": len(shaders), "textures": texture_count,
            "working_rgba8_bytes": required, "max_pass_bytes": limit,
            "eligible": required <= limit}


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
    # Source pins for every declared pass must be checked at graph validation,
    # not only when the shader is eventually selected for rendering.
    for entry in _shader_chain(config):
        _local_fragment(entry)
    shader_pass_plan(config, (1, 1))


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
    # Preflight the *entire* chain before uploading either source or compiling.
    plan = shader_pass_plan(config, outgoing.size)
    if not plan["eligible"]:
        from .effect_graph_gpu import GPUUnsupported
        raise GPUUnsupported(
            f"GL transition requires {plan['working_rgba8_bytes']} RGBA8 bytes, "
            f"above max_pass_bytes={plan['max_pass_bytes']}"
        )
    fragments = [_local_fragment(entry) for entry in _shader_chain(config)]
    width, height = outgoing.size
    uniforms = {
        "progress": progress, "ratio": width / height,
        "resolution": (float(width), float(height)),
        "RENDERSIZE": (float(width), float(height)),
        "TIME": float(config.get("time_seconds", 0)),
        "TIMEDELTA": float(config.get("time_delta_seconds", 0)),
        "FRAMEINDEX": int(config.get("frame_index", 0)),
    }
    def passes(_):
        # Each pass reads the previous output as 'from' and the unchanged B
        # source as 'to'. All work stays in device RGBA8 until final readback.
        # _execute appends each pass result after inputs 0 and 1.
        return [(f"m92_gl_transition_{digest}", fragment,
                 {"from_tex": index + 1 if index else 0, "to_tex": 1}, uniforms)
                for index, (digest, fragment) in enumerate(fragments)]

    result = _execute(backend, [outgoing, incoming], passes, outgoing.size,
                      budget=plan["textures"])
    report = getattr(backend, "last_transport", None)
    if isinstance(report, dict):
        report["external_shader_passes"] = report.get("external_shader_passes", 0) + plan["passes"]
        report["external_shader_peak_payload_bytes"] = max(
            report.get("external_shader_peak_payload_bytes", 0), plan["working_rgba8_bytes"])
    return result


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
            _shader_chain(target)  # Validate additional pass shape before resolving paths.
            pinned = project.ref({"path": target["shader_path"], "sha256": target["shader_sha256"]})
            target["shader_path"] = pinned["path"]
            for pass_spec in target.get("shader_passes", []):
                pinned = project.ref({"path": pass_spec["shader_path"],
                                      "sha256": pass_spec["shader_sha256"]})
                pass_spec["shader_path"] = pinned["path"]
            validate_gl_transition(target)
    return prepared if prepared is not None else spec
