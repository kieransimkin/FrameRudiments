"""Deterministic, alpha-aware reveal masks for the three-input transition node.

This is a CPU reference implementation. All time dependence comes exclusively
from the graph's native source-clocked ``progress`` control. No frame history,
random state, hidden keyframes or audio resampling are introduced here.
"""
from __future__ import annotations

import math

from PIL import Image

from .effect_graph_mattes import KEY_SPACES, chroma_key, validate_key

MATTE_TRANSITION_CHANNELS = frozenset({"alpha", "luminance", "chroma"})
MATTE_TRANSITION_ONLY = frozenset({
    "matte_channel", "invert", "threshold", "mask_gamma", "key_color",
    "key_space", "tolerance", "softness",
})


def _finite(value, label, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"transition {label} must be finite in [{minimum}, {maximum}]")


def validate_transition_matte(config):
    """Validate before render; prevent silent no-op parameters on older styles."""
    if config.get("transition_style", "dissolve") != "matte":
        extra = MATTE_TRANSITION_ONLY.intersection(config)
        if extra:
            raise ValueError(f"matte-only transition settings require matte style: {sorted(extra)}")
        return
    channel = config.get("matte_channel", "alpha")
    if not isinstance(channel, str) or channel not in MATTE_TRANSITION_CHANNELS:
        raise ValueError("transition matte_channel must be alpha, luminance or chroma")
    if type(config.get("invert", False)) is not bool:
        raise ValueError("transition matte invert must be a boolean")
    _finite(config.get("threshold", .5), "threshold", 0, 1)
    _finite(config.get("mask_gamma", 1), "mask_gamma", .1, 8)
    if channel == "chroma" or {"key_color", "key_space", "tolerance", "softness"}.intersection(config):
        # Also validate dormant key settings: a DanceRudiment may switch the
        # matte_channel to chroma later in the same clip.
        validate_key(config)
    if "key_space" in config and config["key_space"] not in KEY_SPACES:
        raise ValueError("invalid transition key_space")


def transition_matte_weight(matte: Image.Image, config: dict, progress: float):
    """Build incoming-image reveal weight from an RGBA matte surface.

    Brighter/higher-alpha values reveal B first; transparent luminance and
    chroma samples never reveal B accidentally. Threshold is the mask cutoff at
    progress 0.5, while progress shifts that cutoff without changing the mask.
    """
    import numpy as np

    channel = config.get("matte_channel", "alpha")
    rgba = np.asarray(matte, dtype=np.float32) / 255
    mask_alpha = rgba[..., 3]
    if channel == "alpha":
        strength = mask_alpha
    elif channel == "luminance":
        # Pillow's traditional luma coefficients, multiplied by matte alpha.
        strength = (rgba[..., 0] * .299 + rgba[..., 1] * .587 + rgba[..., 2] * .114) * mask_alpha
    else:
        # The established chroma keyer returns the *kept* alpha. Subtract it
        # from the source alpha to obtain a positive colour-selection matte.
        key_config = {k: config[k] for k in ("key_color", "key_space", "tolerance", "softness") if k in config}
        kept = np.asarray(chroma_key(matte, key_config).getchannel("A"), dtype=np.float32) / 255
        strength = np.maximum(0, mask_alpha - kept)
    if config.get("invert", False):
        strength = 1 - strength
    gamma = config.get("mask_gamma", 1)
    if gamma != 1:
        strength = np.power(np.clip(strength, 0, 1), gamma)
    threshold = config.get("threshold", .5) + .5 - progress
    feather = config.get("feather", 0)
    if feather:
        t = np.clip((strength - threshold) / feather + .5, 0, 1)
        return t * t * (3 - 2 * t)
    return (strength >= threshold).astype(np.float32)
