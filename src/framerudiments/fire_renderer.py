"""Selectable fire methods behind one RGBA/burn contract; no silent fallback."""

from pathlib import Path
import math
from types import SimpleNamespace

from PIL import Image, ImageFilter

from .natural_materials import NaturalMaterial
from .io import file_ref, number, sha256
from .fire_assets import FireAtlas

METHODS = ("footage", "sprites", "fluid_2d", "volume", "mantaflow", "stylized")


def builtin_asset(name="marburg"):
    if name not in {"marburg", "mantaflow"}:
        raise ValueError("Unknown bundled flame asset")
    path = Path(__file__).with_name("fire_assets") / name / "atlas.json"
    if not path.is_file():
        raise RuntimeError(f"Bundled {name} atlas unavailable; supply a verified baked asset")
    return {"path": str(path), "sha256": sha256(path)}


class FireRenderer(NaturalMaterial):
    def __init__(
        self, size, method="footage", *, asset=None, repeat=True, warmth=0, backend_settings=None, **settings
    ):
        if method not in METHODS:
            raise ValueError(f"Unknown fire method; choose {METHODS}")
        super().__init__(size, "fire", **settings)
        if type(repeat) is not bool:
            raise ValueError("Fire repeat must be a boolean")
        self.method = method
        self.closed = False
        self.warmth = number(warmth, "fire warmth grade", minimum=0, maximum=1)
        self.repeat = repeat
        self.atlas = None
        self.backend = None
        self.inputs = []
        options = {} if backend_settings is None else backend_settings
        if not isinstance(options, dict):
            raise ValueError("Fire backend_settings must be an object")
        if method in {"footage", "sprites", "mantaflow"}:
            if options:
                raise ValueError("Atlas fire does not accept simulation settings")
            self.atlas = FireAtlas(
                asset or builtin_asset("mantaflow" if method == "mantaflow" else "marburg")
            )
            self.inputs = [self.atlas.reference, self.atlas.image_ref]
            if (
                method == "mantaflow"
                and self.atlas.evidence["provenance"].get("method") != "blender-mantaflow"
            ):
                self.atlas.close()
                raise ValueError("Mantaflow method requires its actual bake provenance")
        elif asset is not None:
            raise ValueError("Only footage, sprites and mantaflow accept an atlas")
        if method == "fluid_2d":
            from .fire_fluid import FluidFire

            self.backend = FluidFire(self.internal, seed=self.seed, **options)
        elif method == "volume":
            from .fire_volume import VolumeFire

            self.backend = VolumeFire(self.internal, seed=self.seed, **options)
        elif method == "stylized" and options:
            raise ValueError("Stylized fire uses ordinary material settings")
        if method == "sprites":
            self._make_emitter()
        self.evidence.update(
            method=method,
            warmth_grade=self.warmth,
            repeat=repeat,
            asset=self.atlas.evidence if self.atlas else None,
            backend="opengl"
            if method == "volume"
            else "numpy"
            if method in {"fluid_2d", "stylized"}
            else "pinned-atlas",
            backend_details=self.backend.evidence if self.backend else None,
            limits="Method-specific visual renderer; a successful render is not perceptual approval, measured combustion or rights validation",
        )
        modules = ["natural_materials.py", "fire_renderer.py", "fire_assets.py"]
        if method in {"fluid_2d", "volume"}:
            modules.append("fire_fluid.py" if method == "fluid_2d" else "fire_volume.py")
        if method == "sprites":
            modules.append("particle_engine.py")
        sources = [
            dict(path=str(Path(__file__).with_name(name)), sha256=sha256(Path(__file__).with_name(name)))
            for name in modules
        ]
        self.inputs.extend(sources)
        self.evidence["implementation"] = sources

    def _make_emitter(self):
        from .particle_engine import ParticleEmitter
        from .compositor import FrameContext
        from fractions import Fraction

        fps = 60
        horizon = 86400 * 8 + 2

        def context(n):
            return FrameContext(n, n / fps, n / fps, 0, None, None)

        refs = []
        p = SimpleNamespace(
            width=self.internal[0],
            height=self.internal[1],
            fps=fps,
            base=Path("."),
            inputs=refs,
            start_sample=0,
            end_sample=48000 * horizon,
            sample_rate=48000,
            repeats=1,
            frame_count=fps * horizon,
            slice_duration=Fraction(horizon),
            clock=None,
        )

        def ref(value):
            result = file_ref(value, p.base)
            refs.append(result)
            return result

        p.ref = ref
        config = {
            "id": "fire-sprites",
            "type": "emitter",
            "kind": "particles",
            "bounds": [0, 0, *self.internal],
            "particle_atlas": {k: self.atlas.reference[k] for k in ("path", "sha256")},
            "emission": {"mode": "continuous"},
            "end_seconds": horizon,
            "style": {
                "seed": self.seed,
                "rate": 55,
                "lifetime": 1.1,
                "size": max(8, min(70, self.internal[1] * 0.16)),
                "stretch": 2,
                "shape": "soft",
                "softness": 0.1,
                "size_growth": 0.15,
                "speed": 0.24,
                "direction": -90,
                "spread": 18,
                "wind": 0.025,
                "turbulence": 0.04,
                "emitter_x": 0.5,
                "emitter_y": 0.9,
                "emitter_width": 1,
                "emitter_height": 0.02,
                "rotation": -6,
                "rotation_randomness": 12,
                "spin": 0,
                "fade_in": 0.08,
                "fade_out": 0.6,
                "opacity": 0.8,
                "trail": 0,
                "colour": "#FFFFFF",
                "secondary_colour": "#FFFFFF",
            },
        }
        self.emitter = ParticleEmitter(p, config, context)
        self.emitter_context = context

    def _footage(self, seconds):
        image = Image.new("RGBA", self.size)
        h = max(8, round(self.size[1] * self.flame_height))
        cw, ch = self.atlas.cell_size
        w = max(4, round(h * cw / ch))
        count = math.ceil(self.size[0] / max(1, w * 0.72)) + 1
        for i in range(count):
            t = seconds + ((i * 73 + self.seed * 17) % 233) / 100
            tile = self.atlas.frame_at(t, repeat=self.repeat)
            tile = tile.convert("RGBa").resize((w, h), Image.Resampling.LANCZOS).convert("RGBA")
            x = round(i * w * 0.72 - w * 0.2)
            y = self.size[1] - h
            image.alpha_composite(tile, (x, y))
        return image

    def render(self, seconds, *, front=0.96, amount=1):
        if self.closed:
            raise RuntimeError("Fire renderer is closed")
        seconds = number(seconds, "fire source seconds", minimum=0, maximum=86400)
        front = number(front, "fire front", minimum=-0.5, maximum=1.5)
        amount = number(amount, "fire amount", minimum=0, maximum=1)
        if self.method == "stylized":
            return super().render(seconds, front=front, amount=amount)
        seconds *= self.speed
        if not amount:
            return Image.new("RGBA", self.size)
        if self.method in {"footage", "mantaflow"}:
            tile = self._footage(seconds)
        elif self.method == "sprites":
            t = seconds + 1.2
            canvas = Image.new("RGBA", self.internal)
            n = round(t * 60)
            self.emitter.draw(canvas, self.emitter_context(n))
            tile = canvas
        else:
            tile = self.backend.frame(seconds)
        if tile.size != self.size:
            tile = tile.convert("RGBa").resize(self.size, Image.Resampling.LANCZOS).convert("RGBA")
        if self.warmth and self.method in {"footage", "sprites", "mantaflow"}:
            np = self.np
            a = np.asarray(tile, dtype=np.float32) / 255
            # Grade straight RGB from the source emission. Alpha remains the
            # photographed/simulated silhouette so blue-screen fringes cannot
            # leak into a warm fire treatment.
            energy = np.max(a[:, :, :3], axis=2)
            warm = np.dstack(
                (
                    np.ones_like(energy),
                    np.clip(0.04 + energy * 0.58, 0, 1),
                    np.clip((energy - 0.84) * 4.5, 0, 1),
                )
            )
            a[:, :, :3] = a[:, :, :3] * (1 - self.warmth) + warm * self.warmth
            tile = Image.fromarray(np.rint(a * 255).astype(np.uint8))
        if front != 1:
            shifted = Image.new("RGBA", self.size)
            shifted.alpha_composite(tile, (0, round((front - 1) * self.size[1])))
            tile = shifted
        factor = amount * self.opacity
        tile.putalpha(tile.getchannel("A").point(lambda a: round(a * factor)))
        if self.glow:
            glow = tile.filter(ImageFilter.GaussianBlur(max(0.5, self.size[1] * 0.012)))
            glow.putalpha(glow.getchannel("A").point(lambda a: round(a * min(0.3, self.glow * 0.15))))
            glow.alpha_composite(tile)
            tile = glow
        return tile

    def close(self):
        if self.closed:
            return
        if self.backend:
            self.backend.close()
        if self.atlas:
            self.atlas.close()
        if hasattr(self, "emitter"):
            self.emitter.close()
        self.evidence["cleanup_verified"] = True
        self.closed = True
