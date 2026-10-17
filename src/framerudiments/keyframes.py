"""Deterministic output-time keyframe envelopes for compositor properties.

No implicit DSP or motion synthesis: samples are pure functions of timestamp.
DanceRudiments motion and StemLab recordings remain independent drivers.
"""

import bisect
import math


def validate_envelope(config):
    if not isinstance(config, dict) or set(config) != {"keys"}:
        raise ValueError("Keyframe envelope needs only keys")
    keys = config["keys"]
    if not isinstance(keys, list) or not 1 <= len(keys) <= 128:
        raise ValueError("Keyframe envelope needs 1..128 points")
    times = []
    result = []
    for item in keys:
        if (
            not isinstance(item, dict)
            or set(item) - {"time", "value", "ease"}
            or not {"time", "value"} <= set(item)
        ):
            raise ValueError("Keyframe needs time and value, optionally ease")
        t, v = item["time"], item["value"]
        if type(t) not in (int, float) or not math.isfinite(t) or t < 0:
            raise ValueError("Keyframe time must be finite nonnegative seconds")
        if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1:
            raise ValueError("Keyframe value must be between 0 and 1")
        if times and t <= times[-1]:
            raise ValueError("Keyframe times must strictly increase")
        ease = item.get("ease", "linear")
        if ease not in ("linear", "hold", "smoothstep"):
            raise ValueError("Unknown keyframe easing")
        times.append(float(t))
        result.append((float(t), float(v), ease))
    return tuple(result)


def sample_envelope(keys, seconds):
    """Clamp to endpoints; easing applies to the segment's starting key."""
    if not math.isfinite(seconds):
        raise ValueError("Nonfinite keyframe sample time")
    if seconds <= keys[0][0]:
        return keys[0][1]
    if seconds >= keys[-1][0]:
        return keys[-1][1]
    index = bisect.bisect_right([key[0] for key in keys], seconds) - 1
    a, b = keys[index], keys[index + 1]
    u = (seconds - a[0]) / (b[0] - a[0])
    if a[2] == "hold":
        u = 0
    elif a[2] == "smoothstep":
        u = u * u * (3 - 2 * u)
    return a[1] + u * (b[1] - a[1])
