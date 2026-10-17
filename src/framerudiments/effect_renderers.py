"""Sixty additional views of *observed* Rudiment channels and calibrated bands.

There is no PCM decoder, STFT, inferred phase, new spectral resolution or
free-running animation here. Every pixel depends only on an immutable frame.
"""

import colorsys
import math

from PIL import ImageDraw, ImageOps

from .io import integer, number
from .rudiment_renderers import _Viewport
from .spectrum import analyse_spectrum
from .visualisation_catalogue import EFFECT_VIEWS


def _clip(v, a=0.0, b=1.0):
    return max(a, min(b, v))


def _sampled(items, limit):
    if len(items) <= limit:
        return items
    return [items[round(i * (len(items) - 1) / (limit - 1))] for i in range(limit)]


class EffectRenderer(_Viewport):
    """Creative spectrum, time-frequency, and motion plot families.

    Shared style fields can be independently modulated using style.* routing.
    Unsupported style fields fail validation; no untrusted code is evaluated.
    """

    def __init__(self, size, style, mode):
        super().__init__(
            size,
            style,
            {
                "line_width",
                "floor_db",
                "ceiling_db",
                "gain",
                "history_limit",
                "axis",
                "segments",
                "quantize",
                "gap",
                "fill_alpha",
                "decay",
                "bead_radius",
                "perspective",
                "smoothing",
                "threshold",
                "stride",
                "amplitude_curve",
                "colour_map",
                "mirror",
                "reverse",
                "grid",
                "baseline",
            },
        )
        self.mode = mode
        self.requires_spectrum = mode.startswith(("spectrum-", "spectrogram-"))
        self.line_width = integer(style.get("line_width", 2), "effect line width", minimum=1, maximum=32)
        self.floor = number(style.get("floor_db", -72), "effect spectrum floor", minimum=-240, maximum=0)
        self.ceiling = number(
            style.get("ceiling_db", 0), "effect spectrum ceiling", minimum=self.floor + 0.01, maximum=60
        )
        self.gain = number(style.get("gain", 1), "effect display gain", minimum=0, maximum=8)
        self.history_limit = integer(
            style.get("history_limit", 48), "effect history limit", minimum=4, maximum=256
        )
        self.axis = integer(style.get("axis", 0), "effect axis", minimum=0, maximum=255)
        self.segments = integer(style.get("segments", 14), "effect segments", minimum=2, maximum=64)
        self.quantize = integer(style.get("quantize", 0), "effect quantize", minimum=0, maximum=64)
        self.gap = number(style.get("gap", 0.14), "effect gap fraction", minimum=0, maximum=0.95)
        self.fill_alpha = integer(style.get("fill_alpha", 175), "effect fill alpha", minimum=0, maximum=255)
        self.decay = number(style.get("decay", 0.72), "effect history fade", minimum=0, maximum=1)
        self.bead_radius = number(
            style.get("bead_radius", 3.5), "effect bead radius", minimum=0.5, maximum=20
        )
        self.perspective = number(style.get("perspective", 0.46), "effect depth", minimum=0, maximum=1)
        self.smoothing = integer(style.get("smoothing", 0), "effect plot smoothing", minimum=0, maximum=16)
        self.threshold = number(style.get("threshold", 0.06), "effect plot threshold", minimum=0, maximum=1)
        self.stride = integer(style.get("stride", 1), "effect subsampling", minimum=1, maximum=16)
        self.amplitude_curve = style.get("amplitude_curve", "linear")
        if self.amplitude_curve not in {"linear", "sqrt", "square", "log", "ease"}:
            raise ValueError("Unknown effect amplitude_curve")
        self.colour_map = style.get("colour_map", "axis")
        if self.colour_map not in {"axis", "level", "time", "alternating", "mono", "rainbow"}:
            raise ValueError("Unknown effect colour_map")
        for key in ("mirror", "reverse", "grid", "baseline"):
            value = style.get(key, False)
            if not isinstance(value, bool):
                raise ValueError(f"Effect {key} must be boolean")
            setattr(self, key, value)
        self.area(max(self.line_width / 2, self.bead_radius))
        self.frame_report = None

    def _amplitude(self, v):
        u = _clip(v * self.gain)
        if self.amplitude_curve == "sqrt":
            u = math.sqrt(u)
        elif self.amplitude_curve == "square":
            u *= u
        elif self.amplitude_curve == "log":
            u = math.log1p(9 * u) / math.log(10)
        elif self.amplitude_curve == "ease":
            u = u * u * (3 - 2 * u)
        if self.quantize > 1:
            u = round(u * (self.quantize - 1)) / (self.quantize - 1)
        return _clip(u)

    def _ink(self, i, level=1, *, age=0, alpha=255):
        colour = self.colours[i % len(self.colours)]
        if self.colour_map == "mono":
            colour = self.colours[0]
        elif self.colour_map == "alternating":
            colour = self.colours[(i // 2) % len(self.colours)]
        elif self.colour_map == "time":
            colour = self.colours[(i + age) % len(self.colours)]
        elif self.colour_map == "level":
            colour = self.colours[min(len(self.colours) - 1, int(level * len(self.colours)))]
        elif self.colour_map == "rainbow":
            hue = ((i / 64) + level * 0.25) % 1
            colour = (*tuple(round(255 * v) for v in colorsys.hsv_to_rgb(hue, 0.83, 0.97)), 255)
        a = round(colour[3] * alpha * self.fill_alpha / (255 * 255))
        return (*colour[:3], a)

    def _history(self, frame):
        if not frame.history:
            raise ValueError("Renderer needs observed source-clocked samples")
        return _sampled(frame.history[:: self.stride], self.history_limit)

    def _band_levels(self, frame, samples):
        if not frame.spectrum_bands or len(frame.spectrum_bands) != len(frame.values):
            raise ValueError("Effect spectrum requires calibrated StemLab Fourier bands")
        out = []
        for sample in samples:
            if len(sample.values) != len(frame.spectrum_bands):
                raise ValueError("Mismatched calibrated band sample dimensions")
            row = []
            for band, value in zip(frame.spectrum_bands, sample.values):
                amp = band.rms(value)
                db = 20 * math.log10(amp) if amp > 0 else self.floor
                row.append(self._amplitude((db - self.floor) / (self.ceiling - self.floor)))
            out.append(row)
        return out

    def _smooth(self, row):
        if not self.smoothing:
            return row
        k = self.smoothing
        return [
            sum(row[max(0, i - k) : min(len(row), i + k + 1)])
            / len(row[max(0, i - k) : min(len(row), i + k + 1)])
            for i in range(len(row))
        ]

    def _grid(self, draw, area, *, count=4):
        if not self.grid:
            return
        left, t, r, b = area
        for j in range(count + 1):
            x = left + (r - left) * j / count
            y = t + (b - t) * j / count
            draw.line((x, t, x, b), fill=(64, 84, 106, 100))
            draw.line((left, y, r, y), fill=(64, 84, 106, 100))

    def _spectrum(self, draw, frame, area):
        # Analysis is strictly the existing shared calibrated StemLab-band path.
        self.frame_report = analyse_spectrum(frame, floor=self.floor, ceiling=self.ceiling)
        rows = self._band_levels(frame, self._history(frame))
        levels = self._smooth(rows[-1])
        if self.reverse:
            levels = list(reversed(levels))
            rows = [list(reversed(row)) for row in rows]
        n = len(levels)
        if not n:
            raise ValueError("No calibrated bands to display")
        mode = self.mode.removeprefix("spectrum-")
        left, t, r, b = area
        w, h = r - left, b - t
        cell = w / n
        mid = (left + r) / 2
        points = [(left + (i + 0.5) * cell, b - v * h) for i, v in enumerate(levels)]
        self._grid(draw, area)
        if mode in {"cityscape", "capsules", "outline-blocks", "gel-bars", "thermometer", "partition"}:
            for i, v in enumerate(levels):
                x0, x1 = left + i * cell + self.gap * cell / 2, left + (i + 1) * cell - self.gap * cell / 2
                y = b - h * v
                ink = self._ink(i, v)
                if mode == "cityscape":
                    roof = (i % 5) * h * 0.012
                    draw.polygon(
                        [(x0, b), (x0, y + roof), ((x0 + x1) / 2, y - roof), (x1, y + roof), (x1, b)],
                        fill=ink,
                    )
                    for wy in range(round(y + 10), round(b), 12):
                        draw.line((x0 + 1, wy, x1 - 1, wy), fill=self._ink(i + 1, v, alpha=90))
                elif mode == "capsules":
                    draw.rounded_rectangle((x0, y, x1, b), radius=max(1, min(cell / 2, h * v / 2)), fill=ink)
                elif mode == "outline-blocks":
                    draw.rectangle((x0, y, x1, b), outline=ink, width=self.line_width)
                elif mode == "gel-bars":
                    draw.rectangle((x0, y, x1, b), fill=self._ink(i, v, alpha=145))
                    draw.line((x0 + 1, y + 2, x0 + 1, b - 2), fill=self._ink(i + 1, v, alpha=230), width=1)
                elif mode == "thermometer":
                    draw.rectangle((x0, t, x1, b), fill=self._ink(i, v, alpha=round(255 * v)))
                else:  # partition
                    xmid = x0 + (x1 - x0) * v
                    draw.rectangle((x0, t, xmid, b), fill=ink)
                    draw.rectangle((xmid, t, x1, b), fill=self._ink(i + 1, v, alpha=35))
        elif mode in {"led-matrix", "bead-stacks", "dash-grid"}:
            for i, v in enumerate(levels):
                x = left + (i + 0.5) * cell
                for j in range(self.segments):
                    threshold = (j + 0.5) / self.segments
                    if threshold > v:
                        if mode == "led-matrix":
                            ink = self._ink(i, v, alpha=30)
                        else:
                            continue
                    else:
                        ink = self._ink(i + j, v)
                    y0 = b - h * (j + 1) / self.segments
                    y1 = b - h * j / self.segments
                    if mode == "bead-stacks":
                        radius = min(self.bead_radius, cell * 0.43, (y1 - y0) * 0.43)
                        yc = (y0 + y1) / 2
                        draw.ellipse((x - radius, yc - radius, x + radius, yc + radius), fill=ink)
                    else:
                        gap = 0.15 if mode == "dash-grid" else 0.05
                        draw.rectangle(
                            (x - cell * 0.43, y0 + (y1 - y0) * gap, x + cell * 0.43, y1 - (y1 - y0) * gap),
                            fill=ink,
                        )
        elif mode in {"peak-ladder", "staircase", "lollipop-grid", "eq-curve"}:
            if mode == "eq-curve":
                if len(points) > 1:
                    draw.line(points, fill=self._ink(0), width=self.line_width + 1, joint="curve")
            for i, ((x, y), v) in enumerate(zip(points, levels)):
                ink = self._ink(i, v)
                if mode == "peak-ladder":
                    draw.line((x - cell * 0.45, y, x + cell * 0.45, y), fill=ink, width=self.line_width + 1)
                elif mode == "staircase":
                    draw.line((x - cell * 0.5, y, x + cell * 0.5, y), fill=ink, width=self.line_width)
                    if i > 0:
                        draw.line(
                            (x - cell * 0.5, points[i - 1][1], x - cell * 0.5, y),
                            fill=ink,
                            width=self.line_width,
                        )
                elif mode == "lollipop-grid":
                    draw.line((x, b, x, y), fill=ink, width=self.line_width)
                    radius = min(self.bead_radius, cell * 0.48)
                    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=self._ink(i + 1, v))
        elif mode in {
            "ribbon-stack",
            "mountain-range",
            "braided-lines",
            "wave-fence",
            "rain-bars",
            "peak-trails",
        }:
            history = _sampled(rows, min(24, self.history_limit))
            for j, row in enumerate(history):
                f = (j + 1) / len(history)
                age_alpha = round(255 * ((1 - self.decay) + self.decay * f))
                pts = [
                    (
                        left + (i + 0.5) * cell,
                        b
                        - h * (0.8 if mode == "wave-fence" else 1) * v
                        - (1 - f) * h * self.perspective * 0.5,
                    )
                    for i, v in enumerate(row)
                ]
                if mode == "mountain-range":
                    draw.polygon(
                        [(left, b), *pts, (r, b)], fill=self._ink(j, 0.5, alpha=round(age_alpha * 0.35))
                    )
                elif mode == "ribbon-stack":
                    draw.line(pts, fill=self._ink(j, 1, alpha=age_alpha), width=max(1, self.line_width))
                elif mode == "braided-lines":
                    for i, (x, y) in enumerate(pts):
                        if i > 0:
                            draw.line(
                                (pts[i - 1], (x, y)),
                                fill=self._ink(i + j, row[i], alpha=age_alpha),
                                width=self.line_width,
                            )
                elif mode == "wave-fence":
                    draw.line(pts, fill=self._ink(j, 1, alpha=age_alpha), width=self.line_width)
                    for i, (x, y) in enumerate(pts[:: max(1, n // 18)]):
                        draw.line((x, y, x, b), fill=self._ink(i, 0.5, alpha=round(age_alpha * 0.5)))
                elif mode == "rain-bars":
                    for i, (x, y) in enumerate(pts):
                        if row[i] >= self.threshold:
                            draw.line(
                                (x - cell * 0.33, y, x + cell * 0.33, y),
                                fill=self._ink(i, row[i], alpha=age_alpha),
                            )
                else:  # peak-trails
                    for i, (x, y) in enumerate(pts):
                        if row[i] >= self.threshold:
                            rad = min(self.bead_radius, cell * 0.48)
                            draw.ellipse(
                                (x - rad, y - rad, x + rad, y + rad),
                                fill=self._ink(i, row[i], alpha=age_alpha),
                            )
        elif mode in {"band-delta", "band-variance", "energy-tiles"}:
            previous = rows[-2] if len(rows) > 1 else rows[-1]
            for i, v in enumerate(levels):
                x0, x1 = left + i * cell + self.gap * cell / 2, left + (i + 1) * cell - self.gap * cell / 2
                if mode == "band-delta":
                    delta = v - previous[i]
                    y = (t + b) / 2
                    draw.rectangle(
                        (x0, min(y, y - delta * h * 0.48), x1, max(y, y - delta * h * 0.48)),
                        fill=self._ink(i, abs(delta)),
                    )
                elif mode == "band-variance":
                    mean = sum(row[i] for row in rows) / len(rows)
                    var = sum((row[i] - mean) ** 2 for row in rows) / len(rows)
                    draw.rectangle((x0, b - h * _clip(math.sqrt(var) * 2), x1, b), fill=self._ink(i, var))
                else:
                    draw.rectangle((x0, t, x1, b), fill=self._ink(i, v, alpha=round(255 * v)))
        elif mode in {"semi-circle", "fan-meter"}:
            cx, cy = mid, b
            radius = min(w * 0.48, h * 0.93)
            for i, v in enumerate(levels):
                a = math.pi * (1 - i / max(1, n - 1))
                x0, y0 = cx + radius * 0.33 * math.cos(a), cy - radius * 0.33 * math.sin(a)
                x1, y1 = (
                    cx + radius * (0.33 + 0.67 * v) * math.cos(a),
                    cy - radius * (0.33 + 0.67 * v) * math.sin(a),
                )
                ink = self._ink(i, v)
                if mode == "semi-circle":
                    draw.line((x0, y0, x1, y1), fill=ink, width=max(1, round(cell * 0.45)))
                else:
                    a1 = a - math.pi / max(1, n) * 0.36
                    a2 = a + math.pi / max(1, n) * 0.36
                    pts = [
                        (cx + radius * rr * math.cos(aa), cy - radius * rr * math.sin(aa))
                        for aa, rr in ((a1, 0.3), (a1, 0.33 + 0.67 * v), (a2, 0.33 + 0.67 * v), (a2, 0.3))
                    ]
                    draw.polygon(pts, fill=ink)
        else:
            raise ValueError(f"Unimplemented spectrum effect: {mode}")

    def _spectrogram(self, draw, frame, area):
        self.frame_report = analyse_spectrum(frame, floor=self.floor, ceiling=self.ceiling)
        rows = self._band_levels(frame, self._history(frame))
        if not rows:
            return
        mode = self.mode.removeprefix("spectrogram-")
        left, t, r, b = area
        w, h = r - left, b - t
        nt, nf = len(rows), len(rows[-1])
        cellw = w / nt
        cellh = h / nf
        if self.reverse:
            rows = list(reversed(rows))
        for time, row in enumerate(rows):
            x0, x1 = left + time * cellw, left + (time + 1) * cellw
            for band, v in enumerate(row):
                y0, y1 = b - (band + 1) * cellh, b - band * cellh
                if v < self.threshold and mode not in {"negative", "quilt", "isobands"}:
                    continue
                ink = self._ink(band, v, age=time, alpha=round(45 + 210 * v))
                xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
                if mode == "pixel-mosaic":
                    draw.rectangle((x0, y0, x1, y1), fill=ink)
                elif mode == "isometric-terrain":
                    if band % 3 == 0:
                        y = y1 - v * cellh * 2
                        draw.line((x0, y, x1 + cellw * 0.4, y - cellh * 0.4), fill=ink, width=self.line_width)
                elif mode == "voxel-city":
                    if band % 2 == 0:
                        lift = cellh * max(1, v * 3)
                        draw.polygon(
                            [
                                (x0, y1),
                                (x0, y1 - lift),
                                (x1, y1 - lift - cellh * 0.35),
                                (x1, y1 - cellh * 0.35),
                            ],
                            fill=ink,
                        )
                elif mode == "frequency-stripes":
                    if band % 2:
                        ink = self._ink(band + 1, v, age=time, alpha=round(90 + 165 * v))
                    draw.rectangle((x0, y0, x1, y1), fill=ink)
                elif mode in {"dot-cloud", "bead-grid"}:
                    radius = (
                        min(cellw, cellh) * (0.12 + 0.38 * v)
                        if mode == "dot-cloud"
                        else min(cellw, cellh) * 0.43
                    )
                    draw.ellipse((xc - radius, yc - radius, xc + radius, yc + radius), fill=ink)
                elif mode == "isobands":
                    grade = round(v * self.segments) / self.segments
                    draw.rectangle(
                        (x0, y0, x1, y1), fill=self._ink(band, grade, age=time, alpha=round(32 + 223 * grade))
                    )
                elif mode == "negative":
                    draw.rectangle(
                        (x0, y0, x1, y1), fill=self._ink(band, 1 - v, age=time, alpha=round(255 * (1 - v)))
                    )
                elif mode == "tape":
                    draw.line((x0, yc, x1, yc), fill=ink, width=max(1, round(cellh * 0.76)))
                elif mode == "slit-scan":
                    draw.line((xc, y0, xc, y1), fill=ink, width=max(1, round(cellw * 0.75)))
                elif mode == "trails":
                    draw.line((x0, y1, x1, y0 - v * cellh), fill=ink, width=self.line_width)
                elif mode == "hex-cells":
                    radius = min(cellw, cellh) * 0.47
                    draw.polygon(
                        [
                            (xc + radius * math.cos(math.pi * j / 3), yc + radius * math.sin(math.pi * j / 3))
                            for j in range(6)
                        ],
                        fill=ink,
                    )
                elif mode == "tunnel":
                    if band % 3 == 0 and time % 2 == 0:
                        shrink = (1 - time / max(1, nt)) * w * 0.36
                        draw.rectangle(
                            (
                                left + shrink,
                                t + shrink * h / max(1, w),
                                r - shrink,
                                b - shrink * h / max(1, w),
                            ),
                            outline=ink,
                            width=self.line_width,
                        )
                elif mode == "record-grooves":
                    if band % 2 == 0:
                        cx, cy = (left + r) / 2, (t + b) / 2
                        radius = min(w, h) * (0.1 + 0.4 * time / max(1, nt - 1))
                        draw.arc(
                            (cx - radius, cy - radius, cx + radius, cy + radius),
                            band * 5,
                            band * 5 + max(10, 120 * v),
                            fill=ink,
                            width=self.line_width,
                        )
                elif mode == "quilt":
                    draw.rectangle((x0, y0, x1, y1), fill=self._ink(band + (time // 2) * 2, v, alpha=175))
                elif mode == "crosshatch":
                    if v >= self.threshold:
                        draw.line((x0, y0, x1, y1), fill=ink, width=1)
                        if v > 0.42:
                            draw.line((x0, y1, x1, y0), fill=ink, width=1)
                elif mode == "triangle-tiles":
                    pts = (
                        [(x0, y1), (x1, y1), (xc, y0)]
                        if (time + band) % 2
                        else [(x0, y0), (x1, y0), (xc, y1)]
                    )
                    draw.polygon(pts, fill=ink)
                elif mode == "pixel-rain":
                    if v >= self.threshold:
                        draw.rectangle((xc - cellw * 0.28, y0, xc + cellw * 0.28, y1), fill=ink)
                else:
                    raise ValueError(f"Unimplemented spectrogram effect: {mode}")
        self._grid(draw, area)

    def _motion(self, draw, frame, area):
        history = self._history(frame)
        if not frame.values:
            raise ValueError("Motion effects need Rudiment channels")
        if self.axis >= len(frame.values):
            raise ValueError("Selected motion effect axis is unavailable")
        mode = self.mode.removeprefix("waveform-")
        left, t, r, b = area
        w, h = r - left, b - t
        n = len(history)
        if not n:
            raise ValueError("No source-clocked rudiment history")
        values = [row.values[self.axis] for row in history]
        if self.reverse:
            values = list(reversed(values))
        # Display gain is symmetric for Rudiment values (signed -1 .. +1).
        values = [_clip(v * self.gain, -1, 1) for v in values]
        if self.quantize > 1:
            values = [round(v * (self.quantize - 1)) / (self.quantize - 1) for v in values]
        mid = (t + b) / 2
        pts = [(left + w * i / max(1, n - 1), mid - v * h * 0.46) for i, v in enumerate(values)]
        self._grid(draw, area)
        if self.baseline:
            draw.line((left, mid, r, mid), fill=(110, 132, 162, 140))
        if mode in {"phosphor", "echo", "horizon", "heat-ribbon", "channel-braid", "butterfly", "dna-helix"}:
            other = [row.values[(self.axis + 1) % len(frame.values)] for row in history]
            if self.reverse:
                other = list(reversed(other))
            pts2 = [
                (left + w * i / max(1, n - 1), mid - v * self.gain * h * 0.42) for i, v in enumerate(other)
            ]
            if mode == "phosphor":
                for j in range(1, n):
                    alpha = round(255 * ((1 - self.decay) + self.decay * j / n))
                    draw.line(
                        (pts[j - 1], pts[j]),
                        fill=self._ink(j, abs(values[j]), alpha=alpha),
                        width=self.line_width,
                    )
            elif mode == "echo":
                for echo in range(4, 0, -1):
                    shift = echo * h * 0.035
                    draw.line(
                        [(x, y + shift) for x, y in pts],
                        fill=self._ink(echo, alpha=round(180 / (echo + 1))),
                        width=self.line_width,
                    )
                if n > 1:
                    draw.line(pts, fill=self._ink(0), width=self.line_width)
            elif mode == "horizon":
                for echo in range(4):
                    shift = echo * h * 0.1
                    draw.line(
                        [(x, y + shift) for x, y in pts],
                        fill=self._ink(echo, alpha=190 - echo * 38),
                        width=self.line_width,
                    )
            elif mode == "heat-ribbon":
                draw.polygon([(left, mid), *pts, (r, mid)], fill=self._ink(0, alpha=110))
                for j in range(1, n):
                    draw.line(
                        (pts[j - 1], pts[j]),
                        fill=self._ink(j, abs(values[j]), alpha=255),
                        width=self.line_width,
                    )
            elif mode == "channel-braid":
                draw.polygon([*pts, *reversed(pts2)], fill=self._ink(2, alpha=70))
                draw.line(pts, fill=self._ink(0), width=self.line_width)
                draw.line(pts2, fill=self._ink(1), width=self.line_width)
            elif mode == "butterfly":
                draw.polygon(
                    [*pts, *[(x, 2 * mid - y) for x, y in reversed(pts2)]], fill=self._ink(0, alpha=110)
                )
                draw.line(pts, fill=self._ink(0), width=self.line_width)
                draw.line([(x, 2 * mid - y) for x, y in pts2], fill=self._ink(1), width=self.line_width)
            else:
                draw.line(pts, fill=self._ink(0), width=self.line_width)
                draw.line([(x, 2 * mid - y) for x, y in pts2], fill=self._ink(1), width=self.line_width)
                for j in range(0, n, max(1, n // 20)):
                    draw.line((pts[j], (pts2[j][0], 2 * mid - pts2[j][1])), fill=self._ink(j, alpha=100))
        elif mode in {
            "sample-beads",
            "pulse-train",
            "quantised",
            "triggered",
            "dotted-envelope",
            "vertical-scope",
            "block-meter",
            "signal-staircase",
        }:
            if mode == "triggered":
                crossings = [i for i in range(1, n) if values[i - 1] < 0 <= values[i]]
                if crossings:
                    cut = crossings[0]
                    trail = values[cut:]
                    pts = [
                        (left + w * i / max(1, len(trail) - 1), mid - v * h * 0.46)
                        for i, v in enumerate(trail)
                    ]
                draw.line(pts, fill=self._ink(0), width=self.line_width)
            elif mode == "vertical-scope":
                p2 = [(mid + v * w * 0.46, t + h * i / max(1, n - 1)) for i, v in enumerate(values)]
                if len(p2) > 1:
                    draw.line(p2, fill=self._ink(0), width=self.line_width)
            elif mode == "signal-staircase":
                for i in range(1, n):
                    draw.line(
                        (pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i - 1][1], pts[i][0], pts[i][1]),
                        fill=self._ink(i),
                        width=self.line_width,
                    )
            else:
                for i, ((x, y), v) in enumerate(zip(pts, values)):
                    ink = self._ink(i, abs(v))
                    radius = min(self.bead_radius, w / max(1, n) * 0.5)
                    if mode == "sample-beads":
                        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=ink)
                    elif mode == "pulse-train":
                        draw.line((x, mid, x, y), fill=ink, width=self.line_width)
                    elif mode == "quantised":
                        draw.line((x, y, x + w / max(1, n) * 0.55, y), fill=ink, width=self.line_width + 1)
                    elif mode == "dotted-envelope":
                        ym = abs(v) * h * 0.46
                        draw.ellipse((x - radius, mid - ym - radius, x + radius, mid - ym + radius), fill=ink)
                        draw.ellipse((x - radius, mid + ym - radius, x + radius, mid + ym + radius), fill=ink)
                    else:  # block-meter
                        width = max(1, w / n * (1 - self.gap))
                        draw.rectangle((x - width * 0.5, min(mid, y), x + width * 0.5, max(mid, y)), fill=ink)
        elif mode in {"spiral-trace", "polar-radar"}:
            cx, cy = (left + r) / 2, (t + b) / 2
            outer = min(w, h) * 0.48
            coords = []
            for i, v in enumerate(values):
                frac = i / max(1, n - 1)
                theta = -math.pi / 2 + frac * math.tau * (2.8 if mode == "spiral-trace" else 1)
                radius = outer * ((0.12 + 0.7 * frac) if mode == "spiral-trace" else (0.48 + 0.42 * v))
                coords.append((cx + math.cos(theta) * radius, cy + math.sin(theta) * radius))
            if len(coords) > 1:
                draw.line(coords, fill=self._ink(0), width=self.line_width)
            if mode == "polar-radar":
                for i in range(0, n, max(1, n // 20)):
                    draw.line((cx, cy, *coords[i]), fill=self._ink(i, alpha=round(130 * i / n)))
        elif mode == "phase-trails":
            if len(frame.values) < 2:
                raise ValueError("Phase trails require two Rudiment axes")
            for i, row in enumerate(history):
                x = left + w * (row.values[0] + 1) / 2
                y = t + h * (1 - row.values[1]) / 2
                radius = min(self.bead_radius, 3)
                draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    fill=self._ink(i, alpha=round(255 * ((1 - self.decay) + self.decay * (i + 1) / n))),
                )
        else:
            raise ValueError(f"Unimplemented motion effect: {mode}")

    def render(self, frame):
        image = self.image()
        draw = ImageDraw.Draw(image)
        area = self.area(max(self.line_width / 2, self.bead_radius))
        if self.requires_spectrum:
            if self.mode.startswith("spectrogram-"):
                self._spectrogram(draw, frame, area)
            else:
                self._spectrum(draw, frame, area)
        else:
            self.frame_report = None
            self._motion(draw, frame, area)
        return ImageOps.mirror(image) if self.mirror else image


def register_effect_renderers(registry):
    for spec in EFFECT_VIEWS:

        def factory(size, style, mode=spec.name):
            return EffectRenderer(size, style, mode)

        registry.register(spec.name, factory)
    return registry
