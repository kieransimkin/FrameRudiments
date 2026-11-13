"""Inspect the declared inputs of an ISF 2 fragment without loading a shader."""

from hashlib import sha256
from pathlib import Path
import re

from .io import fields, integer, number, parse_json

MAX_SHADER_BYTES = 256 * 1024
MAX_INPUTS = 64
GLSL_FLOAT_MAX = 3.4028234663852886e38
INPUT_TYPES = {"image", "bool", "long", "float", "point2D", "color"}
FILTER_IMAGE = "inputImage"
TRANSITION_IMAGES = {"startImage", "endImage"}
IMAGE_NAMES = {FILTER_IMAGE, *TRANSITION_IMAGES}
HOST_NAMES = {
    "main", "TIME", "TIMEDELTA", "DATE", "FRAMEINDEX", "PASSINDEX", "RENDERSIZE",
    "isf_FragNormCoord", "IMG_PIXEL", "IMG_NORM_PIXEL", "IMG_THIS_PIXEL",
    "IMG_THIS_NORM_PIXEL", "IMG_SIZE",
}


def _typed_value(kind, value, label):
    if kind == "bool":
        if type(value) in (bool, int, float) and value in (0, 1):
            return bool(value)
        raise ValueError(f"{label} must be a boolean or 0 or 1")
    if kind == "long":
        return integer(value, label, minimum=-(2**31), maximum=2**31 - 1)
    if kind in {"point2D", "color"}:
        length = 2 if kind == "point2D" else 4
        if not isinstance(value, list) or len(value) != length:
            raise ValueError(f"{label} must have {length} components")
        minimum, maximum = (0, 1) if kind == "color" else (-GLSL_FLOAT_MAX, GLSL_FLOAT_MAX)
        return [_float_value(component, label, minimum, maximum) for component in value]
    return _float_value(value, label, -GLSL_FLOAT_MAX, GLSL_FLOAT_MAX)


def _float_value(value, label, minimum, maximum):
    try:
        return number(value, label, minimum=minimum, maximum=maximum)
    except OverflowError as exc:
        raise ValueError(f"{label} exceeds the finite shader number range") from exc


def _exceeds(left, right):
    if isinstance(left, list):
        return any(a > b for a, b in zip(left, right, strict=True))
    return left > right


def _check_bounds(value, control, label):
    if (("minimum" in control and _exceeds(control["minimum"], value))
            or ("maximum" in control and _exceeds(value, control["maximum"]))):
        raise ValueError(f"{label} is outside the declared range")


def _choices(entry, control, label):
    values, labels = entry.get("VALUES"), entry.get("LABELS")
    if (not isinstance(values, list) or not values or not isinstance(labels, list)
            or len(values) != len(labels) or any(not isinstance(text, str) for text in labels)):
        raise ValueError(f"{label} needs equally sized, nonempty VALUES and LABELS arrays")
    choices = []
    for value, text in zip(values, labels, strict=True):
        value = _typed_value("long", value, f"{label} VALUES")
        _check_bounds(value, control, f"{label} VALUES")
        choices.append({"value": value, "label": text})
    for key in ("default", "identity"):
        if key in control and control[key] not in values:
            raise ValueError(f"{label} {key.upper()} must be in VALUES")
    return choices


def _input(entry, index):
    label = f"ISF INPUTS[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{label} must be an object")
    kind = entry.get("TYPE")
    if not isinstance(kind, str) or kind not in INPUT_TYPES:
        raise ValueError(f"{label} TYPE must be one of {', '.join(sorted(INPUT_TYPES))}")
    allowed = {"NAME", "TYPE", "LABEL"}
    if kind != "image":
        allowed |= {"DEFAULT", "IDENTITY"}
    if kind not in {"image", "bool"}:
        allowed |= {"MIN", "MAX"}
    if kind == "long":
        allowed |= {"VALUES", "LABELS"}
    fields(entry, allowed, label)
    name = entry.get("NAME")
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name)
            or name.startswith("gl_") or "__" in name or name in HOST_NAMES
            or (name in IMAGE_NAMES and kind != "image")):
        raise ValueError(f"{label} NAME must be a non-reserved ASCII shader identifier of up to 64 characters")
    label = f"ISF input {name}"
    control = {"name": name, "type": kind}
    if "LABEL" in entry:
        if not isinstance(entry["LABEL"], str):
            raise ValueError(f"{label} LABEL must be a string")
        control["label"] = entry["LABEL"]
    for key, field in (("MIN", "minimum"), ("MAX", "maximum"), ("DEFAULT", "default"), ("IDENTITY", "identity")):
        if key in entry:
            control[field] = _typed_value(kind, entry[key], f"{label} {key}")
    if "minimum" in control and "maximum" in control and _exceeds(control["minimum"], control["maximum"]):
        raise ValueError(f"{label} MIN exceeds MAX")
    for key in ("default", "identity"):
        if key in control:
            _check_bounds(control[key], control, f"{label} {key.upper()}")
    if "VALUES" in entry or "LABELS" in entry:
        control["choices"] = _choices(entry, control, label)
    return control


def _kind(inputs):
    by_name = {control["name"]: control for control in inputs}
    if len(by_name) != len(inputs):
        raise ValueError("Duplicate ISF input NAME")
    images = {name for name, control in by_name.items() if control["type"] == "image"}
    if not images:
        return "generator"
    if images == {FILTER_IMAGE}:
        return "filter"
    if images != TRANSITION_IMAGES:
        raise ValueError("ISF image inputs must declare inputImage or the startImage/endImage pair")
    progress = by_name.get("progress", {})
    if progress.get("type") != "float" or progress.get("minimum", 0) != 0 or progress.get("maximum", 1) != 1:
        raise ValueError("ISF transition requires float progress with a 0 to 1 range")
    for key in ("default", "identity"):
        if key in progress:
            _check_bounds(progress[key], {"minimum": 0, "maximum": 1}, f"ISF transition progress {key}")
    return "transition"


def _metadata(source):
    text = source.decode("utf-8-sig").lstrip()
    end = text.find("*/", 2)
    if not text.startswith("/*") or end < 0 or not text[end + 2:].strip():
        raise ValueError("ISF needs a leading JSON block comment followed by a fragment body")
    try:
        data = parse_json(text[2:end])
    except RecursionError as exc:
        raise ValueError("ISF metadata is nested too deeply") from exc
    fields(data, {"ISFVSN", "VSN", "DESCRIPTION", "CREDIT", "CATEGORIES", "INPUTS", "IMPORTED", "PASSES"},
           "ISF metadata")
    if data.get("ISFVSN") not in ("2", "2.0"):
        raise ValueError("ISFVSN must explicitly declare '2' or '2.0'")
    if data.get("IMPORTED", {}) != {}:
        raise ValueError("ISF inspection subset does not accept imported resources")
    if data.get("PASSES", [{}]) != [{}]:
        raise ValueError("ISF inspection subset accepts a single pass with no target or pass options")
    entries = data.get("INPUTS", [])
    if not isinstance(entries, list) or len(entries) > MAX_INPUTS:
        raise ValueError(f"ISF INPUTS must be an array of up to {MAX_INPUTS} entries")
    inputs = [_input(entry, index) for index, entry in enumerate(entries)]
    result = {
        "isf_version": data["ISFVSN"],
        "kind": _kind(inputs),
        "passes": 1,
        "inputs": inputs,
    }
    for key, field in (("DESCRIPTION", "description"), ("CREDIT", "credit"), ("VSN", "shader_version")):
        if key in data:
            if not isinstance(data[key], str):
                raise ValueError(f"ISF {key} must be a string")
            result[field] = data[key]
    if "CATEGORIES" in data:
        categories = data["CATEGORIES"]
        if not isinstance(categories, list) or any(not isinstance(category, str) for category in categories):
            raise ValueError("ISF CATEGORIES must be an array of strings")
        result["categories"] = categories
    return result


def inspect_isf(path, *, expected_sha256=None):
    """Return typed metadata and provenance for one local, single-pass ISF 2 file."""
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256)
    ):
        raise ValueError("Expected ISF SHA-256 must be 64 hexadecimal characters")
    path = Path(path)
    if not path.is_file():
        raise ValueError("ISF input must be a local regular file")
    with path.open("rb") as stream:
        source = stream.read(MAX_SHADER_BYTES + 1)
    if len(source) > MAX_SHADER_BYTES:
        raise ValueError(f"ISF file exceeds {MAX_SHADER_BYTES} bytes")
    digest = sha256(source).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256.lower():
        raise ValueError("ISF input SHA-256 mismatch")
    return {
        "schema": "framerudiments.isf-metadata.v1",
        "scope": "metadata_only",
        "source": {"sha256": digest, "bytes": len(source)},
        **_metadata(source),
    }
