"""M12.3 pinned ACEScg scene-linear OCIO reference, not an ACES display view.

Transforms linear-sRGB (Rec.709/D65 primaries, linear transfer) to ACEScg
(AP1/D60) through a caller-pinned and trusted official OpenColorIO config.
This does not bake an ACES RRT/ODT or use a display transform as a colour space.
"""

from __future__ import annotations

import hashlib
import math

from .colour_aov import AOVFrame, _pinned, transform_ocio

SCHEMA = "framerudiments.acescg-pinned-reference.v1"


def to_acescg_reference(frame, *, config, config_sha256,
                        source_space="Linear Rec.709 (sRGB)", aces_space="ACEScg",
                        pinned_resources=(), max_roundtrip_abs=0.001):
    """Convert native linear-sRGB AOVs with pinned OCIO; assert round-trip.

    Returns (ACEScg AOVFrame, evidence); geometry and motion are not colour
    channels and are copied byte-for-byte. Input is never mutated. A config
    from the Academy Software Foundation ACES CG release is recommended.
    """
    import numpy as np

    if not isinstance(frame, AOVFrame) or frame.colour_space != "Linear-sRGB":
        raise ValueError("ACEScg reference expects native Linear-sRGB input")
    if aces_space != "ACEScg" or source_space != "Linear Rec.709 (sRGB)":
        raise ValueError("M12.3 requires ACEScg and linear Rec.709/sRGB source primaries")
    if (type(max_roundtrip_abs) not in (int, float) or not math.isfinite(max_roundtrip_abs)
            or not 0 < max_roundtrip_abs <= 0.02):
        raise ValueError("Invalid ACEScg absolute roundtrip bound")
    source = AOVFrame(frame.rgb, source_space, frame.alpha, frame.depth,
                      frame.normals, frame.velocity)
    changed = transform_ocio(source, config=config, config_sha256=config_sha256,
                             source_space=source_space, target_space=aces_space,
                             pinned_resources=pinned_resources)
    restored = transform_ocio(changed, config=config, config_sha256=config_sha256,
                              source_space=aces_space, target_space=source_space,
                              pinned_resources=pinned_resources)
    bound = max_roundtrip_abs * max(1.0, float(np.max(np.abs(source.rgb))))
    error = float(np.max(np.abs(restored.rgb - source.rgb)))
    if not np.isfinite(error) or error > bound:
        raise ValueError("Pinned ACEScg transform fails linear-light roundtrip check")
    for name in ("alpha", "depth", "normals", "velocity"):
        inp, out = getattr(frame, name), getattr(changed, name)
        if (inp is None) != (out is None) or (inp is not None and not np.array_equal(inp, out)):
            raise ValueError(f"ACEScg transform modified non-colour channel {name}")
    file = _pinned(config, config_sha256, suffix=".ocio")
    reference = {
        "schema": SCHEMA, "source_space": source_space,
        "target_space": aces_space, "config_sha256": config_sha256.lower(),
        "config_name": file.name,
        "pinned_resource_sha256": [pair[1].lower() for pair in pinned_resources],
        "roundtrip_max_abs": error, "allowed_abs": bound,
        "view_transform": "none_scene_linear_colour_space_conversion_only",
        "source_rgb_sha256": hashlib.sha256(source.rgb.tobytes()).hexdigest(),
        "acescg_rgb_sha256": hashlib.sha256(changed.rgb.tobytes()).hexdigest(),
        "aovs_unchanged": True,
    }
    return changed, reference
