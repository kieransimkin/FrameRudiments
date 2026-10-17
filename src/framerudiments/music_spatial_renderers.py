"""Music-reactive PBR mesh scenes over native DanceRudiments/StemLab frames.

The source frame is the sole motion authority. `backend=gpu` requires a real
ModernGL/OpenGL 3.3 offscreen context; `backend=cpu` is a deterministic,
reduced-fidelity fallback for offline testing and machines without OpenGL.
"""

from array import array
from dataclasses import dataclass
import math

from PIL import Image, ImageFilter

from .io import fields, integer, number
from .solid_spatial_renderers import (
    _STYLE as _BASE_STYLE, _cross, _fog_pass, _normalized, _project,
    _raster_triangle, _rotate, _shade, _vertex, SolidSpatialRenderer,
)
from .terrain_field import _spectrum_profile, fbm, terrain_field


_ADVANCED_FIELDS = frozenset({
    "backend", "terrain_source", "terrain_height", "fft_floor_db", "fft_gain",
    "mesh_gain", "audio_displacement", "audio_frequency", "audio_twist",
    "audio_ripple_speed", "texture_scale", "texture_octaves", "bump_strength",
    "normal_strength", "weathering", "thermal_erosion", "hydraulic_erosion",
    "glaciation", "erosion_iterations", "seed", "snowline", "ice_coverage",
    "shadow_strength", "shadow_map_size", "light_power", "volumetric_steps",
    "scattering_density", "absorption_density", "anisotropy",
    "light_azimuth", "light_elevation", "camera_pitch", "spectral_deform",
    "spectral_gain",
})


@dataclass(frozen=True)
class SceneMesh:
    """Native vertex coordinates and triangles; OpenGL and CPU share this mesh."""
    positions: tuple[tuple[float, float, float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    terrain: object | None


class MusicSpatialRenderer:
    """Depth-tested, music-driven meshes with procedural geologic materials."""

    def __init__(self, size, style):
        fields(style, _BASE_STYLE | _ADVANCED_FIELDS, "music 3D renderer style")
        self.base = SolidSpatialRenderer(size, {k: v for k, v in style.items() if k in _BASE_STYLE})
        self.size, self.kind = self.base.size, self.base.kind
        self.background = self.base.background
        self.style = dict(style)
        self.backend = style.get("backend", "cpu")
        if self.backend not in {"cpu", "gpu", "auto"}:
            raise ValueError("3D backend must be cpu, gpu or auto")
        self.terrain_source = style.get("terrain_source", "procedural")
        if self.terrain_source not in {"procedural", "fft"}:
            raise ValueError("terrain_source must be procedural or fft")
        if self.terrain_source == "fft" and self.kind != "terrain":
            raise ValueError("FFT-driven row generation is only valid for 3D terrain")
        self.spectral_deform = style.get("spectral_deform", False)
        if type(self.spectral_deform) is not bool:
            raise ValueError("spectral_deform must be a boolean")
        self.spectral_gain = number(style.get("spectral_gain", .5), "spectral mesh displacement",
                                    minimum=0, maximum=4)
        self.requires_spectrum = self.terrain_source == "fft" or self.spectral_deform
        self.terrain_height = number(style.get("terrain_height", 1.0), "terrain height", minimum=0, maximum=5)
        self.fft_floor_db = number(style.get("fft_floor_db", -72), "FFT terrain floor", minimum=-120,
                                   maximum=-6)
        self.fft_gain = number(style.get("fft_gain", 1.0), "FFT terrain gain", minimum=0, maximum=8)
        self.mesh_gain = number(style.get("mesh_gain", 1.0), "mesh gain", minimum=0, maximum=4)
        self.audio_displacement = number(style.get("audio_displacement", 0.18), "audio vertex displacement",
                                         minimum=0, maximum=2)
        self.audio_frequency = number(style.get("audio_frequency", 4), "audio spatial frequency",
                                      minimum=.1, maximum=32)
        self.audio_twist = number(style.get("audio_twist", 0.2), "audio mesh twist", minimum=-4, maximum=4)
        self.audio_ripple_speed = number(style.get("audio_ripple_speed", .4), "audio ripple speed",
                                         minimum=0, maximum=12)
        self.texture_scale = number(style.get("texture_scale", 3.0), "texture scale", minimum=.01, maximum=64)
        self.texture_octaves = integer(style.get("texture_octaves", 4), "texture octaves",
                                       minimum=1, maximum=8)
        self.bump_strength = number(style.get("bump_strength", .24), "bump strength", minimum=0, maximum=4)
        self.normal_strength = number(style.get("normal_strength", .45), "normal strength",
                                      minimum=0, maximum=4)
        self.weathering = number(style.get("weathering", .3), "weathering", minimum=0, maximum=1)
        self.thermal_erosion = number(style.get("thermal_erosion", .2), "thermal erosion", minimum=0,
                                      maximum=1)
        self.hydraulic_erosion = number(style.get("hydraulic_erosion", .15), "hydraulic erosion",
                                        minimum=0, maximum=1)
        self.glaciation = number(style.get("glaciation", .25), "glaciation", minimum=0, maximum=1)
        self.erosion_iterations = integer(style.get("erosion_iterations", 4), "erosion iterations",
                                          minimum=0, maximum=24)
        self.seed = integer(style.get("seed", 17), "terrain seed", minimum=0, maximum=2147483647)
        self.snowline = number(style.get("snowline", .55), "snowline", minimum=-2, maximum=4)
        self.ice_coverage = number(style.get("ice_coverage", .35), "glacial ice coverage",
                                   minimum=0, maximum=1)
        self.shadow_strength = number(style.get("shadow_strength", .85), "shadow strength",
                                      minimum=0, maximum=1)
        self.shadow_map_size = integer(style.get("shadow_map_size", 512), "shadow map size",
                                       minimum=128, maximum=2048)
        self.light_power = number(style.get("light_power", 2.0), "directional light power",
                                  minimum=0, maximum=16)
        self.volumetric_steps = integer(style.get("volumetric_steps", 12), "volume integration steps",
                                        minimum=2, maximum=48)
        self.scattering_density = number(style.get("scattering_density", .14), "scattering coefficient",
                                         minimum=0, maximum=2)
        self.absorption_density = number(style.get("absorption_density", .04), "absorption coefficient",
                                         minimum=0, maximum=2)
        self.anisotropy = number(style.get("anisotropy", .2), "Henyey-Greenstein anisotropy",
                                 minimum=-.9, maximum=.9)
        self.camera_pitch = number(style.get("camera_pitch", 0), "camera pitch", minimum=-65, maximum=65)
        if "light_azimuth" in style or "light_elevation" in style:
            azimuth = math.radians(number(style.get("light_azimuth", -125), "light azimuth",
                                          minimum=-360, maximum=360))
            elevation = math.radians(number(style.get("light_elevation", 45), "light elevation",
                                            minimum=-85, maximum=85))
            self.base.light = _normalized(math.cos(elevation)*math.cos(azimuth),
                                          math.cos(elevation)*math.sin(azimuth),math.sin(elevation))
        self.frame_report = None
        self._gpu_renderer = None
        self._gpu_error = None

    def prepare(self, project):
        """Registry hook. Source association is provided by RudimentLayer."""
        return None

    def _geometry(self, frame):
        values = tuple(getattr(frame, "values", ()))
        if not values or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1.00001
                             for v in values):
            raise ValueError("Music 3D requires normalized finite native motion channels")
        context = frame.context
        t = number(getattr(context, "output_seconds", 0), "3D frame seconds", minimum=0)
        r, s = self.base.rings, self.base.segments
        terrain = None
        spectral_profile = (_spectrum_profile(frame, values, s + 1, self.fft_floor_db,
                                              self.fft_gain) if self.spectral_deform else None)
        if self.kind == "terrain":
            terrain = terrain_field(
                frame, r + 1, s + 1, source=self.terrain_source, floor_db=self.fft_floor_db,
                gain=self.fft_gain, seed=self.seed, weathering=self.weathering,
                thermal_erosion=self.thermal_erosion, hydraulic_erosion=self.hydraulic_erosion,
                glaciation=self.glaciation, erosion_iterations=self.erosion_iterations,
                amplitude=self.terrain_height,
            )
        vertices = []
        motion = values + (0.0, 0.0, 0.0)
        for row in range(r + 1):
            for col in range(s + 1):
                u, v = col / s, row / r
                if self.kind == "terrain":
                    x, y = 2.7 * (u - .5), 2.2 * (v - .5)
                    z = terrain.heights[row][col] - .32
                    if self.terrain_source != "fft":
                        z += .065 * (motion[1] * math.cos(u * 10 + t * self.audio_ripple_speed))
                else:
                    x, y, z = _vertex(self.kind, u, v, 1.0, t)
                angle = self.audio_twist * motion[1] * (v - .5)
                if angle:
                    x, y = (x * math.cos(angle) - y * math.sin(angle),
                            x * math.sin(angle) + y * math.cos(angle))
                # Native Rudiment channel displacement, spatially distributed
                # across every mesh, not merely a rotation of a static object.
                drive = (motion[0] * math.sin(2 * math.pi * self.audio_frequency * u
                                             - t * self.audio_ripple_speed)
                         + .6 * motion[1] * math.cos(2 * math.pi * self.audio_frequency * v)
                         + .35 * motion[2] * math.sin(2 * math.pi * (u + v)))
                delta = self.audio_displacement * self.mesh_gain * drive
                if spectral_profile is not None:
                    delta += self.spectral_gain * .18 * spectral_profile[col]
                if self.kind == "terrain":
                    z += delta
                else:
                    direction = _normalized(x, y, z)
                    x += delta * direction[0]
                    y += delta * direction[1]
                    z += delta * direction[2]
                # Actual geometry displacement and GPU bump mapping are
                # separate and both respond to native channels.
                bump = .025 * self.bump_strength * fbm(u * self.texture_scale * 5,
                                                       v * self.texture_scale * 5, seed=self.seed)
                x, y, z = x, y, z + bump
                vertices.append((x, y, z, u, v))
        triangles = []
        for row in range(r):
            for col in range(s):
                a = row * (s + 1) + col
                b = (row + 1) * (s + 1) + col
                c = a + 1
                d = b + 1
                triangles.extend(((a, b, c), (b, d, c)))
        return SceneMesh(tuple(vertices), tuple(triangles), terrain)

    def _material(self, center, normal, time):
        p = center
        n = fbm(p[0] * self.texture_scale, p[1] * self.texture_scale,
                seed=self.seed, octaves=self.texture_octaves)
        slope = 1 - abs(normal[2])
        age = max(0.0, self.weathering * (.5 + .5 * n))
        ice = max(0, min(1, self.ice_coverage * self.glaciation *
                         max(0, (p[2] - self.snowline + .15) * 1.8)))
        rock = (106 + 54 * n, 94 + 45 * n, 91 + 50 * n)
        moss = (51 + 30 * n, 101 + 34 * n, 70 + 22 * n)
        snow = (200 + 20 * n, 220 + 14 * n, 231 + 10 * n)
        blend = max(0, min(1, (.5 - slope) * (1 - age)))
        base = [rock[i] * (1 - blend) + moss[i] * blend for i in range(3)]
        snow_cover = max(0, min(1, (p[2] - self.snowline) * 1.8 + self.glaciation * .12))
        base = [base[i] * (1 - snow_cover) + snow[i] * snow_cover for i in range(3)]
        ice_colour = (146, 214, 232)
        return tuple(round(max(0, min(255, base[i] * (1 - ice) + ice_colour[i] * ice)))
                     for i in range(3))

    def _render_cpu(self, mesh, frame):
        w, h = self.size
        image = Image.new("RGBA", self.size, self.background)
        pixels = image.load()
        depths = array("f", [math.inf]) * (w * h)
        values = frame.values
        t = frame.context.output_seconds
        yaw = math.radians(self.base.rotation + self.base.orbit * t + values[0] * 45)
        pitch = (.32 + (values[1] if len(values) > 1 else values[0]) * .38
                 + math.radians(self.camera_pitch))
        focal = (min(w, h) - 2 * self.base.padding) / (2 * math.tan(math.radians(self.base.fov) / 2))
        transformed = [_rotate(*p[:3], yaw, pitch) for p in mesh.positions]
        projected = [_project(p, self.size, focal, self.base.distance) for p in transformed]
        covered = 0
        for tri in mesh.triangles:
            coords = [projected[i] for i in tri]
            if any(v is None for v in coords):
                continue
            p, q, r = (transformed[i] for i in tri)
            normal = _normalized(*_cross(tuple(q[n] - p[n] for n in range(3)),
                                         tuple(r[n] - p[n] for n in range(3))))
            center = tuple((p[i] + q[i] + r[i]) / 3 for i in range(3))
            # Approximate normal-map perturbation from procedural material.
            u = sum(mesh.positions[i][3] for i in tri) / 3
            v = sum(mesh.positions[i][4] for i in tri) / 3
            e = .02
            nx = fbm((u + e) * self.texture_scale, v * self.texture_scale, seed=self.seed)
            nx -= fbm((u - e) * self.texture_scale, v * self.texture_scale, seed=self.seed)
            ny = fbm(u * self.texture_scale, (v + e) * self.texture_scale, seed=self.seed)
            ny -= fbm(u * self.texture_scale, (v - e) * self.texture_scale, seed=self.seed)
            normal = _normalized(normal[0] - nx * self.normal_strength * .5,
                                 normal[1] - ny * self.normal_strength * .5, normal[2])
            base = self._material(center, normal, t)
            lit = _shade(normal, center, base, self.base.light, self.base.light_colour,
                         self.base.roughness, self.base.metallic,
                         self.base.ambient * self.light_power, self.base.distance)
            covered += _raster_triangle(pixels, depths, self.size, coords, (*lit, 255))
        if self.base.fog:
            image = _fog_pass(image, depths, distance=self.base.distance, intensity=self.base.fog,
                              colour=self.base.fog_colour, steps=self.base.fog_steps,
                              resolution=self.base.fog_resolution, time=t)
        if self.base.glow:
            image = Image.blend(image, image.filter(ImageFilter.GaussianBlur(2)), self.base.glow * .28)
        return image, covered

    def render(self, frame):
        mesh = self._geometry(frame)
        gpu_error = None
        if self.backend in ("gpu", "auto") and not (self.backend == "auto" and self._gpu_error):
            try:
                if self._gpu_renderer is None:
                    from .gpu_spatial_backend import GLMeshRenderer
                    self._gpu_renderer = GLMeshRenderer(self.size, self.shadow_map_size)
                image, covered = self._gpu_renderer.render(self, mesh, frame)
                used = "opengl-3.3"
            except Exception as exc:
                if self.backend == "gpu":
                    raise RuntimeError("Requested native GPU 3D backend is unavailable") from exc
                gpu_error = str(exc)
                self._gpu_error = gpu_error
                image, covered = self._render_cpu(mesh, frame)
                used = "cpu-fallback"
        else:
            image, covered = self._render_cpu(mesh, frame)
            used = "cpu-fallback" if self._gpu_error else "cpu"
            gpu_error = self._gpu_error
        self.frame_report = {
            "kind": "music-reactive-3d", "geometry": self.kind,
            "backend": used, "triangles": len(mesh.triangles), "covered_pixels": covered,
            "terrain_source": self.terrain_source if mesh.terrain is not None else None,
            "source_sha256": mesh.terrain.source_sha256 if mesh.terrain else None,
            "source_frames": mesh.terrain.source_frames if mesh.terrain else (),
            "movement_authority": "native DanceRudiments/StemLab frame values and history",
            "materials": "procedural fBm, bump/normal, weathered rock, snow and glacial ice",
            "shadows": ("directional shadow map with PCF" if used == "opengl-3.3"
                        else "not calculated in CPU preview"),
            "volume": ("shadow-aware Beer-Lambert + Henyey-Greenstein single scattering"
                       if used == "opengl-3.3" else "approximate CPU fog"),
        }
        if used == "opengl-3.3":
            self.frame_report["gl_renderer"] = self._gpu_renderer.renderer_info
            self.frame_report["gl_version"] = self._gpu_renderer.version_info
        if gpu_error:
            self.frame_report["gpu_fallback_reason"] = gpu_error
        return image

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        if self._gpu_renderer is not None:
            self._gpu_renderer.close()
            self._gpu_renderer = None


def register_music_spatial_renderers(registry):
    for shape in ("torus", "sphere", "terrain"):
        def factory(size, style, shape=shape):
            return MusicSpatialRenderer(size, dict(style, geometry=shape))
        registry.register("spatial-music-" + shape, factory)
