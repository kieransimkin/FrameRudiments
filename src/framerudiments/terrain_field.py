"""Frame-addressed spectral landscapes and deterministic geomorphology.

Fourier measurements come exclusively from checksum-pinned StemLab recordings
already decoded into RudimentFrame.spectrum_bands/history. This module does not
perform FFT, infer frequency resolution, or alter the master audio timeline.
"""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TerrainField:
    heights: tuple[tuple[float, ...], ...]
    source: str
    source_sha256: str | None
    source_frames: tuple[int, ...]


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _hash(ix, iy, seed):
    n = (ix * 374761393 + iy * 668265263 + seed * 1442695041) & 0xffffffff
    n = ((n ^ (n >> 13)) * 1274126177) & 0xffffffff
    return ((n ^ (n >> 16)) & 0xffffffff) / 0xffffffff


def _noise(x, y, seed):
    i, j = math.floor(x), math.floor(y)
    u, v = x - i, y - j
    u, v = u * u * (3 - 2 * u), v * v * (3 - 2 * v)
    a = _hash(i, j, seed) * (1 - u) + _hash(i + 1, j, seed) * u
    b = _hash(i, j + 1, seed) * (1 - u) + _hash(i + 1, j + 1, seed) * u
    return 2 * (a * (1 - v) + b * v) - 1


def fbm(x, y, *, seed=7, octaves=5):
    value, amplitude, total = 0.0, 0.5, 0.0
    for i in range(octaves):
        value += amplitude * _noise(x, y, seed + i * 31)
        total += amplitude
        amplitude *= 0.5
        x *= 2.02
        y *= 2.02
    return value / total


def _spectrum_profile(frame, values, cols, floor_db, gain):
    bands = frame.spectrum_bands
    if not bands or len(bands) != len(values) or len(bands) < 3:
        raise ValueError("FFT terrain requires at least 3 calibrated StemLab Fourier bands")
    if any(b.source_sha256 != bands[0].source_sha256 or b.sample_rate != bands[0].sample_rate
           or b.fft_size != bands[0].fft_size for b in bands):
        raise ValueError("FFT terrain bands require a single source, sample rate and FFT size")
    hz = [math.sqrt(max(1, b.low_hz) * b.high_hz) for b in bands]
    if any(a >= b for a, b in zip(hz, hz[1:])):
        raise ValueError("FFT terrain bands must have ascending frequency centres")
    rms = [max(0.0, band.rms(value)) for band, value in zip(bands, values)]
    energies = [_clamp((max(floor_db, 20 * math.log10(value) if value > 0 else floor_db)
                        - floor_db) / (0 - floor_db)) for value in rms]
    lo, hi = math.log(hz[0]), math.log(hz[-1])
    result = []
    k = 0
    for col in range(cols):
        frequency = lo + (hi - lo) * col / max(1, cols - 1)
        while k + 1 < len(bands) - 1 and math.log(hz[k+1]) < frequency:
            k += 1
        x0, x1 = math.log(hz[k]), math.log(hz[k+1])
        a = _clamp((frequency - x0) / (x1 - x0))
        result.append(_clamp(((1 - a) * energies[k] + a * energies[k+1]) * gain))
    return result


def _thermal(grid, amount, iterations):
    """Conservative talus transport with simultaneous updates."""
    if not amount or not iterations:
        return grid
    h, w = len(grid), len(grid[0])
    for _ in range(iterations):
        delta = [[0.0] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                neighbours = [(yy, xx) for yy, xx in ((y+1, x), (y-1, x), (y, x+1), (y, x-1))
                              if 0 <= yy < h and 0 <= xx < w]
                candidates = [(yy, xx, grid[y][x] - grid[yy][xx] - 0.035)
                              for yy, xx in neighbours if grid[y][x] - grid[yy][xx] > 0.035]
                if not candidates:
                    continue
                transfer = min(sum(t for _, _, t in candidates) * amount * .18,
                               max(0, grid[y][x]) * .12)
                total = sum(t for _, _, t in candidates)
                delta[y][x] -= transfer
                for yy, xx, diff in candidates:
                    delta[yy][xx] += transfer * diff / total
        grid = [[max(0, grid[y][x] + delta[y][x]) for x in range(w)] for y in range(h)]
    return grid


def _hydraulic(grid, amount, iterations):
    """Bounded hydraulic-erosion approximation: rain, downhill sediment flux, evaporation."""
    if not amount or not iterations:
        return grid
    h, w = len(grid), len(grid[0])
    water = [[0.0] * w for _ in range(h)]
    sediment = [[0.0] * w for _ in range(h)]
    for _ in range(iterations):
        dw = [[0.0] * w for _ in range(h)]
        ds = [[0.0] * w for _ in range(h)]
        for y in range(h):
            for x in range(w):
                water[y][x] += .06 * amount
                surface = grid[y][x] + water[y][x]
                neighbours = [(yy, xx, surface - (grid[yy][xx] + water[yy][xx]))
                              for yy, xx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1))
                              if 0 <= yy < h and 0 <= xx < w]
                neighbours = [(yy, xx, d) for yy, xx, d in neighbours if d > 0]
                if not neighbours:
                    continue
                flow = min(water[y][x] * .45, sum(d for _, _, d in neighbours) * .1)
                transported = min(sediment[y][x], flow * .5)
                total = sum(d for _, _, d in neighbours)
                dw[y][x] -= flow
                ds[y][x] -= transported
                for yy, xx, difference in neighbours:
                    share = difference / total
                    dw[yy][xx] += flow * share
                    ds[yy][xx] += transported * share
                capacity = flow * (0.4 + amount)
                eroded = min(max(0, grid[y][x]) * .015, max(0, capacity - sediment[y][x]) * amount * .06)
                grid[y][x] -= eroded
                sediment[y][x] += eroded
        for y in range(h):
            for x in range(w):
                water[y][x] = max(0, (water[y][x] + dw[y][x]) * .68)
                sediment[y][x] = max(0, sediment[y][x] + ds[y][x])
                deposited = sediment[y][x] * .18
                sediment[y][x] -= deposited
                grid[y][x] += deposited
    return grid


def _glacial(grid, amount, iterations):
    """Deterministic U-valley incision, ice smoothing and moraine deposits."""
    if not amount or not iterations:
        return grid
    h, w = len(grid), len(grid[0])
    for _ in range(iterations):
        next_grid = [row[:] for row in grid]
        for y in range(h):
            drift = .13 * math.sin(y / max(1, h - 1) * math.pi * 1.2)
            for x in range(1, w-1):
                coord = x / max(1, w - 1) - .5 - drift
                valley = max(0, 1 - (abs(coord) / .23) ** 4)
                shoulder = math.exp(-((abs(coord) - .3) / .075) ** 2)
                filtered = .25 * grid[y][x-1] + .5 * grid[y][x] + .25 * grid[y][x+1]
                next_grid[y][x] = max(0, grid[y][x] - amount * valley * .014
                                      + amount * shoulder * .002 + amount * valley * .08
                                      * (filtered - grid[y][x]))
        grid = next_grid
    return grid


def terrain_field(frame, rows, cols, *, source="procedural", floor_db=-72.0, gain=1.0,
                  seed=7, weathering=0.0, thermal_erosion=0.0, hydraulic_erosion=0.0,
                  glaciation=0.0, erosion_iterations=3, amplitude=1.0):
    """Generate immutable mesh heights from saved Fourier history or procedural terrain.

    Rows are chronological from oldest (far side) to newest (near side).
    ``fft`` uses only calibrated FFT-band RMS values from StemLab; no DSP is
    repeated and no synthetic interpolated samples are represented as measured.
    """
    if not 3 <= rows <= 97 or not 3 <= cols <= 193:
        raise ValueError("Terrain grid resolution outside safe bounds")
    if source not in {"procedural", "fft"}:
        raise ValueError("terrain source must be procedural or fft")
    for key, value in (("gain", gain), ("weathering", weathering), ("thermal erosion", thermal_erosion),
                       ("hydraulic erosion", hydraulic_erosion), ("glaciation", glaciation),
                       ("amplitude", amplitude)):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid terrain {key}")
    if not isinstance(erosion_iterations, int) or not 0 <= erosion_iterations <= 24:
        raise ValueError("Terrain erosion iterations must be between 0 and 24")
    if not -120 <= floor_db <= -6:
        raise ValueError("FFT terrain floor_db must be -120 to -6")
    t = float(getattr(frame.context, "source_seconds", getattr(frame.context, "output_seconds", 0)))
    if source == "fft":
        bands = tuple(getattr(frame, "spectrum_bands", ()))
        samples = list(getattr(frame, "history", ()))
        if not samples or samples[-1].context.frame != frame.context.frame:
            samples.append(frame)
        samples = samples[-rows:]
        frames = [int(sample.context.frame) for sample in samples]
        energy = [_spectrum_profile(frame, sample.values, cols, floor_db, gain) for sample in samples]
        while len(energy) < rows:
            energy.insert(0, list(energy[0]))
            frames.insert(0, frames[0])
        grid = [[level * amplitude for level in row] for row in energy]
        digest = bands[0].source_sha256 if bands else None
    else:
        frames = []
        digest = None
        grid = []
        for y in range(rows):
            v = y / (rows - 1)
            line = []
            for x in range(cols):
                u = x / (cols - 1)
                ridges = abs(fbm(u*4.2, v*4.2, seed=seed))
                base = (.38 * math.sin(u * 8.5 + t * .12) * math.cos(v * 7.0 - t * .06)
                        + .35 * (1-ridges) + .1 * fbm(u*16, v*16, seed=seed+40))
                line.append(max(0, base * amplitude + .25))
            grid.append(line)
    if weathering:
        for y in range(rows):
            for x in range(cols):
                u, v = x/(cols-1), y/(rows-1)
                edge = abs(grid[y][min(cols-1, x+1)] - grid[y][max(0,x-1)])
                grid[y][x] = max(0, grid[y][x] + weathering * .035
                                 * fbm(u*34, v*34, seed=seed+7) * (1 + min(1, edge*4)))
    grid = _thermal(grid, min(1.0, thermal_erosion), erosion_iterations)
    grid = _hydraulic(grid, min(1.0, hydraulic_erosion), erosion_iterations)
    grid = _glacial(grid, min(1.0, glaciation), erosion_iterations)
    return TerrainField(tuple(tuple(v for v in line) for line in grid), source, digest, tuple(frames))
