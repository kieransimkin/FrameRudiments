"""Shared deterministic refractive fields for raindrop layers and transitions.

The functions operate on caller-supplied coordinate tiles.  They contain no
clock or mutable simulation state, so random-access rendering and chunk replay
produce identical pixels.  Three variants deliberately cover different common
implementations: an analytical travelling ring packet, dome-normal droplet
lenses, and seeded multi-source interference.
"""
from __future__ import annotations

import math

RIPPLE_VARIANTS = frozenset({'rings', 'droplets', 'storm'})


def _centres(size, centre, count, seed, variant):
    """Return stable pixel centres, onset phases and radius scales."""
    import numpy as np

    width, height = size
    cx, cy = centre[0] * width - .5, centre[1] * height - .5
    if variant == 'rings':
        return [(cx, cy, 0.0, 1.0)]
    rng = np.random.default_rng(seed)
    rows = [(cx, cy, 0.0, 1.0)]
    for _ in range(max(0, count - 1)):
        rows.append((
            float(rng.uniform(-.05, 1.05) * width - .5),
            float(rng.uniform(-.05, 1.05) * height - .5),
            float(rng.uniform(0, .42 if variant == 'droplets' else .28)),
            float(rng.uniform(.45, 1.0)),
        ))
    return rows


def ripple_vectors(x, y, size, *, variant='rings', centre=(.5, .5), radius=128,
                   wavelength=32, phase=0, count=6, seed=0):
    """Return a normalized refractive normal field for pixel-centre arrays.

    ``phase`` is in radians and wraps every 2*pi.  The result is intentionally
    dimensionless; the caller owns physical-pixel X/Y displacement strength.
    """
    import numpy as np

    if variant not in RIPPLE_VARIANTS:
        raise ValueError('unsupported ripple variant')
    wavelength = max(float(wavelength), 1.0)
    radius = max(float(radius), 0.5)
    cycle = (float(phase) / (2 * math.pi)) % 1.0
    vx = np.zeros_like(x, dtype=np.float64)
    vy = np.zeros_like(y, dtype=np.float64)
    rows = _centres(size, centre, count, seed, variant)

    for cx, cy, onset, scale in rows:
        dx, dy = x - cx, y - cy
        distance = np.hypot(dx, dy)
        nx = np.divide(dx, distance, out=np.zeros_like(distance), where=distance > 1e-12)
        ny = np.divide(dy, distance, out=np.zeros_like(distance), where=distance > 1e-12)
        local_radius = max(.5, radius * scale)

        if variant == 'droplets':
            # A bounded spherical-cap approximation: its radial normal grows
            # across the lens, then eases to zero at the contact edge.
            q = distance / local_radius
            inside = q < 1
            profile = np.where(inside, q * np.sqrt(np.maximum(0, 1 - q)), 0)
            pulse = .92 + .08 * math.sin(float(phase) + onset * 2 * math.pi)
            magnitude = profile * pulse
        else:
            local_cycle = (cycle - onset) % 1.0
            front = local_cycle * local_radius * (1.15 if variant == 'rings' else 1.8)
            relative = distance - front
            sigma = max(wavelength * (1.35 if variant == 'rings' else 1.8), 1.0)
            envelope = np.exp(-.5 * (relative / sigma) ** 2)
            magnitude = np.sin(2 * math.pi * relative / wavelength) * envelope
            if variant == 'storm':
                # Retain a weak droplet lens behind each travelling packet.
                q = distance / local_radius
                magnitude += np.where(q < 1, .35 * q * np.sqrt(np.maximum(0, 1 - q)), 0)
        vx += nx * magnitude
        vy += ny * magnitude

    if len(rows) > 1:
        normalizer = math.sqrt(len(rows)) if variant == 'storm' else max(1.0, len(rows) * .65)
        vx /= normalizer
        vy /= normalizer
    return np.clip(vx, -1, 1), np.clip(vy, -1, 1)


def wash_coordinate(x, y, size, *, variant='rings', centre=(.5, .5), count=6, seed=0):
    """Return a deterministic 0..1 arrival field for a wash-away reveal."""
    import numpy as np

    if variant not in RIPPLE_VARIANTS:
        raise ValueError('unsupported ripple variant')
    width, height = size
    rows = _centres(size, centre, count, seed, variant)
    if variant == 'rings':
        cx, cy, _, _ = rows[0]
        distance = np.hypot(x - cx, y - cy)
        corners = [(px - cx, py - cy) for px in (-.5, width - .5)
                   for py in (-.5, height - .5)]
        maximum = max(math.hypot(px, py) for px, py in corners) or 1.0
        return np.clip(distance / maximum, 0, 1)

    diagonal = max(math.hypot(width, height), 1.0)
    arrival = np.ones_like(x, dtype=np.float64)
    distance_weight = .72 if variant == 'droplets' else .58
    for cx, cy, onset, scale in rows:
        candidate = onset + distance_weight * np.hypot(x - cx, y - cy) / (diagonal * scale)
        arrival = np.minimum(arrival, candidate)
    # Normalize the bounded seeded field so progress=1 always reveals the target.
    return np.clip(arrival / (distance_weight + (.42 if variant == 'droplets' else .28)), 0, 1)
