"""Deterministic 3D wire geometry projected into an RGBA Rudiment viewport.

Pure Pillow/CPU perspective pipeline: deliberately no GPU claims, mesh engine,
physical volumetric solver or musical inference. Native motion channels are the
only animation inputs.
"""

import math

from PIL import Image, ImageColor, ImageDraw, ImageFilter

from .io import fields, number, integer

_ALLOWED = {
    "geometry",
    "camera_distance",
    "fov",
    "rotation",
    "orbit",
    "line_width",
    "fog",
    "glow",
    "background",
    "colours",
    "padding",
    "segments",
    "rings",
    "depth",
}


def _point(kind, u, v):
    a, b = 2 * math.pi * u, 2 * math.pi * v
    if kind == "torus":
        radius = 1.15 + 0.42 * math.cos(b)
        return radius * math.cos(a), radius * math.sin(a), 0.42 * math.sin(b)
    if kind == "sphere":
        return math.sin(b) * math.cos(a), math.sin(b) * math.sin(a), math.cos(b)
    # Wavy sheet: bounded depth variation, independent of PCM.
    return 2 * (u - 0.5), 2 * (v - 0.5), 0.24 * math.sin(a * 2) * math.cos(b * 2)


def _rotate(x, y, z, yaw, pitch):
    ca, sa = math.cos(yaw), math.sin(yaw)
    x, z = x * ca + z * sa, -x * sa + z * ca
    cb, sb = math.cos(pitch), math.sin(pitch)
    return x, y * cb - z * sb, y * sb + z * cb


class SpatialWireRenderer:
    """Depth-sorted line mesh with a perspective camera and simulated distance fog."""

    def __init__(self, size, style):
        fields(style, _ALLOWED, "3D renderer style")
        if len(size) != 2 or min(size) < 16:
            raise ValueError("3D renderer needs a viewport of at least 16 pixels")
        self.size = tuple(size)
        kind = style.get("geometry", "torus")
        if kind not in {"torus", "sphere", "terrain"}:
            raise ValueError("geometry must be torus, sphere or terrain")
        self.kind = kind
        self.distance = number(style.get("camera_distance", 4.8), "camera distance", minimum=2.5, maximum=30)
        self.fov = number(style.get("fov", 55), "camera fov", minimum=20, maximum=110)
        self.rotation = number(style.get("rotation", 0), "camera rotation", minimum=-360, maximum=360)
        self.orbit = number(style.get("orbit", 35), "camera orbit", minimum=-360, maximum=360)
        self.depth = number(style.get("depth", 0.7), "depth modulation", minimum=0, maximum=2)
        self.width = integer(style.get("line_width", 2), "line width", minimum=1, maximum=24)
        self.fog = number(style.get("fog", 0.22), "distance fog", minimum=0, maximum=1)
        self.glow = number(style.get("glow", 0.35), "wire glow", minimum=0, maximum=1)
        self.rings = integer(style.get("rings", 13), "3D mesh rings", minimum=3, maximum=48)
        self.segments = integer(style.get("segments", 30), "3D mesh segments", minimum=6, maximum=96)
        self.padding = integer(
            style.get("padding", 8), "3D viewport padding", minimum=0, maximum=min(size) // 4
        )
        self.background = ImageColor.getcolor(style.get("background", "#101b2e"), "RGBA")
        colors = style.get("colours", ["#68d9f4", "#fa7dbb"])
        if not isinstance(colors, list) or not 1 <= len(colors) <= 16:
            raise ValueError("colours must contain 1 to 16 CSS colours")
        self.colors = [ImageColor.getcolor(c, "RGB") for c in colors]
        self.frame_report = None

    def render(self, frame):
        values = tuple(getattr(frame, "values", ()))
        if not values or any(not math.isfinite(x) or abs(x) > 1.00001 for x in values):
            raise ValueError("3D wire renderer needs finite normalized Rudiment values")
        time = getattr(frame.context, "output_seconds", 0)
        yaw = math.radians(self.rotation + self.orbit * time + values[0] * 45)
        pitch = 0.27 + (values[1] if len(values) > 1 else values[0]) * 0.3
        amplitude = 1 + self.depth * abs(values[0]) * 0.3
        w, h = self.size
        focal = 0.5 * min(w, h) / math.tan(math.radians(self.fov) / 2)
        points = {}
        for a in range(self.rings + 1):
            for b in range(self.segments + 1):
                x, y, z = _point(self.kind, a / self.rings, b / self.segments)
                x, y, z = _rotate(x * amplitude, y * amplitude, z * amplitude, yaw, pitch)
                denominator = self.distance - z
                if denominator <= 0.01:
                    continue
                px, py = w / 2 + focal * x / denominator, h / 2 - focal * y / denominator
                points[(a, b)] = (px, py, z)
        strokes = []
        for a in range(self.rings + 1):
            for b in range(self.segments + 1):
                for target in ((a + 1, b), (a, b + 1)):
                    if target not in points or (a, b) not in points:
                        continue
                    p, q = points[(a, b)], points[target]
                    z = (p[2] + q[2]) / 2
                    attenuation = 1 - self.fog * max(0, min(1, (self.distance - z) / self.distance))
                    c = self.colors[(a * len(self.colors)) // (self.rings + 1)]
                    rgba = tuple(round(max(0, min(255, v * attenuation))) for v in c) + (210,)
                    strokes.append((z, p[:2], q[:2], rgba))
        strokes.sort(key=lambda row: row[0])
        ink = Image.new("RGBA", self.size)
        draw = ImageDraw.Draw(ink)
        for _, p, q, colour in strokes:
            draw.line((p, q), fill=colour, width=self.width)
        if self.glow:
            blurred = ink.filter(ImageFilter.GaussianBlur(max(1, self.width * 2)))
            blurred.putalpha(blurred.getchannel("A").point(lambda a: round(a * self.glow)))
            blurred.alpha_composite(ink)
            ink = blurred
        canvas = Image.new("RGBA", self.size, self.background)
        canvas.alpha_composite(ink)
        self.frame_report = {
            "kind": "procedural-perspective-wire",
            "geometry": self.kind,
            "source": "normalized-native-rudiment",
            "projected_edges": len(strokes),
        }
        return canvas


def register_spatial_renderers(registry):
    for kind in ("torus", "sphere", "terrain"):

        def factory(size, style, kind=kind):
            return SpatialWireRenderer(size, dict(style, geometry=kind))

        registry.register("spatial-" + kind, factory)
