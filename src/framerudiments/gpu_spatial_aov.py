"""M12.3: explicit offscreen GL 3.3 float32 MRT capture for native solid meshes.

Use the SAME projected triangles and CPU flat-facet GGX radiance as M12.2,
but perform coverage, Z testing and AOV interpolation in real OpenGL. It is
not the independent GPU terrain/material renderer and is not an HDR replacement
for FrameRudiments' existing RGB8 production compositor. No implicit fallback.
"""

from __future__ import annotations

import hashlib
import math

from .colour_aov import AOVFrame, MAX_RGB_BYTES, srgb8_to_scene_linear, write_exr
from .solid_spatial_aov import _clock, _project_mesh, _shade_linear
from .solid_spatial_renderers import SolidSpatialRenderer, _cross, _normalized

SCHEMA = "framerudiments.solid-spatial-gpu-aov.v1"
PARITY_SCHEMA = "framerudiments.solid-spatial-gpu-parity.v1"
# Three RGBA32F buffers + depth, plus 2 readback copies and AOVFrame snapshots.
MAX_GPU_CAPTURE_BYTES = min(512 * 1024 * 1024, MAX_RGB_BYTES)
NEAR = 0.12  # Matches the native solid projection's near-plane rejection.

VERTEX = """#version 330
in vec3 in_current;
in vec3 in_previous;
in vec3 in_normal;
in vec3 in_radiance;
uniform vec2 u_size;
out vec3 v_normal;
out vec3 v_radiance;
out float v_camera_depth;
out vec3 v_previous_h;
void main() {
    float camera_z = in_current.z;
    vec2 ndc = vec2(2.0 * in_current.x / u_size.x - 1.0,
                    1.0 - 2.0 * in_current.y / u_size.y);
    // Camera z as clip w enables perspective-correct raster interpolation.
    // Closest depth wins in the hardware depth test (no encoded RGB proxy).
    gl_Position = vec4(ndc * camera_z, camera_z - 0.24, camera_z);
    v_camera_depth = camera_z;
    v_previous_h = vec3(in_previous.xy * in_previous.z, in_previous.z);
    v_normal = in_normal;
    v_radiance = in_radiance;
}
"""

FRAGMENT = """#version 330
in vec3 v_normal;
in vec3 v_radiance;
in float v_camera_depth;
in vec3 v_previous_h;
uniform vec2 u_size;
layout(location = 0) out vec4 hdr;
layout(location = 1) out vec4 geometry;
layout(location = 2) out vec4 motion;
void main() {
    vec2 xy = vec2(gl_FragCoord.x, u_size.y - gl_FragCoord.y);
    hdr = vec4(v_radiance, 1.0);
    geometry = vec4(v_camera_depth, normalize(v_normal));
    vec2 previous_xy = v_previous_h.xy / v_previous_h.z;
    motion = vec4(xy - previous_xy, 0.0, 0.0);
}
"""


def _check(renderer, frame, previous_frame, light_stops, backend):
    if not isinstance(renderer, SolidSpatialRenderer):
        raise ValueError("GPU AOV requires a native SolidSpatialRenderer")
    if (type(light_stops) not in (int, float) or not math.isfinite(light_stops)
            or not -8 <= light_stops <= 8):
        raise ValueError("GPU AOV light_stops must be a finite value in [-8,8]")
    if backend not in ("egl", "default", "wgl", "osmesa"):
        raise ValueError("Unsupported explicit GPU context backend")
    now, _ = _clock(frame)
    if previous_frame is not None:
        earlier, _ = _clock(previous_frame)
        index = getattr(frame.context, "frame", None)
        old_index = getattr(previous_frame.context, "frame", None)
        if (type(index) is not int or type(old_index) is not int
                or old_index != index - 1 or earlier >= now):
            raise ValueError("GPU velocity requires adjacent, increasing native frames")
    w, h = renderer.size
    # Account for hardware MRT, depth surface, readback and Python-side arrays.
    if w * h * (3 * 16 * 3 + 4 + 10 * 4) > MAX_GPU_CAPTURE_BYTES:
        raise ValueError("GPU float32 AOV capture exceeds its bounded memory budget")


def _triangles(renderer, frame, previous_frame, light_stops):
    """Immutable native triangle stream; identical face order to the CPU owner."""
    import numpy as np  # GPU/AOV opt-in only

    projected, world = _project_mesh(renderer, frame)
    previous = _project_mesh(renderer, previous_frame)[0] if previous_frame is not None else projected
    data = []
    for row in range(renderer.rings):
        for column in range(renderer.segments):
            a, b = (row, column), (row + 1, column)
            c, d = (row, column + 1), (row + 1, column + 1)
            for i, j, k in ((a, b, c), (b, d, c)):
                points = (projected[i], projected[j], projected[k])
                if any(p is None for p in points):
                    continue
                prior = (previous[i], previous[j], previous[k])
                if any(p is None for p in prior):
                    raise ValueError("GPU velocity cannot project the previous visible facet")
                p, q, r = world[i], world[j], world[k]
                normal = _normalized(*_cross(tuple(q[n] - p[n] for n in range(3)),
                                             tuple(r[n] - p[n] for n in range(3))))
                center = tuple((p[n] + q[n] + r[n]) / 3 for n in range(3))
                base = renderer.colours[(row * len(renderer.colours)) // renderer.rings]
                rgb, facing = _shade_linear(normal, center, base, renderer, light_stops)
                for point, older in zip(points, prior):
                    data.append((*point, *older, *facing, *rgb))
    return np.ascontiguousarray(data, dtype=np.float32).reshape((-1, 12))


def _read(texture, size):
    import numpy as np

    w, h = size
    raw = texture.read(alignment=1)
    if len(raw) != w * h * 4 * 4:
        raise RuntimeError("GPU AOV readback length differs from float32 framebuffer")
    # GL readback's first scanline is bottom-up; native AOV images are top-down.
    return np.frombuffer(raw, dtype="=f4").reshape((h, w, 4))[::-1].copy()


def render_solid_aov_gpu(renderer, frame, *, previous_frame=None, light_stops=0.0,
                         backend="egl", return_evidence=False):
    """Render floating point HDR/depth/normals/velocity in a private GL context.

    The GPU owns triangle coverage, hardware depth testing and perspective
    interpolation. Facet radiance is evaluated by the M12.2 CPU GGX reference;
    no claim of independent GPU material-lighting parity is implied.
    """
    _check(renderer, frame, previous_frame, light_stops, backend)
    try:
        import moderngl
    except ImportError as exc:
        raise RuntimeError("M12.3 GPU capture requires the optional moderngl runtime") from exc
    import numpy as np

    vertices = _triangles(renderer, frame, previous_frame, light_stops)
    ctx = None
    resources = []
    try:
        kwargs = {"require": 330}
        if backend != "default":
            kwargs["backend"] = backend
        ctx = moderngl.create_standalone_context(**kwargs)
        if ctx.version_code < 330:
            raise RuntimeError("GPU float AOV requires OpenGL 3.3")
        w, h = renderer.size
        if max(w, h) > ctx.info.get("GL_MAX_TEXTURE_SIZE", 8192):
            raise RuntimeError("GPU AOV exceeds GL_MAX_TEXTURE_SIZE")
        targets = [ctx.texture((w, h), 4, dtype="f4") for _ in range(3)]
        resources.extend(targets)
        depth = ctx.depth_renderbuffer((w, h))
        resources.append(depth)
        fbo = ctx.framebuffer(color_attachments=targets, depth_attachment=depth)
        resources.append(fbo)
        program = ctx.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
        resources.append(program)
        program["u_size"].value = (float(w), float(h))
        fbo.use()
        fbo.clear(0, 0, 0, 0, depth=1.0)
        ctx.viewport = (0, 0, w, h)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.BLEND)
        ctx.disable(moderngl.CULL_FACE)
        ctx.depth_func = "<"
        if len(vertices):
            vbo = ctx.buffer(vertices.tobytes())
            resources.append(vbo)
            vao = ctx.vertex_array(program, [(vbo, "3f 3f 3f 3f", "in_current", "in_previous",
                                               "in_normal", "in_radiance")])
            resources.append(vao)
            vao.render(mode=moderngl.TRIANGLES)
        rgb_out, geometry, motion = [_read(t, (w, h)) for t in targets]
        provenance = {
            "schema": SCHEMA,
            "raster": "hardware_depth_test_float32_mrt",
            "shading": "shared_native_cpu_flat_facet_GGX_before_SDR_clipping",
            "driver": str(ctx.info.get("GL_RENDERER", "unknown")),
            "vendor": str(ctx.info.get("GL_VENDOR", "unknown")),
            "version": str(ctx.info.get("GL_VERSION", "unknown")),
            "backend": backend,
            "triangles": len(vertices) // 3,
            "shader_sha256": hashlib.sha256((VERTEX + FRAGMENT).encode("utf-8")).hexdigest(),
            "ambient": "configured_sRGB_to_linear_not_HDR_inferred",
            "velocity": "previous_native_projected_mesh" if previous_frame is not None else "not_requested",
        }
    finally:
        for resource in reversed(resources):
            resource.release()
        if ctx is not None:
            ctx.release()

    covered = rgb_out[..., 3] > 0.5
    background = srgb8_to_scene_linear(
        np.asarray([[renderer.background[:3]]], dtype=np.uint8))[0, 0]
    colour = np.empty((h, w, 3), dtype=np.float32)
    colour[...] = background
    colour[covered] = rgb_out[..., :3][covered]
    alpha = np.full((h, w), renderer.background[3] / 255, dtype=np.float32)
    alpha[covered] = 1
    frame_result = AOVFrame(colour, "Linear-sRGB", alpha, geometry[..., 0],
                            geometry[..., 1:4], motion[..., :2] if previous_frame is not None else None)
    return (frame_result, provenance) if return_evidence else frame_result


def parity_report(cpu, gpu, *, coverage_min=0.94, rgb_p99_max=0.02,
                  depth_p99_max=0.008, motion_p99_max=0.04):
    """Compare genuine float32 AOVs, excluding mismatched triangle-edge owners.

    Compare colour, normals, depth and motion only on jointly covered pixels
    with compatible surface normals. Report raw silhouette IoU independently.
    Never treat a missing AOV as a pass or convert values to RGB8 for testing.
    """
    import numpy as np

    if not isinstance(cpu, AOVFrame) or not isinstance(gpu, AOVFrame):
        raise ValueError("AOV parity requires both CPU and GPU AOVFrame inputs")
    if cpu.size != gpu.size or cpu.colour_space != gpu.colour_space:
        raise ValueError("AOV parity needs identical dimensions and colour spaces")
    if cpu.depth is None or gpu.depth is None or cpu.normals is None or gpu.normals is None:
        raise ValueError("AOV parity requires genuine depth and normals")
    if (cpu.velocity is None) != (gpu.velocity is None):
        raise ValueError("AOV parity requires identical velocity channel availability")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in
           (coverage_min, rgb_p99_max, depth_p99_max, motion_p99_max)):
        raise ValueError("Nonfinite parity thresholds")
    if not 0 < coverage_min <= 1 or min(rgb_p99_max, depth_p99_max, motion_p99_max) < 0:
        raise ValueError("Invalid parity thresholds")
    a, b = cpu.depth > 0, gpu.depth > 0
    overlap, union = a & b, a | b
    total = int(np.count_nonzero(union))
    if total == 0:
        raise ValueError("AOV parity fixture contains no rasterized geometry")
    iou = int(np.count_nonzero(overlap)) / total
    # Exclude pixels where different adjacent triangles won the depth test,
    # rather than conflating edge raster rules with material or motion error.
    aligned = overlap & (np.sum(cpu.normals * gpu.normals, axis=2) > 0.999)
    aligned &= np.abs(cpu.depth - gpu.depth) < 0.08
    compared = int(np.count_nonzero(aligned))
    if compared < max(10, total // 2):
        raise ValueError("Too few matching visible facets to qualify GPU parity")
    def p99(value):
        return float(np.percentile(value, 99))
    rgb_error = p99(np.max(np.abs(cpu.rgb[aligned] - gpu.rgb[aligned]), axis=1))
    depth_error = p99(np.abs(cpu.depth[aligned] - gpu.depth[aligned]))
    normal_error = p99(np.max(np.abs(cpu.normals[aligned] - gpu.normals[aligned]), axis=1))
    motion_error = (p99(np.max(np.abs(cpu.velocity[aligned] - gpu.velocity[aligned]), axis=1))
                    if cpu.velocity is not None else None)
    passed = (iou >= coverage_min and rgb_error <= rgb_p99_max
              and depth_error <= depth_p99_max and normal_error <= 0.01
              and (motion_error is None or motion_error <= motion_p99_max))
    return {
        "schema": PARITY_SCHEMA, "passed": bool(passed),
        "covered_cpu": int(np.count_nonzero(a)), "covered_gpu": int(np.count_nonzero(b)),
        "coverage_iou": float(iou), "compared_pixels": compared,
        "rgb_p99_absolute": rgb_error, "depth_p99_absolute": depth_error,
        "normal_p99_absolute": normal_error, "motion_p99_pixels": motion_error,
        "limits": {"coverage_min": coverage_min, "rgb_p99_max": rgb_p99_max,
                   "depth_p99_max": depth_p99_max, "motion_p99_max": motion_p99_max},
    }


def export_gpu_solid_exr(renderer, frame, output, *, previous_frame=None,
                         light_stops=0.0, backend="egl"):
    """Opt-in real GPU AOV capture, lossless independently verified EXR output."""
    result, gpu_provenance = render_solid_aov_gpu(renderer, frame,
        previous_frame=previous_frame, light_stops=light_stops,
        backend=backend, return_evidence=True)
    return write_exr(output, result, source_evidence=gpu_provenance)
