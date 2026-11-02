"""Deterministic fractal fields for the FrameRudiments compositor.

Fractal animation consumes resolved compositor controls only. Periodic controls
use the same fixed ``[-1, 1]`` range as native DanceRudiments samples; one
complete turn (``2*pi`` radians) occupies that range and the two endpoints are
the same phase.
"""
from __future__ import annotations

import math


NORMALIZED_PERIOD = (-1.0, 1.0)
ESCAPE_FRACTAL_FAMILIES = frozenset({
    "julia", "mandelbrot", "tricorn", "burning_ship", "celtic", "buffalo",
})
FRACTAL_FAMILIES = ESCAPE_FRACTAL_FAMILIES | {"newton"}

_FAMILY_CENTERS = {
    "mandelbrot": (-0.5, 0.0),
    "tricorn": (0.0, 0.0),
    "burning_ship": (-0.5, -0.5),
    "celtic": (-0.5, 0.0),
    "buffalo": (-0.5, 0.0),
}


def normalized_turn_radians(value: float) -> float:
    """Map a normalized DanceRudiment value to one wrapped turn in radians."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("normalized turn must be a finite number in [-1,1]")
    value = float(value)
    if not math.isfinite(value) or not -1 <= value <= 1:
        raise ValueError("normalized turn must be a finite number in [-1,1]")
    if value == 1:
        value = -1.0
    return math.pi * value


def _pair(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} needs two finite components")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in value):
        raise ValueError(f"{name} needs two finite components")
    return float(value[0]), float(value[1])


def _positive(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


def _nonnegative(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"{name} must be a finite nonnegative number")
    return float(value)


def _coordinates(u, v):
    import numpy as np

    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    if u.shape != v.shape or not np.all(np.isfinite(u)) or not np.all(np.isfinite(v)):
        raise ValueError("fractal coordinates must be finite arrays with matching shapes")
    return u, v


def _plane(u, v, *, zoom, rotation, center):
    zoom = _positive(zoom, "fractal zoom")
    center_x, center_y = _pair(center, "fractal center")
    angle = normalized_turn_radians(rotation)
    cosine, sine = math.cos(angle), math.sin(angle)
    real = (cosine * u + sine * v) / zoom + center_x
    imag = (-sine * u + cosine * v) / zoom + center_y
    return real.astype(complex) + 1j * imag


def julia_constant(center, orbit_radius: float, phase: float) -> complex:
    """Return the Julia constant after a normalized periodic orbit."""
    center_x, center_y = _pair(center, "Julia constant center")
    orbit_radius = _nonnegative(orbit_radius, "Julia orbit radius")
    angle = normalized_turn_radians(phase)
    return complex(center_x, center_y) + orbit_radius * complex(math.cos(angle), math.sin(angle))


def escape_time_field(
    u,
    v,
    *,
    family="mandelbrot",
    center=(0.0, 0.0),
    orbit_radius=0.0,
    phase=0.0,
    zoom=1.0,
    rotation=0.0,
    iterations=96,
    escape_radius=4.0,
    power=2,
):
    """Return a smooth normalized field for an escape-time fractal family.

    ``center`` is the Julia constant for ``family="julia"``. For every other
    family it is an additive pan from the family's useful default viewport.
    Interior points are zero; later escapes approach one. The function owns no
    clock, random state or image allocation.
    """
    import numpy as np

    if family not in ESCAPE_FRACTAL_FAMILIES:
        raise ValueError(f"unknown escape-time fractal family: {family!r}")
    u, v = _coordinates(u, v)
    if type(iterations) is not int or not 1 <= iterations <= 512:
        raise ValueError("fractal iterations must be an integer in [1,512]")
    if type(power) is not int or not 2 <= power <= 8:
        raise ValueError("fractal power must be an integer in [2,8]")
    if (isinstance(escape_radius, bool) or not isinstance(escape_radius, (int, float))
            or not math.isfinite(escape_radius) or escape_radius < 2):
        raise ValueError("fractal escape radius must be a finite number >=2")
    authored_center = _pair(center, "fractal center")
    orbit_radius = _nonnegative(orbit_radius, "fractal orbit radius")
    orbit_angle = normalized_turn_radians(phase)
    orbit = orbit_radius * complex(math.cos(orbit_angle), math.sin(orbit_angle))

    if family == "julia":
        plane = _plane(u, v, zoom=zoom, rotation=rotation, center=(0.0, 0.0))
        z = plane.copy()
        c = complex(*authored_center) + orbit
    else:
        base = _FAMILY_CENTERS[family]
        plane_center = (base[0] + authored_center[0] + orbit.real,
                        base[1] + authored_center[1] + orbit.imag)
        c = _plane(u, v, zoom=zoom, rotation=rotation, center=plane_center)
        z = np.zeros(c.shape, dtype=np.complex128)

    active = np.ones(z.shape, dtype=bool)
    result = np.zeros(z.shape, dtype=np.float64)
    log_power = math.log(power)

    for index in range(iterations):
        if not active.any():
            break
        current = z[active]
        if family == "burning_ship":
            base_value = np.abs(current.real) + 1j * np.abs(current.imag)
            powered = base_value * base_value if power == 2 else base_value ** power
        elif family == "tricorn":
            base_value = np.conjugate(current)
            powered = base_value * base_value if power == 2 else base_value ** power
        else:
            powered = current * current if power == 2 else current ** power
            if family == "celtic":
                powered = np.abs(powered.real) + 1j * powered.imag
            elif family == "buffalo":
                powered = np.abs(powered.real) + 1j * np.abs(powered.imag)
        updated = powered + (c if family == "julia" else c[active])
        z[active] = updated
        escaped_active = np.abs(updated) > escape_radius
        if not escaped_active.any():
            continue
        active_indices = np.flatnonzero(active)
        escaped_indices = active_indices[escaped_active]
        magnitude = np.abs(updated[escaped_active])
        smooth = index + 1 - np.log(np.log(magnitude)) / log_power
        result.flat[escaped_indices] = np.clip(smooth / iterations, 0, 1)
        active.flat[escaped_indices] = False
    return result


def julia_escape_field(
    u,
    v,
    *,
    constant=(-0.8, 0.156),
    orbit_radius=0.0,
    phase=0.0,
    zoom=1.0,
    rotation=0.0,
    iterations=96,
    escape_radius=4.0,
    power=2,
):
    """Backward-compatible Julia wrapper around :func:`escape_time_field`."""
    return escape_time_field(
        u, v, family="julia", center=constant, orbit_radius=orbit_radius,
        phase=phase, zoom=zoom, rotation=rotation, iterations=iterations,
        escape_radius=escape_radius, power=power,
    )


def newton_fractal_field(
    u,
    v,
    *,
    center=(0.0, 0.0),
    phase=0.0,
    zoom=1.0,
    rotation=0.0,
    iterations=48,
    degree=3,
    tolerance=1e-6,
    relaxation=1.0,
):
    """Return root-basin and convergence detail for ``z**degree = phase``."""
    import numpy as np

    u, v = _coordinates(u, v)
    if type(iterations) is not int or not 1 <= iterations <= 256:
        raise ValueError("Newton iterations must be an integer in [1,256]")
    if type(degree) is not int or not 3 <= degree <= 8:
        raise ValueError("Newton degree must be an integer in [3,8]")
    if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance) or not 1e-12 <= tolerance <= .1):
        raise ValueError("Newton tolerance must be finite in [1e-12,0.1]")
    if (isinstance(relaxation, bool) or not isinstance(relaxation, (int, float))
            or not math.isfinite(relaxation) or not .05 <= relaxation <= 2):
        raise ValueError("Newton relaxation must be finite in [0.05,2]")
    z = _plane(u, v, zoom=zoom, rotation=rotation, center=center)
    root_phase = normalized_turn_radians(phase)
    target = complex(math.cos(root_phase), math.sin(root_phase))
    roots = np.exp(1j * (root_phase + 2 * math.pi * np.arange(degree)) / degree)
    active = np.ones(z.shape, dtype=bool)
    result = np.zeros(z.shape, dtype=np.float64)

    for index in range(iterations):
        if not active.any():
            break
        current = z[active]
        derivative = degree * current ** (degree - 1)
        safe = np.abs(derivative) > 1e-14
        updated = current.copy()
        updated[safe] -= relaxation * ((current[safe] ** degree - target) / derivative[safe])
        z[active] = updated
        converged_active = np.abs(updated ** degree - target) <= tolerance
        if not converged_active.any():
            continue
        active_indices = np.flatnonzero(active)
        converged_indices = active_indices[converged_active]
        values = updated[converged_active]
        root_indices = np.argmin(np.abs(values[:, None] - roots[None, :]), axis=1)
        speed = 1 - index / iterations
        result.flat[converged_indices] = (root_indices + .1 + .8 * speed) / degree
        active.flat[converged_indices] = False
    return np.clip(result, 0, 1)
