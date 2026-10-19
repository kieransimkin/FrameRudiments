"""Bounded, seek-safe procedural fire and ice; no fluid or thermodynamic claim.

Advected multi-scale density creates connected flames rather than triangle
sprites. Cellular fracture, frost and moving surface light describe ice.
NumPy is an explicit optional dependency. No models, downloads or wall clock.
"""

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

from .io import fields, integer, number, rectangle, sha256
from .keyframes import sample_envelope, validate_envelope


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Natural materials require framerudiments[materials]") from exc
    return np


class NaturalMaterial:
    """Reusable RGBA tile and alpha-preserving burn/freeze operations.

    Identical seed, source time and settings reproduce a frame regardless of
    call order. Render at a declared internal resolution, then Lanczos upscale.
    """

    def __init__(self, size, kind="fire", **settings):
        self.np = np = _numpy()
        if kind not in {"fire", "ice"}:
            raise ValueError("Natural material must be fire or ice")
        self.kind = kind
        if not isinstance(size, (list, tuple)) or len(size) != 2:
            raise ValueError("Material size requires width and height")
        self.size = tuple(integer(v, "material dimension", minimum=2, maximum=4096) for v in size)
        if math.prod(self.size) > 4_194_304:
            raise ValueError("Material output exceeds four megapixels; use bounded tiles")
        fields(
            settings,
            {"seed", "resolution", "scale", "speed", "turbulence", "glow", "opacity", "flame_height"},
            "material settings",
        )
        self.seed = integer(settings.get("seed", 7), "material seed", maximum=65535)
        self.settings = {"seed": self.seed}
        ranges = {
            "resolution": (0.1, 1, 0.5),
            "scale": (0.25, 8, 1),
            "speed": (0, 8, 1),
            "turbulence": (0, 2, 0.8),
            "glow": (0, 2, 0.65),
            "opacity": (0, 1, 1),
            "flame_height": (0.05, 1.5, 0.65),
        }
        for key, (lo, hi, default) in ranges.items():
            value = number(settings.get(key, default), "material " + key, minimum=lo, maximum=hi)
            setattr(self, key, value)
            self.settings[key] = value
        self.internal = tuple(max(2, round(v * self.resolution)) for v in self.size)
        if math.prod(self.internal) > 1_048_576:
            raise ValueError("Material internal field exceeds one megapixel; lower resolution")
        self.x, self.y = np.meshgrid(
            np.linspace(0, self.size[0] / self.size[1], self.internal[0], dtype=np.float32),
            np.linspace(0, 1, self.internal[1], dtype=np.float32),
        )
        self.evidence = {
            "kind": kind,
            "backend": "numpy",
            "numpy": np.__version__,
            "size": self.size,
            "internal_size": self.internal,
            "settings": self.settings,
            "method": "advected multiscale density / cellular fracture and frost",
            "limits": "Procedural appearance, not fluid dynamics, combustion, physical refraction or acoustic evidence",
        }

    def _smooth(self, a, b, x):
        u = self.np.clip((x - a) / (b - a), 0, 1)
        return u * u * (3 - 2 * u)

    def _hash(self, x, y):
        np = self.np
        v = np.sin(x * 127.1 + y * 311.7 + self.seed * 17.713) * 43758.5453
        return v - np.floor(v)

    def _noise(self, x, y):
        np = self.np
        ix, iy = np.floor(x), np.floor(y)
        fx, fy = x - ix, y - iy
        ux, uy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        a, b = self._hash(ix, iy), self._hash(ix + 1, iy)
        c, d = self._hash(ix, iy + 1), self._hash(ix + 1, iy + 1)
        return (a + (b - a) * ux) * (1 - uy) + (c + (d - c) * ux) * uy

    def _fbm(self, x, y, octaves=4):
        result = self.np.zeros(self.internal[::-1], dtype=self.np.float32)
        weight = 0.533333
        for _ in range(octaves):
            result += weight * self._noise(x, y)
            x, y = x * 2.03 + 13.1, y * 2.01 + 7.7
            weight *= 0.5
        return result

    def _time(self, seconds):
        return number(seconds, "material source seconds", minimum=0, maximum=86400) * self.speed

    def _image(self, rgb, alpha):
        np = self.np
        rgba = np.dstack((np.clip(rgb, 0, 1), np.clip(alpha, 0, 1) * self.opacity))
        img = Image.fromarray(np.rint(rgba * 255).astype(np.uint8))
        if img.size != self.size:
            # Premultiplied resampling avoids coloured fringes on transparent edges.
            img = img.convert("RGBa").resize(self.size, Image.Resampling.LANCZOS).convert("RGBA")
        return img

    def _fire(self, seconds, front):
        np = self.np
        t = self._time(seconds)
        x = self.x * self.scale
        edge = (self._fbm(self.x * 11, self.y * 9) - 0.5) * 0.065
        h = (front - self.y - edge) / self.flame_height
        flow = self._fbm(x * 3.2, h * 2.0 - t * 0.65)
        warp = (flow - 0.5) * self.turbulence
        fine = self._fbm(x * 8 + warp * 2, h * 3.4 - t * 2.6)
        broad = self._noise(x * 5 + warp, t * 0.55 + self.y * 1.1)
        density = 0.92 - h * 0.95 + (fine - 0.48) * 0.94 + (broad - 0.5) * 0.55
        alpha = (
            self._smooth(0.22, 0.62, density)
            * self._smooth(-0.015, 0.025, h)
            * (1 - self._smooth(0.65, 1.1, h))
        )
        heat = np.clip(density * 0.7 + (1 - h) * 0.45, 0, 1)
        # Blackbody-inspired colour ramp with hotter ivory cores and red, thin tips.
        red = np.clip(1.65 * heat + 0.3, 0, 1)
        green = np.clip((heat - 0.15) * 1.35, 0, 1) ** 1.35
        blue = np.clip((heat - 0.68) * 2.9, 0, 1) ** 1.4
        rgb = np.dstack((red, green, blue))
        return self._image(rgb, alpha)

    def _ice(self, seconds, amount):
        np = self.np
        t = self._time(seconds)
        x = self.x * self.scale * 7
        y = self.y * self.scale * 7
        warp = (self._fbm(x * 0.6, y * 0.6) - 0.5) * self.turbulence * 0.7
        x = x + warp
        y = y + warp * 0.6
        gx, gy = np.floor(x), np.floor(y)
        f1 = np.full_like(x, 100)
        f2 = f1.copy()
        for j in (-1, 0, 1):
            for i in (-1, 0, 1):
                cx, cy = gx + i, gy + j
                px = cx + self._hash(cx, cy)
                py = cy + self._hash(cx + 29.3, cy + 71.8)
                distance = (x - px) ** 2 + (y - py) ** 2
                f2 = np.minimum(f2, np.maximum(f1, distance))
                f1 = np.minimum(f1, distance)
        cracks = np.exp(-(((np.sqrt(f2) - np.sqrt(f1)) / 0.028) ** 2))
        frost = self._fbm(x * 2.1, y * 2.1, 3)
        facets = 0.5 + 0.5 * np.sin((x + y * 0.4) * 2.0 + t * 0.35)
        specular = np.clip(facets - 0.89, 0, 1) * 7 * (0.3 + frost)
        grain = self._noise(x * 28, y * 28)
        coverage = self._smooth(0.0, 0.18, amount - (0.25 + 0.65 * frost) * (1 - amount))
        rgb = np.dstack(
            (
                0.13 + 0.47 * frost + 0.65 * cracks + specular,
                0.35 + 0.48 * frost + 0.6 * cracks + specular,
                0.55 + 0.4 * frost + 0.45 * cracks + specular,
            )
        )
        alpha = coverage * (0.22 + 0.35 * frost + 0.4 * cracks + grain * 0.08)
        return self._image(rgb, alpha)

    def render(self, seconds, *, front=0.96, amount=1):
        front = number(front, "material front", minimum=-0.5, maximum=1.5)
        amount = number(amount, "material amount", minimum=0, maximum=1)
        self._time(seconds)  # Validate even at zero amount.
        if not amount:
            return Image.new("RGBA", self.size)
        img = self._fire(seconds, front) if self.kind == "fire" else self._ice(seconds, amount)
        if self.kind == "fire" and amount < 1:
            img.putalpha(img.getchannel("A").point(lambda v: round(v * amount)))
        if self.glow:
            glow = img.filter(ImageFilter.GaussianBlur(max(0.5, self.size[1] * 0.018)))
            glow.putalpha(glow.getchannel("A").point(lambda v: round(v * min(1, self.glow * 0.5))))
            glow.alpha_composite(img)
            img = glow
        return img

    def burn(self, image, seconds, progress):
        if self.kind != "fire":
            raise ValueError("Burn requires the fire material")
        if image.mode != "RGBA" or image.size != self.size:
            raise ValueError("Burn needs same-sized RGBA artwork")
        p = number(progress, "burn progress", minimum=0, maximum=1)
        self._time(seconds)
        if p == 0:
            return image.copy()
        if p == 1:
            return Image.new("RGBA", self.size)
        np = self.np
        front = 1 - p
        rough = (self._fbm(self.x * 11, self.y * 9) - 0.5) * 0.065
        keep = 1 - self._smooth(front - 0.007, front + 0.007, self.y + rough)
        char = np.exp(-(((self.y + rough - front) / 0.045) ** 2)) * 0.87
        mask = Image.fromarray(np.rint(keep * 255).astype(np.uint8)).resize(
            self.size, Image.Resampling.LANCZOS
        )
        scorch = Image.fromarray(np.rint(char * 210).astype(np.uint8)).resize(
            self.size, Image.Resampling.BILINEAR
        )
        result = Image.composite(Image.new("RGBA", self.size, (29, 8, 3, 255)), image, scorch)
        result.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
        flame = self.render(seconds, front=front, amount=min(1, p * 12, (1 - p) * 8))
        result.alpha_composite(flame)
        return result

    def freeze(self, image, seconds, progress):
        if self.kind != "ice":
            raise ValueError("Freeze requires the ice material")
        if image.mode != "RGBA" or image.size != self.size:
            raise ValueError("Freeze needs same-sized RGBA artwork")
        p = number(progress, "freeze progress", minimum=0, maximum=1)
        overlay = self.render(seconds, amount=p)
        overlay.putalpha(ImageChops.multiply(overlay.getchannel("A"), image.getchannel("A")))
        result = image.copy()
        result.alpha_composite(overlay)
        result.putalpha(image.getchannel("A"))
        return result


class NaturalMaterialLayer:
    """Ordinary bounded layer; compatible with existing masks/routing/keyframes."""

    def __init__(self, project, cfg, context_at):
        fields(
            cfg,
            {
                "id",
                "type",
                "kind",
                "bounds",
                "style",
                "opacity",
                "start_seconds",
                "end_seconds",
                "progress",
                "method",
                "asset",
                "repeat",
                "backend_settings",
            },
            "material layer",
        )
        self.id = cfg.get("id")
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Material layer needs an id")
        raw = rectangle(cfg["bounds"], "material bounds", project.width, project.height)
        if any(int(v) != v for v in raw):
            raise ValueError("Material bounds must be integer pixels")
        self.bounds = tuple(map(int, raw))
        self.motion = None
        self.frame_report = None
        self.start = number(cfg.get("start_seconds", 0), "material start", minimum=0)
        self.end = number(cfg.get("end_seconds", 86400), "material end", minimum=self.start)
        self.opacity = number(cfg.get("opacity", 1), "material opacity", minimum=0, maximum=1)
        p = cfg.get("progress", 1)
        self.progress = (
            validate_envelope(p)
            if isinstance(p, dict)
            else number(p, "material progress", minimum=0, maximum=1)
        )
        size = (self.bounds[2] - self.bounds[0], self.bounds[3] - self.bounds[1])
        if cfg.get("kind", "fire") not in {"fire", "ice"}:
            raise ValueError("Material kind must be fire or ice")
        if cfg.get("kind", "fire") == "fire":
            from .fire_renderer import FireRenderer

            self.material = FireRenderer(
                size,
                method=cfg.get("method", "footage"),
                asset=cfg.get("asset"),
                repeat=cfg.get("repeat", True),
                backend_settings=cfg.get("backend_settings"),
                **cfg.get("style", {}),
            )
            if self.material.method == "fluid_2d" and self.end * self.material.speed > 30:
                self.material.close()
                raise ValueError("2D fluid layer requires end_seconds * speed <=30; bake an atlas for longer source time")
            project.inputs.extend(self.material.inputs)
        else:
            if any(k in cfg for k in ("method", "asset", "repeat", "backend_settings")):
                raise ValueError("Fire backend settings require kind fire")
            self.material = NaturalMaterial(size, "ice", **cfg.get("style", {}))
        self.evidence = self.material.evidence
        project.ref({"path": str(Path(__file__).resolve()), "sha256": sha256(__file__)})

    def draw(self, image, ctx):
        self.frame_report = None
        if not self.start <= ctx.source_seconds < self.end:
            return None
        amount = (
            sample_envelope(self.progress, ctx.source_seconds)
            if isinstance(self.progress, tuple)
            else self.progress
        )
        tile = self.material.render(ctx.source_seconds, amount=amount)
        if self.opacity != 1:
            tile.putalpha(tile.getchannel("A").point(lambda v: round(v * self.opacity)))
        bb = tile.getchannel("A").getbbox()
        self.frame_report = {
            "kind": self.material.kind,
            "source_seconds": ctx.source_seconds,
            "amount": amount,
            "bounds": self.bounds,
        }
        if not bb:
            return None
        image.alpha_composite(tile, self.bounds[:2])
        return [
            self.bounds[0] + bb[0],
            self.bounds[1] + bb[1],
            self.bounds[0] + bb[2],
            self.bounds[1] + bb[3],
        ]

    def close(self):
        close = getattr(self.material, "close", None)
        if close:
            close()
