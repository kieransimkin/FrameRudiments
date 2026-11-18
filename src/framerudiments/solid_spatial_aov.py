"""M12.2: opt-in, native float32 AOV capture for CPU solid spatial meshes.

This is *not* inverse depth/normal estimation from RGB8. It replays the same
pure native mesh projection and the same depth-tested triangle rasteriser as
SolidSpatialRenderer.render(), but records the winning facet's linear GGX light,
camera Z, facing normal, and optionally exact prior-frame screen displacement.

The established display renderer, fog/glow, external effects, and GUI path are
unmodified. An AOV here is the pre-fog, pre-glow surface, with the configured
static background interpreted explicitly as sRGB (not invented HDR).
"""

from __future__ import annotations

from array import array
import math

from .colour_aov import AOVFrame, MAX_RGB_BYTES, srgb8_to_scene_linear
from .solid_spatial_renderers import _cross, _normalized, _project, _raster_triangle, _rotate, _vertex

SCHEMA = "framerudiments.solid-spatial-aov.v1"
MAX_CAPTURE_BYTES = MAX_RGB_BYTES


def _clock(frame):
    try:
        context = frame.context
        t = context.output_seconds
        values = tuple(frame.values)
    except (AttributeError, TypeError) as exc:
        raise ValueError("Solid AOV requires a native frame with context and values") from exc
    if (type(t) not in (int, float) or not math.isfinite(t) or t < 0
            or not values or any(type(v) not in (int, float) or not math.isfinite(v)
                                 or abs(v) > 1.00001 for v in values)):
        raise ValueError("Solid AOV requires finite normalized native values and output time")
    return t, values


def _project_mesh(renderer, frame):
    """Share the CPU native shape, orbit, displacement and projection formula."""
    t, values = _clock(frame)
    yaw = math.radians(renderer.rotation + renderer.orbit * t + values[0] * 45)
    pitch = 0.32 + (values[1] if len(values) > 1 else values[0]) * 0.38
    amplitude = 1 + renderer.depth * abs(values[0]) * 0.22
    w, h = renderer.size
    focal = (min(w, h) - 2 * renderer.padding) / (2 * math.tan(math.radians(renderer.fov) / 2))
    projected, world = {}, {}
    for row in range(renderer.rings + 1):
        for column in range(renderer.segments + 1):
            u, v = column / renderer.segments, row / renderer.rings
            xyz = _vertex(renderer.kind, u, v, amplitude, t + values[0])
            xyz = _rotate(*xyz, yaw, pitch)
            world[row, column] = xyz
            projected[row, column] = _project(xyz, (w, h), focal, renderer.distance)
    return projected, world


def _linear(channel):
    v = channel / 255
    return v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4


def _shade_linear(normal, point, base, renderer, light_stops):
    """The actual solid renderer's GGX direct-light terms, before SDR clipping."""
    view = _normalized(-point[0], -point[1], renderer.distance - point[2])
    if sum(a * b for a, b in zip(normal, view)) < 0:
        normal = tuple(-x for x in normal)
    light = renderer.light
    ndotl = max(0.0, sum(a * b for a, b in zip(normal, light)))
    ndotv = max(1e-4, sum(a * b for a, b in zip(normal, view)))
    half_vec = _normalized(*(a + b for a, b in zip(view, light)))
    ndoth = max(0.0, sum(a * b for a, b in zip(normal, half_vec)))
    vdoth = max(0.0, sum(a * b for a, b in zip(view, half_vec)))
    alpha = renderer.roughness * renderer.roughness
    alpha2 = alpha * alpha
    d = alpha2 / (math.pi * (ndoth * ndoth * (alpha2 - 1) + 1) ** 2 + 1e-8)
    k = (renderer.roughness + 1) ** 2 / 8
    g = (ndotv / (ndotv * (1 - k) + k)) * (ndotl / (ndotl * (1 - k) + k))
    direct_scale = 3.2 * (2.0 ** light_stops)
    rgb = []
    for channel, lamp in zip(base, renderer.light_colour):
        albedo, radiance = _linear(channel), _linear(lamp)
        f0 = 0.04 * (1 - renderer.metallic) + albedo * renderer.metallic
        fresnel = f0 + (1 - f0) * (1 - vdoth) ** 5
        specular = d * g * fresnel / max(1e-5, 4 * ndotv * max(ndotl, 1e-5))
        diffuse = (1 - fresnel) * (1 - renderer.metallic) * albedo / math.pi
        rgb.append(albedo * renderer.ambient * .45
                   + (diffuse + specular) * radiance * ndotl * direct_scale)
    return tuple(rgb), normal


class _PixelWriter:
    """Attach to *native* _raster_triangle at the moment its Z-test wins."""

    def __init__(self, rgb, alpha, depth, normals, velocity, zbuffer):
        self.rgb = rgb
        self.alpha = alpha
        self.depth = depth
        self.normals = normals
        self.velocity = velocity
        self.zbuffer = zbuffer
        self.width = rgb.shape[1]
        self.colour = (0.0, 0.0, 0.0)
        self.normal = (0.0, 0.0, 1.0)
        self.vertices = None
        self.prior = None

    def __setitem__(self, location, _colour_unused):
        x, y = location
        self.rgb[y, x] = self.colour
        self.alpha[y, x] = 1.0
        self.depth[y, x] = self.zbuffer[y * self.width + x]
        self.normals[y, x] = self.normal
        if self.velocity is not None:
            # Perspective-correct barycentrics identify the *same visible
            # surface point* in the preceding explicit native frame. This is
            # geometry motion, not optical-flow inference from display pixels.
            (x0, y0, d0), (x1, y1, d1), (x2, y2, d2) = self.vertices
            sx, sy = x + .5, y + .5
            area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
            p0 = ((x1 - sx) * (y2 - sy) - (y1 - sy) * (x2 - sx)) / area
            p1 = ((x2 - sx) * (y0 - sy) - (y2 - sy) * (x0 - sx)) / area
            p2 = 1 - p0 - p1
            q = (p0 / d0, p1 / d1, p2 / d2)
            total = sum(q)
            a, b, c = (v / total for v in q)
            # Reproject in homogeneous depth space: screen-coordinate
            # blending with 3D barycentrics alone is incorrect under
            # perspective, and invents motion on completely static meshes.
            dprev = a * self.prior[0][2] + b * self.prior[1][2] + c * self.prior[2][2]
            xp = (a * self.prior[0][0] * self.prior[0][2]
                  + b * self.prior[1][0] * self.prior[1][2]
                  + c * self.prior[2][0] * self.prior[2][2]) / dprev
            yp = (a * self.prior[0][1] * self.prior[0][2]
                  + b * self.prior[1][1] * self.prior[1][2]
                  + c * self.prior[2][1] * self.prior[2][2]) / dprev
            self.velocity[y, x] = (sx - xp, sy - yp)


def render_solid_aov(renderer, frame, *, previous_frame=None, light_stops=0.0):
    """Record native camera-space depth and normals (and optional motion).

    ``previous_frame`` MUST be the adjacent, explicitly supplied native source
    frame; it is never guessed from FPS, inferred from imagery, or cached.
    No request alters the legacy renderer's frame_report or display pixels.
    """
    from .solid_spatial_renderers import SolidSpatialRenderer
    if not isinstance(renderer, SolidSpatialRenderer):
        raise ValueError("M12.2 accepts only native SolidSpatialRenderer instances")
    if (type(light_stops) not in (float, int) or not math.isfinite(light_stops)
            or not -8 <= light_stops <= 8):
        raise ValueError("Solid AOV light_stops must be finite in [-8,8]")
    w, h = renderer.size
    components = 10 if previous_frame is not None else 8
    if w * h * components * 4 > MAX_CAPTURE_BYTES:
        raise ValueError("Solid AOV exceeds 512 MiB float32 capture budget")
    current_t, _ = _clock(frame)
    if previous_frame is not None:
        earlier_t, _ = _clock(previous_frame)
        now = getattr(frame.context, "frame", None)
        before = getattr(previous_frame.context, "frame", None)
        if (type(now) is not int or type(before) is not int or before != now - 1
                or earlier_t >= current_t):
            raise ValueError("Velocity requires adjacent native frame indices and increasing output time")

    import numpy as np  # Explicit M12 optional dependency, never imported by legacy render.
    background = srgb8_to_scene_linear(
        np.asarray([[renderer.background[:3]]], dtype=np.uint8))[0, 0]
    rgb = np.empty((h, w, 3), dtype=np.float32)
    rgb[...] = background
    alpha = np.full((h, w), renderer.background[3] / 255, dtype=np.float32)
    depth = np.zeros((h, w), dtype=np.float32)
    normals = np.zeros((h, w, 3), dtype=np.float32)
    velocity = np.zeros((h, w, 2), dtype=np.float32) if previous_frame is not None else None
    zbuffer = array("f", [math.inf]) * (w * h)
    writer = _PixelWriter(rgb, alpha, depth, normals, velocity, zbuffer)
    projected, world = _project_mesh(renderer, frame)
    prior = _project_mesh(renderer, previous_frame)[0] if previous_frame is not None else None

    for row in range(renderer.rings):
        for column in range(renderer.segments):
            a, b = (row, column), (row + 1, column)
            c, d = (row, column + 1), (row + 1, column + 1)
            for i, j, k in ((a, b, c), (b, d, c)):
                vertices = (projected[i], projected[j], projected[k])
                if any(p is None for p in vertices):
                    continue
                old_vertices = (prior[i], prior[j], prior[k]) if prior is not None else None
                if old_vertices is not None and any(p is None for p in old_vertices):
                    raise ValueError("Motion AOV cannot project the previous visible facet")
                p, q, r = world[i], world[j], world[k]
                normal = _normalized(*_cross(tuple(q[n] - p[n] for n in range(3)),
                                             tuple(r[n] - p[n] for n in range(3))))
                center = tuple((p[n] + q[n] + r[n]) / 3 for n in range(3))
                base = renderer.colours[(row * len(renderer.colours)) // renderer.rings]
                writer.colour, writer.normal = _shade_linear(normal, center, base, renderer, light_stops)
                writer.vertices, writer.prior = vertices, old_vertices
                _raster_triangle(writer, zbuffer, renderer.size, vertices, None)
    return AOVFrame(rgb, "Linear-sRGB", alpha, depth, normals, velocity)


def export_solid_exr(renderer, frame, output, *, previous_frame=None, light_stops=0.0):
    """Capture actual CPU solid-mesh AOVs and publish verified native EXR.

    Requires optional OpenEXR bindings only for the export; callers may inspect
    :func:`render_solid_aov` outputs without an EXR runtime. This is intentionally
    not the ordinary RGB8 ``renderer.render`` or project video render.
    """
    from .colour_aov import write_exr

    capture = render_solid_aov(renderer, frame, previous_frame=previous_frame,
                               light_stops=light_stops)
    provenance = {
        "schema": SCHEMA,
        "producer": "native-cpu-solid-triangle-raster",
        "geometry": renderer.kind,
        "colour": "GGX_scene_linear_before_fog_glow_or_output_AA",
        "depth": "positive_camera_distance_from_projection_zero_for_background",
        "normals": "camera_oriented_face_normals_zero_for_background",
        "velocity": ("previous_native_frame_to_current_screen_displacement_pixels"
                     if previous_frame is not None else "not_requested"),
        "output_frame": getattr(frame.context, "frame", None),
        "previous_frame": (getattr(previous_frame.context, "frame", None)
                           if previous_frame is not None else None),
        "light_stops": light_stops,
    }
    return write_exr(output, capture, source_evidence=provenance)
