"""Circular, annular and polar view geometries over pinned Rudiment samples.

The spectral family reads *calibrated* StemLab frequency-band recordings;
waveform/phase families read native DanceRudiment values, NOT decoded PCM.
All geometry depends only on the immutable RudimentFrame and validated style.
"""
import math

from PIL import ImageDraw

from .io import integer, number
from .rudiment_renderers import _Viewport
from .spectrum import analyse_spectrum, peak_indices
from .visualisation_catalogue import CIRCULAR_VIEWS


def _clamp(v):
    return max(0.0, min(1.0, v))


def _reduce(rows, n):
    if len(rows) <= n:
        return rows
    return [rows[round(i * (len(rows) - 1) / (n - 1))] for i in range(n)]


def _frequency(f, scale):
    if scale == "log":
        return math.log2(max(1, f))
    if scale == "mel":
        return 2595 * math.log10(1 + f / 700)
    if scale == "bark":
        return 13 * math.atan(.00076 * f) + 3.5 * math.atan((f / 7500) ** 2)
    if scale == "erb":
        return 21.4 * math.log10(1 + .00437 * f)
    return f


class CircularRenderer(_Viewport):
    """60 distinct polar drawing variants with a consistent parameter model."""

    def __init__(self, size, style, mode):
        super().__init__(size, style, {
            "inner_radius", "outer_radius", "start_angle", "sweep_angle", "clockwise", "line_width",
            "bar_gap", "bar_width", "gain", "floor_db", "ceiling_db", "fill_alpha",
            "led_segments", "rings", "history_limit", "axis", "twists", "grid",
            "frequency_scale", "cap_radius", "peak_threshold", "peak_count", "decay", "mirror",
        })
        self.mode = mode
        self.inner = number(style.get("inner_radius", .27), "inner radius", minimum=0, maximum=.95)
        self.outer = number(style.get("outer_radius", .96), "outer radius", minimum=.05, maximum=1)
        if self.inner >= self.outer:
            raise ValueError("inner_radius must be smaller than outer_radius")
        self.start = number(style.get("start_angle", 0), "start angle", minimum=-36000, maximum=36000)
        self.sweep = number(style.get("sweep_angle", 360), "sweep angle", minimum=5, maximum=360)
        self.clockwise = style.get("clockwise", True)
        self.mirror = style.get("mirror", False)
        self.grid = style.get("grid", False)
        if any(not isinstance(flag, bool) for flag in (self.clockwise, self.mirror, self.grid)):
            raise ValueError("clockwise/mirror/grid must be boolean")
        self.stroke = integer(style.get("line_width", 3), "circular stroke", minimum=1, maximum=64)
        self.gap = number(style.get("bar_gap", .16), "angular bar gap", minimum=0, maximum=.9)
        self.bar_width = number(style.get("bar_width", 1), "bar width multiplier", minimum=.1, maximum=1)
        self.gain = number(style.get("gain", 1), "visual gain", minimum=0, maximum=8)
        self.floor = number(style.get("floor_db", -72), "spectrum floor", minimum=-240, maximum=0)
        self.ceiling = number(style.get("ceiling_db", 0), "spectrum ceiling", minimum=self.floor + .01, maximum=60)
        self.alpha = integer(style.get("fill_alpha", 170), "fill alpha", minimum=0, maximum=255)
        self.led_segments = integer(style.get("led_segments", 16), "LED segments", minimum=2, maximum=64)
        self.rings = integer(style.get("rings", 4), "annular rings", minimum=1, maximum=12)
        self.history_limit = integer(style.get("history_limit", 40), "annular history", minimum=4, maximum=256)
        self.axis = integer(style.get("axis", 0), "annular motion axis", minimum=0, maximum=255)
        self.twists = number(style.get("twists", 2), "spiral turns", minimum=.2, maximum=12)
        self.cap_radius = number(style.get("cap_radius", 2.5), "cap radius", minimum=.5, maximum=20)
        self.peak_threshold = number(style.get("peak_threshold", .18), "peak threshold", minimum=0, maximum=1)
        self.peak_count = integer(style.get("peak_count", 16), "peak count", minimum=1, maximum=64)
        self.decay = number(style.get("decay", .65), "history decay", minimum=0, maximum=1)
        self.frequency_scale = style.get("frequency_scale", "equal")
        if self.frequency_scale not in {"equal", "linear", "log", "mel", "bark", "erb"}:
            raise ValueError("Unknown annular frequency axis scale")
        self.area(max(2, self.stroke / 2, self.cap_radius))
        self.frame_report = None
        self.requires_spectrum = mode.startswith(("spectrum-", "spectrogram-"))

    def _geometry(self):
        left, top, right, bottom = self.area(max(2, self.stroke / 2, self.cap_radius))
        self.cx, self.cy = (left + right) / 2, (top + bottom) / 2
        radius = max(2.0, min(right - left, bottom - top) / 2)
        self.r0, self.r1 = radius * self.inner, radius * self.outer

    def _theta(self, fraction):
        # Zero is 12 o'clock, positive angles clockwise like a clock dial.
        return math.radians(self.start - 90 + (1 if self.clockwise else -1) * self.sweep * fraction)

    def _point(self, fraction, radius):
        a = self._theta(fraction)
        return (self.cx + math.cos(a) * radius, self.cy + math.sin(a) * radius)

    def _dot(self, draw, fraction, radius, colour, r=None):
        x, y = self._point(fraction, radius)
        r = r if r is not None else self.cap_radius
        draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)

    def _line(self, draw, fraction, a, b, colour, width=None):
        draw.line((*self._point(fraction, a), *self._point(fraction, b)),
                  fill=colour, width=self.stroke if width is None else width)

    def _arc(self, draw, r, f0, f1, colour, width=None):
        # Pillow arcs always sweep forward; for reverse arcs sample explicitly.
        count = max(2, math.ceil(abs(f1 - f0) * 250))
        coords = [self._point(f0 + (f1 - f0) * j / (count - 1), r) for j in range(count)]
        draw.line(coords, fill=colour, width=width or self.stroke)

    def _sector(self, draw, f0, f1, a, b, colour):
        # Four vertices give narrow musical analyser bars. For wider wedges,
        # subdivide the curved edges so they look like actual annular sectors.
        steps = max(2, math.ceil(abs(f1 - f0) * 180))
        points = ([self._point(f0 + (f1 - f0) * j / steps, b) for j in range(steps + 1)]
                  + [self._point(f1 - (f1 - f0) * j / steps, a) for j in range(steps + 1)])
        draw.polygon(points, fill=colour)

    def _bands(self, frame):
        if not frame.spectrum_bands or len(frame.spectrum_bands) != len(frame.values):
            raise ValueError("Annular spectrum needs calibrated selected StemLab Fourier bands")
        report = analyse_spectrum(frame, floor=self.floor, ceiling=self.ceiling)
        self.frame_report = report
        levels = [self._level(b, v) for b, v in zip(frame.spectrum_bands, frame.values)]
        return levels

    def _level(self, band, value):
        power = band.rms(value)
        db = 20 * math.log10(power) if power > 0 else self.floor
        return _clamp((db - self.floor) / (self.ceiling - self.floor) * self.gain)

    def _positions(self, frame):
        if self.frequency_scale == "equal":
            n = len(frame.values)
            return [(i / n, (i + 1) / n, (i + .5) / n) for i in range(n)]
        bands = frame.spectrum_bands
        a = _frequency(bands[0].low_hz, self.frequency_scale)
        b = _frequency(bands[-1].high_hz, self.frequency_scale)
        den = max(1e-10, b - a)
        return [((_frequency(row.low_hz, self.frequency_scale) - a) / den,
                 (_frequency(row.high_hz, self.frequency_scale) - a) / den,
                 (_frequency((row.low_hz + row.high_hz) / 2, self.frequency_scale) - a) / den)
                for row in bands]

    def _slots(self, segments):
        n = len(segments)
        for j, value in enumerate(segments):
            yield j / n, (j + 1) / n, (j + .5) / n, value

    def _bar(self, draw, slot, a, b, colour):
        f0, f1, center = slot
        width = (f1 - f0) * (1 - self.gap) * self.bar_width
        if width * min(self.r1, self.r0 + 8) * math.radians(self.sweep) <= .65:
            self._line(draw, center, a, b, colour, width=max(1, round(self.stroke / 2)))
        else:
            self._sector(draw, center - width / 2, center + width / 2, a, b, colour)

    def _ink(self, i, alpha=255):
        c = self.colours[i % len(self.colours)]
        return (*c[:3], round(c[3] * alpha / 255))

    def _plot(self, draw, levels, slots=None, *, phase=0, close=False, fill=False, alpha=None, radii=None):
        slots = slots or [(a, b, c) for a, b, c, _ in self._slots(levels)]
        r0, r1 = radii or (self.r0, self.r1)
        pts = [self._point((slot[2] + phase) % 1, r0 + _clamp(level) * (r1 - r0))
               for slot, level in zip(slots, levels)]
        if len(pts) > 1:
            if fill:
                inner = [self._point(slot[2], r0) for slot in reversed(slots)]
                draw.polygon(pts + inner, fill=self._ink(0, alpha if alpha is not None else self.alpha))
            draw.line(pts + ([pts[0]] if close and self.sweep == 360 else []),
                      fill=self._ink(1 if fill else 0), width=self.stroke, joint="curve")
        elif pts:
            self._dot(draw, slots[0][2], r0 + levels[0] * (r1 - r0), self._ink(0))
        return pts

    def _spectrum(self, draw, frame):
        levels = self._bands(frame)
        slots = self._positions(frame)
        mode = self.mode.removeprefix("spectrum-annular-")
        span = self.r1 - self.r0
        mid = (self.r0 + self.r1) / 2
        if mode in {"bars", "bars-inward", "bars-bipolar", "bars-mirror", "split", "sunburst", "fan", "petals"}:
            for i, (s0, s1, center) in enumerate(slots):
                v = levels[i]
                if mode == "bars-inward":
                    a, b = self.r1 - span * v, self.r1
                elif mode in {"bars-bipolar", "bars-mirror"}:
                    a, b = mid - span * v / 2, mid + span * v / 2
                elif mode == "split":
                    a, b = (self.r1 - span * v, self.r1) if i % 2 else (self.r0, self.r0 + span * v)
                elif mode == "petals":
                    a, b = self.r0, self.r0 + span * v
                    self._sector(draw, s0 + (s1 - s0) * .44, s1 - (s1 - s0) * .44,
                                 self.r0, b, self._ink(i, self.alpha))
                    continue
                elif mode == "sunburst":
                    a, b = self.r0, self.r0 + span * v * (.5 if i % 2 else 1)
                else:
                    a, b = self.r0, self.r0 + span * v
                colour = self._ink(i, self.alpha if mode == "fan" else 255)
                if mode == "fan":
                    self._sector(draw, s0, s1, a, b, colour)
                else:
                    self._bar(draw, (s0, s1, center), a, b, colour)
                    if mode == "bars-mirror":
                        self._dot(draw, center, b, colour, r=self.cap_radius * .7)
        elif mode in {"led", "led-inward", "ticks", "arc", "dial"}:
            for i, (s0, s1, center) in enumerate(slots):
                v = levels[i]
                if mode == "ticks":
                    r = self.r0 + span * v
                    self._line(draw, center, r - self.stroke * 1.5, r + self.stroke * 1.5, self._ink(i))
                elif mode == "arc":
                    r = self.r0 + span * i / max(1, len(slots) - 1)
                    self._arc(draw, r, s0, s0 + (s1 - s0) * v * (1 - self.gap), self._ink(i))
                elif mode == "dial":
                    self._line(draw, center, self.r0, self.r0 + span * v, self._ink(i))
                    self._dot(draw, center, self.r0 + span * v, "#ffffff", self.cap_radius)
                else:
                    for j in range(self.led_segments):
                        a = j / self.led_segments
                        if a >= v:
                            break
                        b = (j + .7) / self.led_segments
                        r0 = self.r1 - b * span if mode == "led-inward" else self.r0 + a * span
                        r1 = self.r1 - a * span if mode == "led-inward" else self.r0 + b * span
                        self._bar(draw, (s0, s1, center), r0, r1, self._ink(i))
        elif mode in {"dots", "lollipop", "orbit", "caterpillar", "peaks"}:
            chosen = set(range(len(levels)))
            if mode == "peaks":
                chosen = {j for j, _, _, _ in peak_indices(levels, floor=0, threshold=self.peak_threshold,
                                                           contrast=.04, distance=2, count=self.peak_count)}
            for i, (s0, s1, center) in enumerate(slots):
                if i not in chosen:
                    continue
                angle = center
                if mode == "orbit":
                    angle = center + (levels[i] - .5) / max(1, len(levels))
                r = self.r0 + span * levels[i]
                colour = self._ink(i)
                if mode in {"lollipop", "peaks"}:
                    self._line(draw, angle, self.r0, r, colour)
                self._dot(draw, angle, r, colour, self.cap_radius * (1.5 if mode == "caterpillar" else 1))
        elif mode in {"area", "line", "ribbon", "halo", "rose", "concentric"}:
            if mode == "rose":
                folded = [(_clamp(v) if i % 2 == 0 else _clamp(v) * .4) for i, v in enumerate(levels)]
                self._plot(draw, folded, slots, close=True, fill=True)
            elif mode == "concentric":
                for ring in range(self.rings):
                    frac = (ring + 1) / (self.rings + 1)
                    shifted = [v * (.25 + frac * .75) for v in levels]
                    self._plot(draw, shifted, slots, close=True,
                               radii=(self.r0 + span * frac * .12, self.r0 + span * (.28 + .67 * frac)))
            else:
                self._plot(draw, levels, slots, close=True, fill=mode in {"area", "ribbon", "halo"},
                           alpha=75 if mode == "halo" else None)
                if mode == "ribbon":
                    self._plot(draw, [v * .65 for v in levels], slots, close=True)
                if mode == "halo":
                    for i in range(2):
                        self._plot(draw, [v * (1 - .12 * (i + 1)) for v in levels], slots, close=True)
        elif mode in {"sectors", "layers", "gauge"}:
            if mode == "gauge":
                average = sum(levels) / max(1, len(levels))
                self._sector(draw, 0, average, self.r0, self.r1, self._ink(0, self.alpha))
                self._arc(draw, mid, 0, average, self._ink(1), max(3, self.stroke * 2))
            elif mode == "sectors":
                for i, (a, b, _) in enumerate(slots):
                    self._sector(draw, a, a + (b - a) * (1 - self.gap), self.r0,
                                 self.r0 + span * max(.1, levels[i]), self._ink(i, self.alpha))
            else:
                for i, (a, b, _) in enumerate(slots):
                    lane = i % self.rings
                    lo = self.r0 + span * lane / self.rings
                    hi = lo + span / self.rings * levels[i]
                    self._sector(draw, a, b - (b - a) * self.gap, lo, hi, self._ink(i))
        elif mode in {"spiral", "helix"}:
            points = []
            for j, v in enumerate(levels):
                frac = j / max(1, len(levels) - 1)
                angle = (frac * self.twists) % 1
                radius = self.r0 + span * (.1 + .7 * frac + .2 * v)
                points.append(self._point(angle, radius))
            if len(points) > 1:
                draw.line(points, fill=self._ink(0), width=self.stroke)
            for j, point in enumerate(points):
                r = self.cap_radius * .6
                draw.ellipse((point[0]-r, point[1]-r, point[0]+r, point[1]+r), fill=self._ink(j))
        else:
            raise ValueError(f"Unimplemented circular spectrum renderer: {self.mode}")

    def _spectrogram(self, draw, frame):
        if not frame.spectrum_bands or len(frame.spectrum_bands) != len(frame.values):
            raise ValueError("Annular spectrogram needs calibrated Fourier bands")
        self.frame_report = analyse_spectrum(frame, floor=self.floor, ceiling=self.ceiling)
        mode = self.mode.removeprefix("spectrogram-annular-")
        history = _reduce(frame.history, min(self.history_limit, 80))
        n = len(history)
        if not n:
            return
        bands = frame.spectrum_bands
        positions = self._positions(frame)
        # Raster-bound at most 96 frequency bands × 80 observed frames.
        chosen = list(range(len(bands)))
        if len(chosen) > 96:
            chosen = [round(i * (len(bands) - 1) / 95) for i in range(96)]
        span = self.r1 - self.r0
        if mode == "spiral":
            positions2 = []
            for i, sample in enumerate(history):
                levels = [self._level(b, sample.values[j]) for j, b in enumerate(bands)]
                dominant = max(range(len(levels)), key=levels.__getitem__)
                frac = i / max(1, n - 1)
                radius = self.r0 + span * (.12 + .72 * frac + .12 * levels[dominant])
                positions2.append(self._point(frac * self.twists % 1, radius))
            if len(positions2) > 1:
                draw.line(positions2, fill=self._ink(0), width=self.stroke)
            return
        for i, sample in enumerate(history):
            values = [self._level(b, sample.values[j]) for j, b in enumerate(bands)]
            t0, t1 = i / n, (i + 1) / n
            for j in chosen:
                b0, b1, _ = positions[j]
                level = values[j]
                if mode == "contour":
                    level = round(level * 6) / 6
                if mode == "stripes" and (i // 2 + j // 2) % 2:
                    level *= .55
                if mode == "sweep":
                    level *= .28 + .72 * (i + 1) / n
                # At given t and frequency, no interpolation invents new DSP.
                if mode in {"time", "rings", "heatmap", "stripes", "contour"}:
                    a0, a1 = b0, b1
                    r0, r1 = self.r0 + span * t0, self.r0 + span * t1
                    if mode == "rings":
                        r1 = r0 + (r1 - r0) * .72
                elif mode == "frequency":
                    a0, a1 = t0, t1
                    r0, r1 = self.r0 + span * b0, self.r0 + span * b1
                else:  # sweep: angle=time, radius=frequency
                    a0, a1 = t0, t1
                    r0, r1 = self.r0 + span * b0, self.r0 + span * b1
                base = self._ink(j)
                colour = (*base[:3], round(level * self.alpha))
                if level > .005:
                    self._sector(draw, a0, a1, r0, r1, colour)

    def _motion(self, draw, frame):
        if not frame.history or not frame.values or self.axis >= len(frame.values):
            raise ValueError("Annular waveform requires observed selected Rudiment axes")
        mode = self.mode.removeprefix("waveform-annular-")
        history = _reduce(frame.history, min(self.history_limit * 3, 240))
        n = len(history)
        values = [max(-1, min(1, s.values[self.axis])) for s in history]
        slots = list(self._slots(values))
        span = self.r1 - self.r0
        mid = (self.r0 + self.r1) / 2
        if mode in {"line", "fill", "area", "envelope", "halo", "rose", "pulse"}:
            transformed = [(.5 + .46 * v) if mode != "envelope" else abs(v) for v in values]
            if mode == "rose":
                transformed = [abs(v) * (.5 if j % 2 else 1) for j, v in enumerate(values)]
            if mode == "pulse":
                transformed = [(.5 + .35 * abs(v)) for v in values]
            coord = [self._point(c, self.r0 + _clamp(v) * span) for (_, _, c, _), v in zip(slots, transformed)]
            if len(coord) > 1:
                if mode in {"fill", "area", "halo", "pulse", "rose"}:
                    inner = [self._point(c, mid if mode in {"fill", "pulse"} else self.r0)
                             for _, _, c, _ in reversed(slots)]
                    draw.polygon(coord + inner, fill=self._ink(0, 65 if mode == "halo" else self.alpha))
                draw.line(coord + ([coord[0]] if self.sweep == 360 else []),
                          fill=self._ink(1 if mode != "line" else 0), width=self.stroke)
                if mode == "pulse":
                    for _, _, c, v in slots:
                        self._dot(draw, c, mid, self._ink(1, round(120 * abs(v))), self.cap_radius)
            else:
                self._dot(draw, slots[0][2], self.r0 + transformed[0] * span, self._ink(0))
        elif mode in {"mirror", "ribbon", "rings", "bands"}:
            if mode in {"ribbon", "bands"} and len(frame.values) < 2:
                raise ValueError("Annular ribbon/bands need two selected Rudiment dimensions")
            second = [max(-1, min(1, s.values[1])) for s in history] if len(frame.values) >= 2 else values
            outer = [self._point(c, mid + .45 * span * abs(v)) for _, _, c, v in slots]
            inner = [self._point(c, mid - .45 * span * abs(v2 if mode in {"ribbon", "bands"} else v))
                     for (_, _, c, v), v2 in zip(slots, second)]
            if len(outer) > 1:
                draw.polygon([*outer, *reversed(inner)], fill=self._ink(0, self.alpha))
                draw.line(outer, fill=self._ink(0), width=self.stroke)
                draw.line(inner, fill=self._ink(1), width=self.stroke)
                if mode in {"rings", "bands"}:
                    for axis in range(min(self.rings, len(frame.values))):
                        rd = self.r0 + span * axis / min(self.rings, len(frame.values))
                        for _, _, c, item in slots:
                            self._dot(draw, c, rd + max(0, item) * span / (self.rings + 1),
                                      self._ink(axis, 160), self.cap_radius * .4)
        elif mode in {"bars", "spokes", "led", "petals", "sectors", "arc", "dots", "radar", "trails"}:
            for i, (a, b, center, v) in enumerate(slots):
                level = abs(v) * self.gain
                r = mid + v * span * .45 if mode in {"bars", "spokes", "dots", "trails", "radar"} else self.r0 + span * level
                colour = self._ink(i)
                if mode in {"bars", "spokes", "radar"}:
                    self._line(draw, center, mid, r, colour)
                elif mode == "led":
                    for k in range(self.led_segments):
                        if (k + 1) / self.led_segments > level:
                            break
                        self._bar(draw, (a, b, center), self.r0 + span * k / self.led_segments,
                                  self.r0 + span * (k + .7) / self.led_segments, colour)
                elif mode == "petals":
                    self._sector(draw, a + .35 * (b - a), b - .35 * (b - a), self.r0, r,
                                 self._ink(i, self.alpha))
                elif mode == "sectors":
                    self._sector(draw, a, a + (b - a) * level, self.r0, self.r1, self._ink(i, self.alpha))
                elif mode == "arc":
                    self._arc(draw, self.r0 + span * i / max(1, n - 1), a, b,
                              self._ink(i, round(255 * (.2 + .8 * level))))
                elif mode in {"dots", "trails"}:
                    alpha = round(255 * (.15 + .85 * (i + 1) / n) ** (2 if mode == "trails" else 1))
                    self._dot(draw, center, r, self._ink(i, alpha))
                if mode == "radar":
                    self._dot(draw, center, r, colour, self.cap_radius * .55)
        elif mode in {"spiral", "helix"}:
            points = []
            for j, v in enumerate(values):
                time = j / max(1, n - 1)
                theta = (time * self.twists) % 1
                drift = .65 * time + .25 * (.5 + .5 * v)
                if mode == "helix":
                    drift += .1 * math.sin(time * self.twists * 2 * math.pi)
                points.append(self._point(theta, self.r0 + span * _clamp(drift)))
            if len(points) > 1:
                draw.line(points, fill=self._ink(0), width=self.stroke)
        else:
            raise ValueError(f"Unimplemented circular waveform renderer: {self.mode}")

    def _phase(self, draw, frame):
        if len(frame.values) < 2:
            raise ValueError("Annular phase plot needs two Rudiment dimensions")
        history = _reduce(frame.history, min(256, self.history_limit * 4))
        pts = [self._point(_clamp((s.values[0] + 1) / 2),
                           self.r0 + _clamp((s.values[1] + 1) / 2) * (self.r1 - self.r0)) for s in history]
        if len(pts) >= 2:
            draw.line(pts, fill=self._ink(0), width=self.stroke)
        for j, (x, y) in enumerate(pts):
            draw.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=self._ink(j))

    def render(self, frame):
        self._geometry()
        image = self.image()
        draw = ImageDraw.Draw(image)
        if self.grid:
            for radius in (self.r0, (self.r0 + self.r1) / 2, self.r1):
                self._arc(draw, radius, 0, 1, (75, 94, 121, 150), width=1)
        if self.mode.startswith("spectrum-annular-"):
            self._spectrum(draw, frame)
        elif self.mode.startswith("spectrogram-annular-"):
            self._spectrogram(draw, frame)
        elif self.mode.startswith("waveform-annular-"):
            self._motion(draw, frame)
        elif self.mode.startswith("phase-annular-"):
            self._phase(draw, frame)
        else:
            raise ValueError("Unknown circular renderer")
        return image


def register_circular_renderers(registry):
    for spec in CIRCULAR_VIEWS:
        def factory(size, style, mode=spec.name):
            return CircularRenderer(size, style, mode)
        registry.register(spec.name, factory)
    return registry
