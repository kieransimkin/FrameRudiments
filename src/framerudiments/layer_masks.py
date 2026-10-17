"""Deterministic, declared layer-to-layer alpha/luminance track mattes.

Masks operate on post-element-effect isolated RGBA surfaces. The provider must
precede the consumer in actual compositing order; this avoids feedback cycles
and makes exact frame seeking safe, including with temporal supersampling.
"""

from PIL import Image, ImageChops, ImageOps

from .keyframes import validate_envelope, sample_envelope


class LayerMasks:
    def __init__(self, config, available):
        if not isinstance(config, dict) or len(config) > 64:
            raise ValueError("layer_masks must map at most 64 layer names")
        self.config = {}
        order = {name: index for index, name in enumerate(available)}
        for target, item in config.items():
            if not isinstance(target, str) or not target.startswith("layer:") or target not in order:
                raise ValueError("layer_masks target must be an existing layer:<id>")
            if not isinstance(item, dict) or set(item) - {"source", "mode", "invert", "opacity"}:
                raise ValueError("Invalid layer mask configuration")
            source = item.get("source")
            if source not in order or order[source] >= order[target]:
                raise ValueError("Layer mask source must precede target in compositing order")
            mode = item.get("mode", "alpha")
            if mode not in {"alpha", "luminance"} or type(item.get("invert", False)) is not bool:
                raise ValueError("Layer mask mode/invert must be alpha|luminance and boolean")
            opacity = item.get("opacity", 1.0)
            if isinstance(opacity, dict):
                opacity = validate_envelope(opacity)
            elif type(opacity) not in (int, float) or not 0 <= opacity <= 1:
                raise ValueError("Mask opacity must be 0..1 or a keyframe envelope")
            self.config[target] = {
                "source": source,
                "mode": mode,
                "invert": item.get("invert", False),
                "opacity": opacity,
            }

    @property
    def enabled(self):
        return bool(self.config)

    def apply(self, name, image, surfaces, context=None):
        entry = self.config.get(name)
        if entry is None:
            return image
        source = surfaces.get(entry["source"])
        if source is None:
            mask = Image.new("L", image.size, 0)
        else:
            if source.mode != "RGBA" or source.size != image.size:
                raise ValueError("Mask source must be full-frame RGBA")
            mask = source.getchannel("A")
            if entry["mode"] == "luminance":
                mask = ImageChops.multiply(mask, ImageOps.grayscale(source.convert("RGB")))
        if entry["invert"]:
            mask = ImageOps.invert(mask)
        opacity = entry["opacity"]
        if isinstance(opacity, tuple):
            if context is None:
                raise ValueError("Animated mask opacity requires frame context")
            opacity = sample_envelope(opacity, context.output_seconds)
        if opacity < 1:
            mask = mask.point(lambda a: round(a * opacity))
        result = image.copy()
        result.putalpha(ImageChops.multiply(result.getchannel("A"), mask))
        return result
