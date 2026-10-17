"""History-addressable spectrum displays and motion traces.

No FFT or audio decoding happens here. Spectral views require the existing
validated StemLab Fourier-band contract. Waveform views plot rudiment values,
not PCM. All geometry is a pure function of (RudimentFrame, style).
"""

import math

from PIL import ImageDraw

from .io import integer, number
from .rudiment_renderers import _Viewport
from .spectrum import analyse_spectrum, peak_indices
from .visualisation_catalogue import SPECTRAL_VIEWS, MOTION_VIEWS


def _clamp(value):
    return max(0.0, min(1.0, value))


def _gradient(colour, intensity):
    """Opaque heatmap colour; exact deterministic interpolation, no state."""
    v = _clamp(intensity)
    base = (10, 17, 34)
    return tuple(round(lo + (hi - lo) * v) for lo, hi in zip(base, colour[:3])) + (255,)


def _frequency(value, scale):
    """Coordinate transforms only; never claim to produce a new transform."""
    if scale in {"log", "third-octave"}:
        return math.log2(max(1.0, value))
    if scale == "mel":
        return 2595 * math.log10(1 + value / 700)
    if scale == "bark":
        return 13 * math.atan(0.00076 * value) + 3.5 * math.atan((value / 7500) ** 2)
    if scale == "erb":
        return 21.4 * math.log10(1 + 0.00437 * value)
    return value


def _reduce(sequence, limit):
    """Take chronological samples, including the newest, under a raster budget."""
    if len(sequence) <= limit:
        return sequence
    return [sequence[round(i * (len(sequence) - 1) / (limit - 1))] for i in range(limit)]


class AdvancedSpectrumRenderer(_Viewport):
    requires_spectrum = True

    def __init__(self, size, style, mode):
        super().__init__(size, style, {"floor_db", "ceiling_db", "line_width", "contrast", "history_limit"})
        self.mode = mode
        self.floor = number(style.get("floor_db", -72), "spectrum floor", minimum=-240, maximum=0)
        self.ceiling = number(
            style.get("ceiling_db", 0), "spectrum ceiling", minimum=self.floor + 0.01, maximum=60
        )
        self.line_width = integer(style.get("line_width", 2), "spectrum stroke", minimum=1, maximum=24)
        self.contrast = number(style.get("contrast", 1.35), "spectrum contrast", minimum=0.1, maximum=4)
        self.history_limit = integer(
            style.get("history_limit", 96), "spectrum history limit", minimum=8, maximum=256
        )
        self.area(max(2, self.line_width))
        self.frame_report = None

    def _level(self, band, native_value):
        rms = band.rms(native_value)
        db = 20 * math.log10(rms) if rms > 0 else self.floor
        return _clamp((db - self.floor) / (self.ceiling - self.floor))

    def _levels(self, frame, sample):
        return [self._level(band, value) for band, value in zip(frame.spectrum_bands, sample.values)]

    def _scale(self):
        suffix = self.mode.rsplit("-", 1)[-1]
        return suffix if suffix in {"log", "mel", "bark", "erb", "third-octave"} else "linear"

    def _positions(self, frame, lo, hi, scale=None):
        scale = scale or self._scale()
        bands = frame.spectrum_bands
        start = _frequency(bands[0].low_hz, scale)
        stop = _frequency(bands[-1].high_hz, scale)
        width = max(1e-12, stop - start)
        return [
            lo + (hi - lo) * (_frequency((b.low_hz + b.high_hz) / 2, scale) - start) / width for b in bands
        ]

    def _spectrum(self, image, frame, report):
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = self.area(max(2, self.line_width))
        mid = (top + bottom) / 2
        mode = self.mode
        levels = [self._level(b, v) for b, v in zip(frame.spectrum_bands, frame.values)]
        points = [
            (x, bottom - v * (bottom - top)) for x, v in zip(self._positions(frame, left, right), levels)
        ]
        ink = self.colours[0]
        if mode in {"spectrum-line", "spectrum-log", "spectrum-mel", "spectrum-third-octave"}:
            if len(points) >= 2:
                draw.line(points, fill=ink, width=self.line_width, joint="curve")
            else:
                draw.point(points[0], fill=ink)
        elif mode == "spectrum-area":
            draw.polygon([(points[0][0], bottom), *points, (points[-1][0], bottom)], fill=(*ink[:3], 135))
            if len(points) > 1:
                draw.line(points, fill=ink, width=self.line_width)
        elif mode == "spectrum-dots":
            for index, (x, y) in enumerate(points):
                r = max(2, self.line_width * 1.7)
                draw.ellipse((x - r, y - r, x + r, y + r), fill=self.colours[index % len(self.colours)])
        elif mode == "spectrum-stems":
            for index, (x, y) in enumerate(points):
                colour = self.colours[index % len(self.colours)]
                draw.line((x, bottom, x, y), fill=colour, width=self.line_width)
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=colour)
        elif mode == "spectrum-steps":
            for index, (x, y) in enumerate(points):
                leftx = left if index == 0 else (points[index - 1][0] + x) / 2
                rightx = right if index == len(points) - 1 else (points[index + 1][0] + x) / 2
                draw.line((leftx, y, rightx, y), fill=ink, width=self.line_width)
                if index:
                    draw.line((leftx, points[index - 1][1], leftx, y), fill=ink, width=self.line_width)
        elif mode == "spectrum-mirror":
            for index, (x, _) in enumerate(points):
                length = levels[index] * (bottom - top) / 2
                draw.line(
                    (x, mid - length, x, mid + length),
                    fill=self.colours[index % len(self.colours)],
                    width=max(1, self.line_width + 1),
                )
        elif mode in {"spectrum-radial", "spectrum-ring", "spectrum-spiral"}:
            self._circular(draw, levels, mode)
        elif mode == "spectrum-peaks":
            chosen = peak_indices(levels, floor=0, threshold=0.12, contrast=0.04, distance=1, count=32)
            for index, _, _, _ in chosen:
                x, y = points[index]
                draw.line((x, bottom, x, y), fill=self.colours[index % len(self.colours)], width=3)
                draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#ffffff")
        else:
            self._history_spectra(draw, frame, mode, (left, top, right, bottom))
        return image

    def _circular(self, draw, levels, mode):
        left, top, right, bottom = self.area(max(2, self.line_width))
        cx, cy = (left + right) / 2, (top + bottom) / 2
        radius = min(right - left, bottom - top) / 2
        n = len(levels)
        if mode == "spectrum-spiral":
            points = []
            for j, value in enumerate(levels):
                angle = 5 * math.pi * j / max(1, n - 1) - math.pi / 2
                r = radius * (0.12 + 0.58 * j / max(1, n - 1) + 0.22 * value)
                points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
            if len(points) > 1:
                draw.line(points, fill=self.colours[0], width=self.line_width)
            for j, p in enumerate(points):
                draw.ellipse(
                    (p[0] - 2, p[1] - 2, p[0] + 2, p[1] + 2), fill=self.colours[j % len(self.colours)]
                )
            return
        coords = []
        for j, value in enumerate(levels):
            angle = -math.pi / 2 + 2 * math.pi * j / n
            a, b = radius * 0.24, radius * (0.24 + 0.72 * value)
            p = (cx + a * math.cos(angle), cy + a * math.sin(angle))
            q = (cx + b * math.cos(angle), cy + b * math.sin(angle))
            coords.append(q)
            if mode == "spectrum-radial":
                draw.line((*p, *q), fill=self.colours[j % len(self.colours)], width=self.line_width)
        if mode == "spectrum-ring" and len(coords) > 1:
            draw.polygon(coords, fill=(*self.colours[0][:3], 85))
            draw.line([*coords, coords[0]], fill=self.colours[0], width=self.line_width)

    def _history_spectra(self, draw, frame, mode, area):
        left, top, right, bottom = area
        # Perspective wireframes become unreadable when hundreds of ridges
        # overlap; keep bounded history but limit visible strokes.
        rows = _reduce(frame.history, min(self.history_limit, 40))
        if not rows:
            return
        for i, sample in enumerate(rows):
            levels = self._levels(frame, sample)
            t = i / max(1, len(rows) - 1)
            xcoords = self._positions(frame, left + 13 * t, right - 13 * (1 - t))
            if mode == "spectrum-horizon":
                ybase = bottom - t * (bottom - top) * 0.46
                height = (bottom - top) * 0.51
            elif mode == "spectrum-ridgeline":
                ybase = bottom - t * (bottom - top) * 0.79
                height = (bottom - top) * 0.3
            else:  # perspective waterfall
                ybase = bottom - t * (bottom - top) * 0.63
                height = (bottom - top) * (0.34 + t * 0.16)
            points = [(x, max(top, ybase - v * height)) for x, v in zip(xcoords, levels)]
            if len(points) > 1:
                colour = self.colours[i % len(self.colours)]
                draw.line(points, fill=(*colour[:3], 255), width=self.line_width)

    def _spectrogram(self, image, frame):
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = self.area(3)
        mode = self.mode
        samples = _reduce(frame.history, self.history_limit)
        if not samples:
            return image
        span = max(1, (frame.history_frames - 1) * frame.history_stride)
        if mode == "spectrogram-polar":
            self._polar_sonogram(draw, frame, samples)
            return image
        scale = self._scale()
        f_min = _frequency(frame.spectrum_bands[0].low_hz, scale)
        f_max = _frequency(frame.spectrum_bands[-1].high_hz, scale)
        extent = max(1e-12, f_max - f_min)
        ranges = [
            (
                top + (1 - (_frequency(b.high_hz, scale) - f_min) / extent) * (bottom - top),
                top + (1 - (_frequency(b.low_hz, scale) - f_min) / extent) * (bottom - top),
            )
            for b in frame.spectrum_bands
        ]
        centres = [
            right - (frame.context.frame - item.context.frame) / span * (right - left) for item in samples
        ]
        # A history downsample changes inter-sample spacing. Tessellate using
        # midpoint boundaries so heatmaps have no synthetic black gaps.
        column_bounds = []
        for i, x in enumerate(centres):
            x0 = left if i == 0 else (centres[i - 1] + x) / 2
            x1 = right if i + 1 == len(centres) else (x + centres[i + 1]) / 2
            column_bounds.append((max(left, x0), min(right, x1)))
        for sample, (x0, x1) in zip(samples, column_bounds):
            levels = self._levels(frame, sample)
            selected = set()
            if mode == "spectrogram-peaks":
                selected = {
                    j
                    for j, _, _, _ in peak_indices(
                        levels, floor=0, threshold=0.12, contrast=0.05, distance=1, count=len(levels)
                    )
                }
            for j, (lo, hi) in enumerate(ranges):
                if mode == "spectrogram-peaks" and j not in selected:
                    continue
                value = levels[j]
                if mode == "spectrogram-contours":
                    value = math.floor(value * 7) / 7
                value = value ** (1 / self.contrast)
                colour = _gradient(self.colours[j % len(self.colours)], value)
                if mode == "spectrogram-transposed":
                    t0 = top + (right - x1) / max(1, right - left) * (bottom - top)
                    t1 = top + (right - x0) / max(1, right - left) * (bottom - top)
                    f0 = left + (lo - top) / max(1, bottom - top) * (right - left)
                    f1 = left + (hi - top) / max(1, bottom - top) * (right - left)
                    draw.rectangle((min(f0, f1), t0, max(f0, f1) + 1, t1), fill=colour)
                elif mode == "spectrogram-mirror":
                    mid = (top + bottom) / 2
                    a, b = top + (lo - top) / 2, top + (hi - top) / 2
                    draw.rectangle((x0, a, x1, b), fill=colour)
                    draw.rectangle((x0, 2 * mid - b, x1, 2 * mid - a), fill=colour)
                else:
                    draw.rectangle((x0, lo, x1, max(hi, lo + 1)), fill=colour)
        return image

    def _polar_sonogram(self, draw, frame, samples):
        left, top, right, bottom = self.area(3)
        cx, cy = (left + right) / 2, (top + bottom) / 2
        radius = min(right - left, bottom - top) / 2
        bands = frame.spectrum_bands
        lo = bands[0].low_hz
        hi = bands[-1].high_hz
        samples = _reduce(samples, 72)
        indices = list(range(len(bands)))
        if len(indices) > 80:
            indices = [round(x) for x in [i * (len(bands) - 1) / 79 for i in range(80)]]
        span = max(1, (frame.history_frames - 1) * frame.history_stride)
        angles = [
            -math.pi / 2 + 2 * math.pi * (1 - (frame.context.frame - sample.context.frame) / span)
            for sample in samples
        ]
        for i, sample in enumerate(samples):
            angle = angles[i]
            theta0 = (
                (angles[i - 1] + angle) / 2
                if i
                else angle - (angles[1] - angle) / 2
                if len(angles) > 1
                else angle
            )
            theta1 = (
                (angle + angles[i + 1]) / 2
                if i + 1 < len(angles)
                else angle + (angle - angles[i - 1]) / 2
                if i
                else angle
            )
            levels = self._levels(frame, sample)
            for j in indices:
                band = bands[j]
                r0 = radius * (0.12 + 0.86 * (band.low_hz - lo) / max(1, hi - lo))
                r1 = radius * (0.12 + 0.86 * (band.high_hz - lo) / max(1, hi - lo))
                polygon = []
                for r, theta in [(r0, theta0), (r1, theta0), (r1, theta1), (r0, theta1)]:
                    polygon.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
                draw.polygon(
                    polygon,
                    fill=_gradient(self.colours[j % len(self.colours)], levels[j] ** (1 / self.contrast)),
                )

    def render(self, frame):
        if not frame.spectrum_bands or len(frame.spectrum_bands) != len(frame.values):
            raise ValueError("Advanced spectrum views require calibrated, selected Fourier band metadata")
        self.frame_report = analyse_spectrum(frame, floor=self.floor, ceiling=self.ceiling)
        image = self.image()
        if self.mode.startswith("spectrogram-"):
            return self._spectrogram(image, frame)
        return self._spectrum(image, frame, self.frame_report)


class AdvancedWaveformRenderer(_Viewport):
    """Analog-inspired geometry over motion values, not decoded audio."""

    def __init__(self, size, style, mode):
        super().__init__(size, style, {"line_width", "grid"})
        self.mode = mode
        self.line_width = integer(style.get("line_width", 2), "waveform stroke", minimum=1, maximum=24)
        self.grid = style.get("grid", True)
        if not isinstance(self.grid, bool):
            raise ValueError("Waveform grid must be boolean")
        self.area(max(2, self.line_width))

    def _xy(self, frame, samples, axis, bounds):
        left, top, right, bottom = bounds
        span = max(1, (frame.history_frames - 1) * frame.history_stride)
        mid = (top + bottom) / 2
        return [
            (
                right - (frame.context.frame - sample.context.frame) / span * (right - left),
                mid - sample.values[axis] * (bottom - top) / 2,
            )
            for sample in samples
        ]

    def render(self, frame):
        if not frame.history or not frame.values:
            raise ValueError("Waveform views require observed rudiment history")
        image = self.image()
        draw = ImageDraw.Draw(image)
        left, top, right, bottom = self.area(max(2, self.line_width))
        mode = self.mode
        history = frame.history
        if mode.startswith("phase-"):
            if len(frame.values) < 2:
                raise ValueError("Phase-space views need two selected rudiment axes")
            if self.grid:
                draw.line((left, (top + bottom) / 2, right, (top + bottom) / 2), fill="#30465d")
                draw.line(((left + right) / 2, top, (left + right) / 2, bottom), fill="#30465d")
            coords = [
                (left + (1 + s.values[0]) / 2 * (right - left), top + (1 - s.values[1]) / 2 * (bottom - top))
                for s in history
            ]
            if mode == "phase-portrait" and len(coords) > 1:
                draw.line(coords, fill=self.colours[0], width=self.line_width)
            for i, (x, y) in enumerate(coords):
                r = 1 if mode == "phase-portrait" else 2
                colour = self.colours[i % len(self.colours)]
                draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)
            return image
        if mode == "waveform-polar":
            cx, cy = (left + right) / 2, (top + bottom) / 2
            radius = min(right - left, bottom - top) / 2
            points = []
            for sample in history:
                age = frame.context.frame - sample.context.frame
                span = max(1, (frame.history_frames - 1) * frame.history_stride)
                angle = -math.pi / 2 + 2 * math.pi * (1 - age / span)
                r = radius * (0.5 + sample.values[0] * 0.45)
                points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
            if len(points) > 1:
                draw.line(points, fill=self.colours[0], width=self.line_width)
            return image
        if mode == "waveform-ribbon":
            if len(frame.values) < 2:
                raise ValueError("Waveform ribbon needs two selected rudiment axes")
            a = self._xy(frame, history, 0, (left, top, right, bottom))
            b = self._xy(frame, history, 1, (left, top, right, bottom))
            if len(a) > 1:
                draw.polygon([*a, *reversed(b)], fill=(*self.colours[0][:3], 95))
                draw.line(a, fill=self.colours[0], width=self.line_width)
                draw.line(b, fill=self.colours[1 % len(self.colours)], width=self.line_width)
            return image
        if mode == "waveform-mirror":
            mid = (top + bottom) / 2
            upper = [
                (x, mid - abs(s.values[0]) * (bottom - top) / 2)
                for x, s in zip(
                    [p[0] for p in self._xy(frame, history, 0, (left, top, right, bottom))], history
                )
            ]
            lower = [(x, 2 * mid - y) for x, y in upper]
            if len(upper) > 1:
                draw.polygon([*upper, *reversed(lower)], fill=(*self.colours[0][:3], 115))
                draw.line(upper, fill=self.colours[0], width=self.line_width)
                draw.line(lower, fill=self.colours[0], width=self.line_width)
            return image
        lanes = len(frame.values) if mode not in {"waveform-oscilloscope", "waveform-envelope"} else 1
        if (bottom - top) / lanes <= 3:
            raise ValueError("Too many waveform axes for this viewport; select axes")
        for axis in range(lanes):
            band_top = top + axis * (bottom - top) / lanes
            band_bottom = top + (axis + 1) * (bottom - top) / lanes
            ymid = (band_top + band_bottom) / 2
            if self.grid:
                draw.line((left, ymid, right, ymid), fill="#30465d")
            points = self._xy(frame, history, axis, (left, band_top, right, band_bottom))
            colour = self.colours[axis % len(self.colours)]
            if mode == "waveform-area" and len(points) > 1:
                draw.polygon([(points[0][0], ymid), *points, (points[-1][0], ymid)], fill=(*colour[:3], 110))
                draw.line(points, fill=colour, width=self.line_width)
            elif mode == "waveform-dots":
                for x, y in points:
                    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=colour)
            elif mode == "waveform-steps":
                for prev, cur in zip(points, points[1:]):
                    draw.line(
                        (prev[0], prev[1], cur[0], prev[1], cur[0], cur[1]),
                        fill=colour,
                        width=self.line_width,
                    )
            elif mode == "waveform-envelope":
                # One observed value per frame; this is not a PCM peak envelope.
                groups = {}
                for x, y in points:
                    groups.setdefault(round(x), []).append(y)
                for x, ys in groups.items():
                    draw.line((x, min(ys), x, max(ys)), fill=colour, width=max(2, self.line_width))
                if len(points) > 1:
                    draw.line(points, fill=colour, width=1)
            elif len(points) > 1:
                draw.line(points, fill=colour, width=self.line_width)
            else:
                draw.point(points[0], fill=colour)
        return image


def register_extended_renderers(registry):
    """Renderers are explicit, locally registered and source-hashed by registry."""
    for spec in (*SPECTRAL_VIEWS, *MOTION_VIEWS):
        if spec.family == "motion":

            def factory(size, style, mode=spec.name):
                return AdvancedWaveformRenderer(size, style, mode)
        else:

            def factory(size, style, mode=spec.name):
                return AdvancedSpectrumRenderer(size, style, mode)

        registry.register(spec.name, factory)
    return registry
