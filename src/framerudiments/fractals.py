"""Deterministic fractal fields for the FrameRudiments compositor.

Fractal animation consumes resolved compositor controls only.  In particular,
periodic controls use the same fixed ``[-1, 1]`` range as native
DanceRudiments samples; one complete turn (``2*pi`` radians) occupies that
range and the two endpoints are the same phase.
"""
from __future__ import annotations

import math


NORMALIZED_PERIOD = (-1.0, 1.0)


def normalized_turn_radians(value: float) -> float:
    """Map a normalized DanceRudiment value to one wrapped turn in radians.

    ``-1`` and ``+1`` deliberately produce the identical phase.  Keeping the
    public control normalized avoids mixing radians with stored recording data
    and lets every named/table/recording motion use the existing modulator.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("normalized turn must be a finite number in [-1,1]")
    value = float(value)
    if not math.isfinite(value) or not -1 <= value <= 1:
        raise ValueError("normalized turn must be a finite number in [-1,1]")
    # Canonicalise the joined endpoint before multiplication so direct renders
    # at -1 and +1 are byte-identical rather than merely visually equivalent.
    if value == 1:
        value = -1.0
    return math.pi * value


def julia_constant(center, orbit_radius: float, phase: float) -> complex:
    """Return the Julia constant after a normalized periodic orbit."""
    if not isinstance(center, (list, tuple)) or len(center) != 2:
        raise ValueError("Julia constant center needs two finite components")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in center):
        raise ValueError("Julia constant center needs two finite components")
    if (isinstance(orbit_radius, bool) or not isinstance(orbit_radius, (int, float))
            or not math.isfinite(orbit_radius) or orbit_radius < 0):
        raise ValueError("Julia orbit radius must be a finite nonnegative number")
    angle = normalized_turn_radians(phase)
    return complex(center[0], center[1]) + float(orbit_radius) * complex(
        math.cos(angle), math.sin(angle)
    )


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
    """Return a smooth normalized Julia escape field for matching coordinate arrays.

    Interior points are zero.  Escaped points approach one as their escape is
    delayed, which makes the existing multi-stop palette renderer useful around
    the boundary.  This function owns no clock, motion engine, random state or
    image allocation; the effect graph supplies transformed coordinates and
    consumes the returned ``0..1`` field.
    """
    import numpy as np

    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    if u.shape != v.shape or not np.all(np.isfinite(u)) or not np.all(np.isfinite(v)):
        raise ValueError("Julia coordinates must be finite arrays with matching shapes")
    if (isinstance(zoom, bool) or not isinstance(zoom, (int, float))
            or not math.isfinite(zoom) or zoom <= 0):
        raise ValueError("Julia zoom must be a finite positive number")
    if type(iterations) is not int or not 1 <= iterations <= 512:
        raise ValueError("Julia iterations must be an integer in [1,512]")
    if type(power) is not int or not 2 <= power <= 8:
        raise ValueError("Julia power must be an integer in [2,8]")
    if (isinstance(escape_radius, bool) or not isinstance(escape_radius, (int, float))
            or not math.isfinite(escape_radius) or escape_radius < 2):
        raise ValueError("Julia escape radius must be a finite number >=2")

    angle = normalized_turn_radians(rotation)
    cosine, sine = math.cos(angle), math.sin(angle)
    real = (cosine * u + sine * v) / zoom
    imag = (-sine * u + cosine * v) / zoom
    z = real.astype(np.complex128) + 1j * imag
    c = julia_constant(constant, orbit_radius, phase)
    active = np.ones(z.shape, dtype=bool)
    result = np.zeros(z.shape, dtype=np.float64)
    log_power = math.log(power)

    for index in range(iterations):
        if not active.any():
            break
        current = z[active]
        updated = current * current + c if power == 2 else current ** power + c
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
