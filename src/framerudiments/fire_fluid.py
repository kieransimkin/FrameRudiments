"""Bounded 2-D buoyant flow: semi-Lagrangian advection and pressure projection.

Original implementation of published numerical principles; not a calibrated
combustion model. Fixed simulation steps and deterministic replay own time.
"""

from collections import OrderedDict
import math

from PIL import Image

from .io import integer, number


def thermal_colour(temperature):
    """Three-wavelength Planck-inspired emission, fixed exposure (not thermometry)."""
    import numpy as np

    kelvin = 650 + 1900 * np.clip(temperature, 0, 1)
    wavelength = np.array([700e-9, 546e-9, 436e-9], dtype=np.float64)
    reference = 1 / (wavelength[0] ** 5 * np.expm1(0.01438776877 / (wavelength[0] * 2450)))
    emission = 1 / (wavelength**5 * np.expm1(0.01438776877 / (kelvin[:, :, None] * wavelength))) / reference
    return np.clip(1 - np.exp(-emission * 1.7), 0, 1) ** (1 / 2.2)


class FluidFire:
    def __init__(self, size, *, seed=7, simulation_hz=60, resolution=96, warmup=1.5):
        import numpy as np

        self.np = np
        self.size = size
        self.seed = integer(seed, "fluid seed", maximum=65535)
        self.hz = integer(simulation_hz, "fluid Hz", minimum=30, maximum=120)
        self.height = integer(resolution, "fluid resolution", minimum=48, maximum=192)
        self.width = max(48, min(256, round(self.height * size[0] / size[1])))
        self.warmup = number(warmup, "fluid warmup", minimum=0, maximum=4)
        self.yy, self.xx = np.mgrid[0 : self.height, 0 : self.width].astype(np.float32)
        self.checkpoints = OrderedDict()
        self._reset()
        self.evidence = {
            "method": "fluid_2d",
            "simulation_hz": self.hz,
            "field_size": [self.width, self.height],
            "pressure_iterations": 24,
            "warmup_seconds": self.warmup,
            "seed": self.seed,
            "physics": "buoyancy, vorticity confinement, semi-Lagrangian advection, pressure projection, cooling",
            "limits": "2-D visual fluid model, no chemical reaction solver or scientific validation; max30s per replay",
        }

    def _reset(self):
        np = self.np
        shape = (self.height, self.width)
        self.u = np.zeros(shape, dtype=np.float32)
        self.v = self.u.copy()
        self.heat = self.u.copy()
        self.fuel = self.u.copy()
        self.step_index = 0

    def _sample(self, field, x, y):
        np = self.np
        x = np.clip(x, 0, self.width - 1.001)
        y = np.clip(y, 0, self.height - 1.001)
        x0 = x.astype(np.int32)
        y0 = y.astype(np.int32)
        fx = x - x0
        fy = y - y0
        return (field[y0, x0] * (1 - fx) + field[y0, x0 + 1] * fx) * (1 - fy) + (
            field[y0 + 1, x0] * (1 - fx) + field[y0 + 1, x0 + 1] * fx
        ) * fy

    def _advect(self, field, u, v, dt):
        return self._sample(field, self.xx - u * dt, self.yy - v * dt).astype(self.np.float32)

    def project(self, u, v):
        np = self.np
        divergence = 0.5 * (np.roll(u, -1, 1) - np.roll(u, 1, 1) + np.roll(v, -1, 0) - np.roll(v, 1, 0))
        pressure = np.zeros_like(divergence)
        for _ in range(24):
            pressure = (
                np.roll(pressure, 1, 0)
                + np.roll(pressure, -1, 0)
                + np.roll(pressure, 1, 1)
                + np.roll(pressure, -1, 1)
                - divergence
            ) * 0.25
            pressure[0] = pressure[1]
            pressure[-1] = pressure[-2]
            pressure[:, 0] = pressure[:, 1]
            pressure[:, -1] = pressure[:, -2]
        u = u - 0.5 * (np.roll(pressure, -1, 1) - np.roll(pressure, 1, 1))
        v = v - 0.5 * (np.roll(pressure, -1, 0) - np.roll(pressure, 1, 0))
        u[:, 0] = 0
        u[:, -1] = 0
        v[0] = v[1]
        v[-1] = v[-2]
        return u.astype(np.float32), v.astype(np.float32)

    def _step(self):
        np = self.np
        dt = 1 / self.hz
        t = self.step_index * dt
        curl = 0.5 * (
            np.roll(self.v, -1, 1) - np.roll(self.v, 1, 1) - np.roll(self.u, -1, 0) + np.roll(self.u, 1, 0)
        )
        mag = np.abs(curl)
        nx = np.roll(mag, -1, 1) - np.roll(mag, 1, 1)
        ny = np.roll(mag, -1, 0) - np.roll(mag, 1, 0)
        norm = np.sqrt(nx * nx + ny * ny) + 1e-6
        nx /= norm
        ny /= norm
        self.u += ny * curl * dt * 4
        self.v += (-nx * curl * 4 - self.heat * 75) * dt
        self.u, self.v = self.project(self.u, self.v)
        u, v = self.u.copy(), self.v.copy()
        self.u = self._advect(u, u, v, dt) * 0.998
        self.v = self._advect(v, u, v, dt) * 0.998
        self.u, self.v = self.project(self.u, self.v)
        self.heat = self._advect(self.heat, self.u, self.v, dt) * math.exp(-dt * 0.85)
        self.fuel = self._advect(self.fuel, self.u, self.v, dt) * math.exp(-dt * 0.65)
        x = self.xx / self.width
        # Finite fuel ribbon with changing independent tongues; no global random state.
        source = (0.5 + 0.5 * np.sin(x * 29 + np.sin(t * 2.7 + self.seed) * 2.5)) ** 3
        source *= 0.55 + 0.45 * np.sin(x * 11 - t * 3.1 + self.seed) ** 2
        edge = np.clip((x - 0.04) * 20, 0, 1) * np.clip((0.96 - x) * 20, 0, 1)
        band = (self.yy > self.height - 5) & (self.yy < self.height - 1)
        feed = source * edge * band
        self.heat = np.maximum(self.heat, feed * 0.95)
        self.fuel = np.maximum(self.fuel, feed)
        self.v = np.where(band, -42 * (0.15 + source) * edge, self.v).astype(np.float32)
        self.heat[0] = 0
        self.fuel[0] = 0
        self.step_index += 1
        if self.step_index % 60 == 0:
            self.checkpoints[self.step_index] = tuple(
                a.copy() for a in (self.u, self.v, self.heat, self.fuel)
            )
            while len(self.checkpoints) > 8:
                self.checkpoints.popitem(last=False)

    def frame(self, seconds):
        seconds = number(seconds, "fluid source seconds", minimum=0, maximum=30)
        target = round((seconds + self.warmup) * self.hz)
        if target < self.step_index:
            earlier = [n for n in self.checkpoints if n <= target]
            if earlier:
                n = max(earlier)
                self.u, self.v, self.heat, self.fuel = (a.copy() for a in self.checkpoints[n])
                self.step_index = n
            else:
                self._reset()
        while self.step_index < target:
            self._step()
        np = self.np
        heat = np.clip(self.heat, 0, 1)
        alpha = np.clip(self.fuel * 2.2, 0, 1) * np.clip((heat - 0.1) * 2.8, 0, 1)
        rgb = thermal_colour(np.clip(heat * 1.3, 0, 1))
        rgba = np.dstack((rgb, alpha))
        image = Image.fromarray(np.rint(rgba * 255).astype(np.uint8))
        return image.convert("RGBa").resize(self.size, Image.Resampling.LANCZOS).convert("RGBA")

    def close(self):
        self.checkpoints.clear()
