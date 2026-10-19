"""Smooth radial Bezier petals over existing independent native motion channels.

Geometry only: StemLab owns smoothing/analysis and DanceRudiments owns sampling.
No recording is rephased, normalized, integrated, looped or interpreted as audio.
"""

import math

from PIL import ImageColor, ImageDraw, ImageFilter

from .io import integer, number
from .rudiment_renderers import _Viewport


class RangoliRenderer(_Viewport):
    """A reusable, bounded petal ring; stack rings for independent stem roles."""

    def __init__(self, size, style):
        super().__init__(size, style, {
            "petals", "radius", "petal_length", "petal_width", "angle",
            "line_width", "outline", "fill", "glow", "scale_gain",
            "rotation_gain", "dy_gain", "scale_axis", "rotation_axis", "dy_axis",
        })
        self.petals = integer(style.get("petals", 16), "petal count", minimum=3, maximum=128)
        self.radius = number(style.get("radius", .55), "rangoli radius", minimum=.01, maximum=1)
        self.length = number(style.get("petal_length", .5), "petal length", minimum=.01, maximum=1)
        self.width = number(style.get("petal_width", .14), "petal width", minimum=.001, maximum=.5)
        self.angle = number(style.get("angle", 0), "petal angle")
        self.stroke = integer(style.get("line_width", 2), "petal stroke", minimum=1, maximum=64)
        self.glow = number(style.get("glow", 0), "petal glow", minimum=0, maximum=.1)
        self.fill = ImageColor.getcolor(style.get("fill", "#dd33aa55"), "RGBA")
        self.outline = ImageColor.getcolor(style.get("outline", "#ffcf66ee"), "RGBA")
        self.scale_gain = number(style.get("scale_gain", .18), "petal scale gain", minimum=-.99, maximum=.99)
        self.rotation_gain = number(style.get("rotation_gain", 18), "petal rotation gain", minimum=-1e6, maximum=1e6)
        self.dy_gain = number(style.get("dy_gain", 0), "petal lift gain", minimum=-.5, maximum=.5)
        self.axes = tuple(integer(style.get(key, 0), key, maximum=255)
                          for key in ("scale_axis", "rotation_axis", "dy_axis"))
        # Normalized cubic source geometry, transformed directly in floating
        # point each frame. No expanded bitmap bounding box or integer resize.
        tip = (0, -1)
        base = (0, -1 + self.length)
        controls = [
            (tip, (self.width, -1 + self.length*.32),
             (self.width*.78, -1 + self.length*.72), base),
            (base, (-self.width*.78, -1 + self.length*.72),
             (-self.width, -1 + self.length*.32), tip),
        ]
        self.points = tuple(
            tuple((1-t)**3*p0[k] + 3*(1-t)**2*t*p1[k]
                  + 3*(1-t)*t*t*p2[k] + t**3*p3[k] for k in (0, 1))
            for p0, p1, p2, p3 in controls for t in (i/32 for i in range(33))
        )
        self.frame_report = None

    def render(self, frame):
        if max(self.axes) >= len(frame.values):
            raise ValueError("Rangoli axis is not present in selected native dimensions")
        scale_value, angle_value, dy_value = (frame.values[i] for i in self.axes)
        scale = 1 + scale_value*self.scale_gain
        radius = (min(self.size)-2*self.padding)*.5*self.radius*scale
        cx, cy = self.size[0]/2, self.size[1]/2 + dy_value*self.dy_gain*self.size[1]
        angle = (self.angle + angle_value*self.rotation_gain) % 360
        image = self.image()
        draw = ImageDraw.Draw(image)
        polygons = []
        for i in range(self.petals):
            a = math.radians(angle + i*360/self.petals)
            ca, sa = math.cos(a), math.sin(a)
            polygons.append(tuple((cx+radius*(x*ca-y*sa), cy+radius*(x*sa+y*ca))
                                  for x, y in self.points))
        # Fail on real geometry overflow rather than hiding it in a viewport.
        halo = math.ceil(self.glow*min(self.size)*3) + self.stroke + 1
        points = [p for polygon in polygons for p in polygon]
        if any(x < halo or y < halo or x >= self.size[0]-halo or y >= self.size[1]-halo
               for x, y in points):
            raise ValueError("Rangoli petals exceed viewport; reduce radius/gain or enlarge bounds")
        for polygon in polygons:
            draw.polygon(polygon, fill=self.fill)
            draw.line((*polygon, polygon[0]), fill=self.outline, width=self.stroke, joint="curve")
        if self.glow:
            glow = image.filter(ImageFilter.GaussianBlur(self.glow*min(self.size)))
            glow.alpha_composite(image)
            image = glow
        self.frame_report = dict(kind="rangoli-petals", petals=self.petals,
                                 radius_pixels=radius, scale=scale, rotation_degrees=angle,
                                 source_seconds=getattr(frame.context, "source_seconds", None),
                                 axes=list(self.axes))
        return image
