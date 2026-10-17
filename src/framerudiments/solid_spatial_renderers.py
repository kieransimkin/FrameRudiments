"""Deterministic CPU 3D solid meshes driven by existing Rudiment channels.

A real per-pixel depth buffer resolves triangle occlusion. Camera, flat-shaded
material response and a low-resolution single-scattering fog pass are pure
functions of the source frame; no scene simulations or audio analysis occur here.
"""

from array import array
import math

from PIL import Image, ImageColor, ImageFilter

from .io import fields, integer, number

_STYLE = frozenset({
    "geometry", "camera_distance", "fov", "rotation", "orbit", "depth",
    "rings", "segments", "colours", "background", "padding",
    "ambient", "roughness", "metallic", "light_direction", "light_colour",
    "fog", "fog_colour", "fog_steps", "fog_resolution", "glow",
})


def _rgb(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a CSS colour string")
    return ImageColor.getcolor(value, "RGB")


def _normalized(x, y, z):
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 1e-10:
        return (0.0, 0.0, 1.0)
    return (x / length, y / length, z / length)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _vertex(shape, u, v, amplitude, wave):
    azimuth = 2 * math.pi * u
    if shape == "torus":
        minor = 2 * math.pi * v
        radius = 1.1 + 0.43 * math.cos(minor)
        return (radius * math.cos(azimuth) * amplitude,
                radius * math.sin(azimuth) * amplitude,
                0.43 * math.sin(minor) * amplitude)
    if shape == "sphere":
        latitude = math.pi * v
        return (math.sin(latitude) * math.cos(azimuth) * amplitude,
                math.sin(latitude) * math.sin(azimuth) * amplitude,
                math.cos(latitude) * amplitude)
    # Terrain is an open rectangular height field, not a periodic torus.
    x, y = 2.7 * (u - 0.5), 2.2 * (v - 0.5)
    z = (0.23 * math.sin(4 * math.pi * u + wave)
         * math.cos(3 * math.pi * v - wave * 0.4)
         + 0.12 * math.cos(9 * u + 7 * v)) * amplitude
    return x, y, z


def _rotate(x, y, z, yaw, pitch):
    c, s = math.cos(yaw), math.sin(yaw)
    x, z = x * c + z * s, -x * s + z * c
    c, s = math.cos(pitch), math.sin(pitch)
    return x, y * c - z * s, y * s + z * c


def _project(camera_xyz, size, focal, distance):
    x, y, z = camera_xyz
    depth = distance - z
    if depth <= 0.12:
        return None
    return (size[0] / 2 + focal * x / depth,
            size[1] / 2 - focal * y / depth, depth)


def _shade(normal, point, base, light, light_colour, roughness, metallic, ambient, distance):
    """Energy-conserving Cook-Torrance/GGX direct light for a flat mesh facet.

    Diffuse/specular terms are evaluated in linear RGB. This is a local PBR
    approximation without IBL, cast shadows or global illumination.
    """
    view = _normalized(-point[0], -point[1], distance - point[2])
    if sum(a * b for a, b in zip(normal, view)) < 0:
        normal = tuple(-x for x in normal)
    ndotl = max(0.0, sum(a * b for a, b in zip(normal, light)))
    ndotv = max(1e-4, sum(a * b for a, b in zip(normal, view)))
    half_vec = _normalized(*(a + b for a, b in zip(view, light)))
    ndoth = max(0.0, sum(a * b for a, b in zip(normal, half_vec)))
    vdoth = max(0.0, sum(a * b for a, b in zip(view, half_vec)))
    alpha = roughness * roughness
    alpha2 = alpha * alpha
    d = alpha2 / (math.pi * (ndoth * ndoth * (alpha2 - 1) + 1) ** 2 + 1e-8)
    k = (roughness + 1) ** 2 / 8
    g = (ndotv / (ndotv * (1 - k) + k)) * (ndotl / (ndotl * (1 - k) + k))

    def linear(channel):
        v = channel / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    def srgb(v):
        v = max(0.0, min(1.0, v))
        return round(255 * (12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055))

    rgb = []
    for channel, lamp in zip(base, light_colour):
        albedo, radiance = linear(channel), linear(lamp)
        f0 = 0.04 * (1 - metallic) + albedo * metallic
        fresnel = f0 + (1 - f0) * (1 - vdoth) ** 5
        specular = d * g * fresnel / max(1e-5, 4 * ndotv * max(ndotl, 1e-5))
        diffuse = (1 - fresnel) * (1 - metallic) * albedo / math.pi
        rgb.append(srgb(albedo * ambient * 0.45
                        + (diffuse + specular) * radiance * ndotl * 3.2))
    return tuple(rgb)

def _raster_triangle(out, zbuffer, size, vertices, colour):
    """Rasterise a projected triangle using perspective-correct reciprocal depth."""
    (x0, y0, d0), (x1, y1, d1), (x2, y2, d2) = vertices
    area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
    if abs(area) < 1e-8:
        return 0
    w, h = size
    xa = max(0, math.ceil(min(x0, x1, x2) - 0.5))
    xb = min(w - 1, math.floor(max(x0, x1, x2) - 0.5))
    ya = max(0, math.ceil(min(y0, y1, y2) - 0.5))
    yb = min(h - 1, math.floor(max(y0, y1, y2) - 0.5))
    if xa > xb or ya > yb:
        return 0
    pixels = 0
    inv = 1 / area
    for y in range(ya, yb + 1):
        sy = y + 0.5
        for x in range(xa, xb + 1):
            sx = x + 0.5
            p0 = ((x1 - sx) * (y2 - sy) - (y1 - sy) * (x2 - sx)) * inv
            if p0 < -1e-9:
                continue
            p1 = ((x2 - sx) * (y0 - sy) - (y2 - sy) * (x0 - sx)) * inv
            if p1 < -1e-9:
                continue
            p2 = 1 - p0 - p1
            if p2 < -1e-9:
                continue
            depth = 1 / (p0 / d0 + p1 / d1 + p2 / d2)
            index = y * w + x
            if depth < zbuffer[index]:
                zbuffer[index] = depth
                out[x, y] = colour
                pixels += 1
    return pixels


def _fog_pass(image, zbuffer, *, distance, intensity, colour, steps, resolution, time):
    """Approximate single scattering along each camera ray in bounded volume.

    Depth terminates rays at the closest visible triangle; empty pixels march
    toward the far background. Lower-resolution ray marching controls cost.
    """
    if intensity <= 0:
        return image
    w, h = image.size
    scale = min(1.0, resolution / max(w, h))
    fw, fh = max(1, round(w * scale)), max(1, round(h * scale))
    fog = Image.new("RGBA", (fw, fh))
    ink = fog.load()
    for yy in range(fh):
        y = min(h - 1, int((yy + 0.5) * h / fh))
        for xx in range(fw):
            x = min(w - 1, int((xx + 0.5) * w / fw))
            far = zbuffer[y * w + x]
            if not math.isfinite(far):
                far = distance * 1.9
            span = max(0, far - 0.12)
            if span == 0:
                continue
            dt = span / steps
            optical = 0.0
            for i in range(steps):
                travel = (i + 0.5) * dt
                # Deterministic, slowly moving low-frequency density structure.
                px = (x / w - 0.5) * travel
                py = (y / h - 0.5) * travel
                density = 0.72 + 0.16 * math.sin(2.3 * px + 1.4 * travel + time * 0.6)
                density += 0.12 * math.cos(2.8 * py - travel * 0.7 + time * 0.4)
                optical += max(0, density) * dt
            opacity = round(255 * (1 - math.exp(-optical * intensity / distance)))
            ink[xx, yy] = (*colour, max(0, min(255, opacity)))
    if (fw, fh) != (w, h):
        fog = fog.resize((w, h), Image.Resampling.BILINEAR)
    output = image.copy()
    output.alpha_composite(fog)
    return output


class SolidSpatialRenderer:
    """Depth-buffered solid torus, sphere, or animated height-field surface."""

    def __init__(self, size, style):
        fields(style, _STYLE, "solid spatial renderer style")
        if len(size) != 2 or any(type(v) is not int or v < 16 for v in size):
            raise ValueError("3D solid renderer needs integer viewport >= 16px")
        self.size = tuple(size)
        self.kind = style.get("geometry", "torus")
        if self.kind not in {"torus", "sphere", "terrain"}:
            raise ValueError("Unsupported solid 3D geometry")
        self.distance = number(style.get("camera_distance", 4.8), "3D camera distance", minimum=2.5, maximum=30)
        self.fov = number(style.get("fov", 55), "3D field of view", minimum=20, maximum=110)
        self.rotation = number(style.get("rotation", 0), "3D camera rotation", minimum=-360, maximum=360)
        self.orbit = number(style.get("orbit", 35), "3D orbit degrees/s", minimum=-360, maximum=360)
        self.depth = number(style.get("depth", 0.7), "3D audio displacement", minimum=0, maximum=2)
        self.rings = integer(style.get("rings", 14), "3D mesh rings", minimum=3, maximum=48)
        self.segments = integer(style.get("segments", 24), "3D mesh segments", minimum=6, maximum=72)
        self.padding = integer(style.get("padding", 8), "3D viewport padding", minimum=0,
                               maximum=min(size) // 4)
        self.ambient = number(style.get("ambient", 0.26), "3D ambient light", minimum=0, maximum=1)
        self.roughness = number(style.get("roughness", 0.42), "3D material roughness", minimum=0.06, maximum=1)
        self.metallic = number(style.get("metallic", 0.2), "3D material metallic", minimum=0, maximum=1)
        self.fog = number(style.get("fog", 0.18), "3D volumetric fog density", minimum=0, maximum=1)
        self.fog_steps = integer(style.get("fog_steps", 6), "3D ray march steps", minimum=2, maximum=24)
        self.fog_resolution = integer(style.get("fog_resolution", 240), "3D fog grid size",
                                      minimum=64, maximum=720)
        self.glow = number(style.get("glow", 0.18), "3D halo intensity", minimum=0, maximum=1)
        self.background = ImageColor.getcolor(style.get("background", "#101b2e"), "RGBA")
        self.fog_colour = _rgb(style.get("fog_colour", "#597ca3"), "fog_colour")
        self.light_colour = _rgb(style.get("light_colour", "#ffffff"), "light_colour")
        direction = style.get("light_direction", [-0.45, -0.35, 0.82])
        if (not isinstance(direction, (list, tuple)) or len(direction) != 3 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in direction) or
                sum(x * x for x in direction) < 1e-10):
            raise ValueError("light_direction must be a nonzero finite 3D vector")
        self.light = _normalized(*direction)
        colours = style.get("colours", ["#5bc9ef", "#f6a6c9", "#bea9fc"])
        if not isinstance(colours, list) or not 1 <= len(colours) <= 16:
            raise ValueError("colours must contain 1 to 16 CSS colours")
        self.colours = tuple(_rgb(c, "colours") for c in colours)
        self.frame_report = None

    def render(self, frame):
        values = tuple(getattr(frame, "values", ()))
        if not values or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1.00001
                             for v in values):
            raise ValueError("3D solid renderer requires finite normalized Rudiment values")
        t = number(getattr(frame.context, "output_seconds", 0), "3D frame time", minimum=0)
        yaw = math.radians(self.rotation + self.orbit * t + values[0] * 45)
        pitch = 0.32 + (values[1] if len(values) > 1 else values[0]) * 0.38
        amplitude = 1 + self.depth * abs(values[0]) * 0.22
        w, h = self.size
        focal = (min(w, h) - 2 * self.padding) / (2 * math.tan(math.radians(self.fov) / 2))
        projected, world = {}, {}
        for row in range(self.rings + 1):
            for column in range(self.segments + 1):
                u, v = column / self.segments, row / self.rings
                xyz = _vertex(self.kind, u, v, amplitude, t + values[0])
                xyz = _rotate(*xyz, yaw, pitch)
                world[(row, column)] = xyz
                projected[(row, column)] = _project(xyz, (w, h), focal, self.distance)
        canvas = Image.new("RGBA", (w, h), self.background)
        ink = canvas.load()
        zbuffer = array("f", [math.inf]) * (w * h)
        triangles = 0
        visible_pixels = 0
        for row in range(self.rings):
            for column in range(self.segments):
                a, b = (row, column), (row + 1, column)
                c, d = (row, column + 1), (row + 1, column + 1)
                for i, j, k in ((a, b, c), (b, d, c)):
                    verts = (projected[i], projected[j], projected[k])
                    if any(p is None for p in verts):
                        continue
                    p, q, r = world[i], world[j], world[k]
                    normal = _normalized(*_cross(tuple(q[n] - p[n] for n in range(3)),
                                                 tuple(r[n] - p[n] for n in range(3))))
                    center = tuple((p[n] + q[n] + r[n]) / 3 for n in range(3))
                    base = self.colours[(row * len(self.colours)) // self.rings]
                    material = _shade(normal, center, base, self.light, self.light_colour,
                                      self.roughness, self.metallic, self.ambient, self.distance)
                    visible_pixels += _raster_triangle(ink, zbuffer, (w, h), verts, (*material, 255))
                    triangles += 1
        image = _fog_pass(canvas, zbuffer, distance=self.distance, intensity=self.fog,
                          colour=self.fog_colour, steps=self.fog_steps,
                          resolution=self.fog_resolution, time=t)
        if self.glow:
            blurred = image.filter(ImageFilter.GaussianBlur(2))
            image = Image.blend(image, blurred, self.glow * 0.28)
        self.frame_report = {
            "kind": "procedural-solid-3d",
            "geometry": self.kind,
            "source": "normalized-native-rudiment",
            "depth_buffer": "per-pixel reciprocal camera depth",
            "shading": "linear-light Cook-Torrance GGX direct surface reflection",
            "fog": "depth-terminated bounded single-scattering approximation" if self.fog else "off",
            "triangles": triangles,
            "covered_pixels": visible_pixels,
        }
        return image


def register_solid_spatial_renderers(registry):
    for shape in ("torus", "sphere", "terrain"):
        def factory(size, style, shape=shape):
            return SolidSpatialRenderer(size, dict(style, geometry=shape))
        registry.register("spatial-solid-" + shape, factory)
