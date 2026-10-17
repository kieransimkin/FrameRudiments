"""Deterministic row-wise displacement by an earlier isolated matte layer.

This is an image effect, not phase, pitch, audio waveform or a DSP measure.
"""

import math
from PIL import Image, ImageOps


class LayerDisplacement:
    def __init__(self, config, available):
        if not isinstance(config, dict) or len(config) > 32:
            raise ValueError("layer_displacement must map at most 32 layers")
        order = {name: i for i, name in enumerate(available)}
        self.config = {}
        for target, row in config.items():
            if target not in order or not target.startswith("layer:") or not isinstance(row, dict):
                raise ValueError("Displacement target must be existing layer")
            if (
                set(row) != {"source", "amount"}
                or row["source"] not in order
                or order[row["source"]] >= order[target]
            ):
                raise ValueError("Displacement source must precede target")
            amount = row["amount"]
            if type(amount) not in (int, float) or not math.isfinite(amount) or abs(amount) > 128:
                raise ValueError("Displacement amount must be finite and <=128 pixels")
            self.config[target] = (row["source"], amount)

    @property
    def enabled(self):
        return bool(self.config)

    def apply(self, target, image, surfaces):
        entry = self.config.get(target)
        if entry is None:
            return image
        source_name, amount = entry
        source = surfaces.get(source_name)
        if source is None or not amount:
            return image.copy()
        if source.mode != "RGBA" or source.size != image.size or image.mode != "RGBA":
            raise ValueError("Displacement requires equal full-frame RGBA surfaces")
        # Use alpha-weighted luminance of an explicitly earlier layer as a
        # per-row horizontal shift. Never sample outside the frame (transparent).
        luminance = ImageOps.grayscale(source.convert("RGB"))
        alpha = source.getchannel("A")
        output = Image.new("RGBA", image.size)
        for y in range(image.height):
            values = luminance.crop((0, y, image.width, y + 1)).tobytes()
            coverage = alpha.crop((0, y, image.width, y + 1)).tobytes()
            total = sum(v * a for v, a in zip(values, coverage))
            denom = sum(coverage)
            mean = total / denom if denom else 127.5
            offset = round(((mean / 255) * 2 - 1) * amount)
            output.paste(image.crop((0, y, image.width, y + 1)), (offset, y))
        return output
