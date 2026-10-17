"""Project-wide, default-maximum spatial and temporal supersampling policy.

The spatial stage redraws pinned fonts and procedural vector geometry at four
physical source pixels per output pixel. Temporal subframes use a temporary
high-fps compositor bound to the *same* source audio and beat timeline.
"""

from PIL import Image

from .io import fields, integer, number
from .quality_effects import edge_aware_antialias


class OutputQuality:
    """Validated quality controls. Values below the maxima require explicit JSON."""

    MAX_SPATIAL = 4
    MAX_TEMPORAL = 8

    def __init__(self, config=None):
        config = config or {}
        fields(
            config,
            {
                "enabled",
                "spatial_scale",
                "temporal_samples",
                "antialias",
                "antialias_radius",
                "antialias_threshold",
                "antialias_passes",
            },
            "render_quality",
        )
        self.enabled = config.get("enabled", True)
        if type(self.enabled) is not bool:
            raise ValueError("render_quality.enabled must be a boolean")
        self.spatial_scale = integer(
            config.get("spatial_scale", self.MAX_SPATIAL),
            "render_quality.spatial_scale",
            minimum=1,
            maximum=self.MAX_SPATIAL,
        )
        self.temporal_samples = integer(
            config.get("temporal_samples", self.MAX_TEMPORAL),
            "render_quality.temporal_samples",
            minimum=1,
            maximum=self.MAX_TEMPORAL,
        )
        self.strength = number(config.get("antialias", 1), "output antialias strength", minimum=0, maximum=1)
        self.radius = number(
            config.get("antialias_radius", 0.75), "output antialias radius", minimum=0.25, maximum=3
        )
        self.threshold = number(
            config.get("antialias_threshold", 0.04), "output antialias threshold", minimum=0, maximum=0.95
        )
        self.passes = integer(
            config.get("antialias_passes", 3), "output antialias passes", minimum=1, maximum=3
        )
        self.evidence = dict(
            enabled=self.enabled,
            spatial_scale=self.spatial_scale if self.enabled else 1,
            temporal_samples=self.temporal_samples if self.enabled else 1,
            antialias=self.strength if self.enabled else 0,
            antialias_radius=self.radius,
            antialias_threshold=self.threshold,
            antialias_passes=self.passes,
            spatial_method="true high-resolution pinned-font and procedural-vector raster; Lanczos reduction",
            temporal_method="real native-source-clock subframe renders; linear-light shutter integration",
            final_method="premultiplied edge-aware AA after GPU output, before encode",
            optical_flow=False,
            gpu_feedback_subframes=False,
        )

    @property
    def vector_scale(self):
        return self.spatial_scale if self.enabled else 1

    @property
    def time_samples(self):
        return self.temporal_samples if self.enabled else 1

    def virtual_quality(self):
        """A source-time sampling compositor must not recursively oversample."""
        return OutputQuality(
            dict(
                enabled=self.enabled,
                spatial_scale=self.spatial_scale,
                temporal_samples=1,
                antialias=self.strength,
                antialias_radius=self.radius,
                antialias_threshold=self.threshold,
                antialias_passes=self.passes,
            )
        )

    def apply(self, image):
        """Final spatial AA, once per finished encoded frame (after GPU effects)."""
        if not isinstance(image, Image.Image) or image.mode not in {"RGB", "RGBA"}:
            raise ValueError("Output quality requires an RGB or RGBA frame")
        if not self.enabled or not self.strength or min(image.size) < 2:
            return image
        rgba = image.convert("RGBA") if image.mode == "RGB" else image
        result = edge_aware_antialias(
            rgba, strength=self.strength, radius=self.radius, threshold=self.threshold, passes=self.passes
        )
        return result.convert("RGB") if image.mode == "RGB" else result
