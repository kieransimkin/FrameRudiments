"""Bounded, deterministic output-time animation of isolated RGBA surfaces."""

import math
from PIL import Image, ImageChops, ImageDraw
from .keyframes import validate_envelope, sample_envelope


def _parameter(value, name, low, high):
    if isinstance(value, dict):
        keys = validate_envelope(value)
        # Envelope values are normalised; map them into the property's range.
        return (keys, low, high)
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite value in [{low}, {high}] or keyframes")
    return value


def _value(value, context):
    if isinstance(value, tuple):
        keys, low, high = value
        return low + sample_envelope(keys, context.output_seconds) * (high - low)
    return value


class LayerAnimations:
    """Animations refer to compositor layer:<id> names, never arbitrary Python."""

    def __init__(self, config, available):
        if not isinstance(config, dict) or len(config) > 64:
            raise ValueError("layer_animations must be an object with <=64 entries")
        self.config = {}
        for name, row in config.items():
            if name not in available or not name.startswith("layer:") or not isinstance(row, dict):
                raise ValueError("Animation target must be an existing layer:<id>")
            if set(row) - {"opacity", "x", "y", "rotation", "clip"}:
                raise ValueError("Unknown layer animation property")
            parsed = {}
            for prop, limits in {
                "opacity": (0, 1),
                "x": (-1024, 1024),
                "y": (-1024, 1024),
                "rotation": (-360, 360),
            }.items():
                if prop in row:
                    parsed[prop] = _parameter(row[prop], prop, *limits)
            if "clip" in row:
                polygon = row["clip"]
                if isinstance(polygon, dict):
                    keys = polygon.get("keys")
                    if set(polygon) != {"keys"} or not isinstance(keys, list) or not 1 <= len(keys) <= 128:
                        raise ValueError("Animated clip requires 1..128 keyframes")
                    samples = []
                    for key in keys:
                        if (
                            not isinstance(key, dict)
                            or set(key) - {"time", "points", "ease"}
                            or not {"time", "points"} <= set(key)
                        ):
                            raise ValueError("Clip key requires time and points")
                        t = key["time"]
                        if (
                            type(t) not in (int, float)
                            or not math.isfinite(t)
                            or t < 0
                            or (samples and t <= samples[-1][0])
                        ):
                            raise ValueError("Clip times must increase")
                        ease = key.get("ease", "linear")
                        if ease not in ("linear", "smoothstep", "hold"):
                            raise ValueError("Invalid clip easing")
                        samples.append((float(t), self._polygon(key["points"]), ease))
                    if len({len(key[1]) for key in samples}) != 1:
                        raise ValueError("Clip vertex count must remain constant")
                    parsed["clip"] = ("animated", tuple(samples))
                else:
                    parsed["clip"] = self._polygon(polygon)
            self.config[name] = parsed

    @staticmethod
    def _polygon(points):
        if (
            not isinstance(points, list)
            or not 3 <= len(points) <= 128
            or any(
                not isinstance(pt, list)
                or len(pt) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in pt)
                for pt in points
            )
        ):
            raise ValueError("Clip must have 3..128 finite coordinate pairs")
        return tuple(tuple(p) for p in points)

    @staticmethod
    def _clip_at(config, seconds):
        if not config or config[0] != "animated":
            return config
        keys = config[1]
        if seconds <= keys[0][0]:
            return keys[0][1]
        if seconds >= keys[-1][0]:
            return keys[-1][1]
        for a, b in zip(keys, keys[1:]):
            if a[0] <= seconds < b[0]:
                t = (seconds - a[0]) / (b[0] - a[0])
                if a[2] == "hold":
                    t = 0
                elif a[2] == "smoothstep":
                    t = t * t * (3 - 2 * t)
                return tuple((x + (u - x) * t, y + (v - y) * t) for (x, y), (u, v) in zip(a[1], b[1]))
        return keys[-1][1]

    @property
    def enabled(self):
        return bool(self.config)

    def apply(self, name, image, context):
        settings = self.config.get(name)
        if settings is None:
            return image
        if image.mode != "RGBA":
            raise ValueError("Layer animation needs RGBA")
        opacity = _value(settings.get("opacity", 1), context)
        dx = round(_value(settings.get("x", 0), context))
        dy = round(_value(settings.get("y", 0), context))
        angle = _value(settings.get("rotation", 0), context)
        result = image.copy()
        if "clip" in settings:
            mask = Image.new("L", image.size)
            ImageDraw.Draw(mask).polygon(self._clip_at(settings["clip"], context.output_seconds), fill=255)
            result.putalpha(ImageChops.multiply(result.getchannel("A"), mask))
        if angle:
            result = result.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)
        if dx or dy:
            moved = Image.new("RGBA", image.size)
            moved.paste(result, (dx, dy))
            result = moved
        if opacity < 1:
            result.putalpha(result.getchannel("A").point(lambda a: round(a * opacity)))
        return result
