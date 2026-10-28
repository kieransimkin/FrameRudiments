"""Deterministic chroma-key and track-matte compositing for visualiser stages."""

from PIL import Image, ImageChops, ImageColor, ImageOps

from .io import fields, number


COMPOSITE_MODULATION_TARGETS = {
    "composite.opacity",
    "composite.matte.opacity",
    "composite.chroma_key.similarity",
    "composite.chroma_key.smoothness",
    "composite.chroma_key.despill",
}


def _colour(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a CSS colour string")
    try:
        rgb = ImageColor.getrgb(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a CSS colour string") from exc
    if len(rgb) != 3:
        raise ValueError(f"{label} must not include alpha")
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


def validate_stage_composite(config, label="visualiser composite"):
    """Validate one stage's post-render compositing contract."""
    if config is None:
        return None
    fields(config, {"opacity", "chroma_key", "matte", "underlay", "modulations"}, label)
    if not config:
        raise ValueError(f"{label} must declare opacity, chroma_key, matte or underlay")
    result = {
        "opacity": number(config.get("opacity", 1), f"{label}.opacity", minimum=0, maximum=1)
    }
    modulations = config.get("modulations", [])
    if not isinstance(modulations, list) or len(modulations) > 32:
        raise ValueError(f"{label}.modulations must be a list with <=32 entries")
    modulation_ids = []
    for index, row in enumerate(modulations):
        if not isinstance(row, dict) or row.get("target") not in COMPOSITE_MODULATION_TARGETS:
            raise ValueError(
                f"{label}.modulations[{index}] must target a numeric composite parameter"
            )
        modulation_id = row.get("id")
        if not isinstance(modulation_id, str) or not modulation_id:
            raise ValueError(f"{label}.modulations[{index}].id must be a non-empty string")
        modulation_ids.append(modulation_id)
        target = row["target"]
        if target.startswith("composite.matte.") and config.get("matte") is None:
            raise ValueError(f"{label}.modulations[{index}] requires a matte")
        if target.startswith("composite.chroma_key.") and config.get("chroma_key") is None:
            raise ValueError(f"{label}.modulations[{index}] requires chroma_key")
        interval = row.get("range", [-1, 1])
        if (
            not isinstance(interval, (list, tuple))
            or len(interval) != 2
            or any(type(value) not in (int, float) or not 0 <= value <= 1 for value in interval)
        ):
            raise ValueError(
                f"{label}.modulations[{index}].range must stay within zero and one"
            )
    if len(set(modulation_ids)) != len(modulation_ids):
        raise ValueError(f"{label}.modulations ids must be unique")
    if modulations:
        result["modulations"] = modulations
    underlay = config.get("underlay")
    if underlay is not None:
        fields(underlay, {"source"}, f"{label}.underlay")
        source = underlay.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError(f"{label}.underlay.source must name an isolated compositor element")
        result["underlay"] = {"source": source}
    chroma = config.get("chroma_key")
    if chroma is not None:
        fields(chroma, {"colour", "similarity", "smoothness", "despill"}, f"{label}.chroma_key")
        result["chroma_key"] = {
            "colour": _colour(chroma.get("colour"), f"{label}.chroma_key.colour"),
            "similarity": number(
                chroma.get("similarity", 0.1),
                f"{label}.chroma_key.similarity",
                minimum=0,
                maximum=1,
            ),
            "smoothness": number(
                chroma.get("smoothness", 0.08),
                f"{label}.chroma_key.smoothness",
                minimum=0,
                maximum=1,
            ),
            "despill": number(
                chroma.get("despill", 0.35),
                f"{label}.chroma_key.despill",
                minimum=0,
                maximum=1,
            ),
        }
    matte = config.get("matte")
    if matte is not None:
        fields(matte, {"source", "mode", "invert", "opacity"}, f"{label}.matte")
        source = matte.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError(f"{label}.matte.source must name an isolated compositor element")
        mode = matte.get("mode", "alpha")
        invert = matte.get("invert", False)
        if mode not in {"alpha", "luminance"} or type(invert) is not bool:
            raise ValueError(f"{label}.matte mode/invert must be alpha|luminance and boolean")
        result["matte"] = {
            "source": source,
            "mode": mode,
            "invert": invert,
            "opacity": number(
                matte.get("opacity", 1), f"{label}.matte.opacity", minimum=0, maximum=1
            ),
        }
    return result


def _smooth_key_alpha(distance, similarity, smoothness):
    lower = similarity * 255
    upper = min(255.0, (similarity + smoothness) * 255)
    if upper <= lower:
        return distance.point(lambda value: 0 if value <= lower else 255)

    def curve(value):
        x = max(0.0, min(1.0, (value - lower) / (upper - lower)))
        return round((x * x * (3 - 2 * x)) * 255)

    return distance.point(curve)


def apply_chroma_key(image, config):
    """Return an RGBA image keyed by maximum per-channel RGB distance."""
    source = image.convert("RGBA")
    rgb = source.convert("RGB")
    key = Image.new("RGB", source.size, config["colour"])
    red, green, blue = ImageChops.difference(rgb, key).split()
    distance = ImageChops.lighter(red, ImageChops.lighter(green, blue))
    key_alpha = _smooth_key_alpha(distance, config["similarity"], config["smoothness"])

    if config["despill"]:
        spill = ImageOps.invert(key_alpha).point(lambda value: round(value * config["despill"]))
        grey = ImageOps.grayscale(rgb).convert("RGB")
        rgb = Image.composite(grey, rgb, spill)

    result = rgb.convert("RGBA")
    result.putalpha(ImageChops.multiply(source.getchannel("A"), key_alpha))
    return result


def matte_alpha(source, mode, invert, opacity):
    if source.mode != "RGBA":
        raise ValueError("Visualiser matte source must be RGBA")
    mask = source.getchannel("A")
    if mode == "luminance":
        mask = ImageChops.multiply(mask, ImageOps.grayscale(source.convert("RGB")))
    if invert:
        mask = ImageOps.invert(mask)
    if opacity < 1:
        mask = mask.point(lambda value: round(value * opacity))
    return mask


def composite_stage(base, rendered, config, surfaces):
    """Key/matte a rendered stage and alpha-composite it over its stage input."""
    if config is None:
        return rendered
    if base.size != rendered.size:
        raise ValueError("Visualiser stage base/output sizes differ")
    keyed = rendered.convert("RGBA")
    if "chroma_key" in config:
        keyed = apply_chroma_key(keyed, config["chroma_key"])

    alpha = keyed.getchannel("A")
    matte = config.get("matte")
    if matte is not None:
        source = surfaces.get(matte["source"])
        if source is None:
            mask = Image.new("L", keyed.size, 0)
        else:
            if source.size != keyed.size:
                raise ValueError("Visualiser matte source must be full-frame RGBA")
            mask = matte_alpha(source, matte["mode"], matte["invert"], matte["opacity"])
        alpha = ImageChops.multiply(alpha, mask)
    if config["opacity"] < 1:
        alpha = alpha.point(lambda value: round(value * config["opacity"]))
    keyed.putalpha(alpha)
    result = base.convert("RGBA")
    underlay = config.get("underlay")
    if underlay is not None:
        source = surfaces.get(underlay["source"])
        if source is not None:
            if source.mode != "RGBA" or source.size != keyed.size:
                raise ValueError("Visualiser underlay source must be full-frame RGBA")
            result.alpha_composite(source)
    result.alpha_composite(keyed)
    return result
