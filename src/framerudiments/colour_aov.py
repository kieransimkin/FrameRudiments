"""M12.1: opt-in scene-linear colour and explicitly authored OpenEXR AOVs.

The canonical compositor still returns RGB8. This module does not synthesize
geometry depth, normals, velocity or claim an HDR render from SDR pixels.
External OCIO configurations and EXR files must come from trusted local inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

SCHEMA = "framerudiments.colour-aov.v1"
MANIFEST_SCHEMA = "framerudiments.colour-aov-input.v1"
MAX_RGB_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_SIDE = 8192
_SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")
_CHANNELS = {"RGB": 3, "A": 1, "Z": 1, "N": 3, "motion": 2}
_EXR_CHANNELS = ("R", "G", "B", "A", "Z", "N.X", "N.Y", "N.Z", "motion.X", "motion.Y")


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("M12 colour/AOV operations require numpy; install '.[colour_aov]'") from exc
    return np


def _openexr():
    try:
        import OpenEXR
    except ImportError as exc:
        raise RuntimeError("EXR operations require OpenEXR >=3.4; install '.[colour_aov]'") from exc
    if not hasattr(OpenEXR, "File"):
        raise RuntimeError("EXR operations require the modern OpenEXR.File API")
    return OpenEXR


def _ocio():
    try:
        import PyOpenColorIO as ocio
    except ImportError as exc:
        raise RuntimeError("Colour transforms require OpenColorIO; install '.[colour_aov]'") from exc
    return ocio


def _digest(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _pinned(path, expected, *, suffix=None):
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        raise ValueError("Input needs a 64-hex SHA-256")
    path = Path(path).resolve(strict=True)
    if (not path.is_file() or path.stat().st_size > MAX_FILE_BYTES
            or suffix is not None and path.suffix.lower() != suffix):
        raise ValueError("Input is not a bounded file of the requested format")
    if _digest(path).lower() != expected.lower():
        raise ValueError("Pinned input SHA-256 does not match")
    return path


def _json_file(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("AOV manifest must be a bounded JSON file")
    return path, json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def _string(value, label):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be a non-empty printable name")
    return value


def _float_array(value, label, dims, size=None):
    np = _numpy()
    if not isinstance(value, np.ndarray) or value.dtype != np.dtype("float32"):
        raise ValueError(f"{label} must be a float32 NumPy array")
    if value.ndim != len(dims) or any(d is not None and n != d for n, d in zip(value.shape, dims)):
        raise ValueError(f"{label} has the wrong channel dimensions")
    if value.ndim > 1 and (value.shape[0] < 1 or value.shape[1] < 1 or
                            value.shape[0] > MAX_SIDE or value.shape[1] > MAX_SIDE):
        raise ValueError("AOV image exceeds dimensions")
    if size is not None and value.shape[:2] != size:
        raise ValueError("AOV channels must have identical extent")
    if not np.isfinite(value).all():
        raise ValueError(f"{label} contains non-finite values")
    return value


@dataclass(frozen=True)
class AOVFrame:
    """Detached float32 buffers. Colour is unassociated *scene-linear* RGB."""

    rgb: object
    colour_space: str
    alpha: object = None
    depth: object = None
    normals: object = None
    velocity: object = None

    def __post_init__(self):
        np = _numpy()
        _string(self.colour_space, "Colour space")
        arrays = {
            "rgb": (self.rgb, (None, None, 3)),
            "alpha": (self.alpha, (None, None)),
            "depth": (self.depth, (None, None)),
            "normals": (self.normals, (None, None, 3)),
            "velocity": (self.velocity, (None, None, 2)),
        }
        _float_array(self.rgb, "RGB", (None, None, 3))
        height, width = self.rgb.shape[:2]
        total = sum(x.nbytes for x, _ in arrays.values() if x is not None)
        if total > MAX_RGB_BYTES:
            raise ValueError("AOV channels exceed 512 MiB native float budget")
        for name, (array, dimensions) in arrays.items():
            if array is None:
                continue
            _float_array(array, name, dimensions, (height, width))
            if name == "alpha" and (np.any(array < 0) or np.any(array > 1)):
                raise ValueError("Unassociated alpha must be within 0..1")
            if name == "depth" and np.any(array < 0):
                raise ValueError("Depth must be nonnegative camera-space units")
            # Copy once, detach from mutable producer arrays, and expose read-only views.
            owned = np.array(array, dtype=np.float32, order="C", copy=True)
            owned.setflags(write=False)
            object.__setattr__(self, name, owned)

    @property
    def size(self):
        return (self.rgb.shape[1], self.rgb.shape[0])

    def channels(self):
        result = {"RGB": self.rgb}
        for key, name in (("A", "alpha"), ("Z", "depth"), ("N", "normals"),
                          ("motion", "velocity")):
            value = getattr(self, name)
            if value is not None:
                result[key] = value
        return result

    def evidence(self):
        return {"schema": SCHEMA, "width": self.size[0], "height": self.size[1],
                "colour_space": self.colour_space, "association": "straight",
                "depth_units": "camera_space_units_if_supplied",
                "velocity_units": "pixels_per_encoded_frame_if_supplied",
                "channels": {name: hashlib.sha256(value.tobytes(order="C")).hexdigest()
                             for name, value in self.channels().items()}}


def srgb8_to_scene_linear(rgb):
    """Exact sRGB transfer decoding; only a bounded display conversion, not HDR recovery."""
    np = _numpy()
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError("Expected uint8 RGB display pixels")
    if rgb.shape[0] < 1 or rgb.shape[1] < 1 or rgb.shape[0] > MAX_SIDE or rgb.shape[1] > MAX_SIDE:
        raise ValueError("Display image dimensions out of bounds")
    x = rgb.astype("float32") / 255.0
    result = np.where(x <= .04045, x / 12.92, ((x + .055) / 1.055) ** 2.4)
    return np.asarray(result, dtype=np.float32)


def scene_linear_to_srgb8(rgb, *, exposure=0.0, tone_map="reinhard"):
    """Explicit *display-only* tone map and quantisation; source data is unchanged."""
    np = _numpy()
    _float_array(rgb, "RGB", (None, None, 3))
    if (not isinstance(exposure, (int, float)) or isinstance(exposure, bool)
            or not np.isfinite(exposure) or not -16 <= exposure <= 16):
        raise ValueError("Exposure must be a finite value in [-16,16] stops")
    if tone_map not in ("reinhard", "clip"):
        raise ValueError("Tone map must be reinhard or clip")
    x = np.maximum(rgb * np.float32(2 ** exposure), 0)
    x = x / (1 + x) if tone_map == "reinhard" else np.clip(x, 0, 1)
    display = np.where(x <= .0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - .055)
    return np.uint8(np.round(np.clip(display, 0, 1) * 255))


def transform_ocio(frame, *, config, config_sha256, source_space, target_space,
                   pinned_resources=()):
    """Transform authored RGB via a trusted, pinned OCIO config; preserve AOVs.

    External file transforms must be separately pinned by the caller. The config
    is *trusted* because OCIO may reference LUTs and environment expansions.
    """
    np = _numpy()
    if not isinstance(frame, AOVFrame):
        raise ValueError("OCIO source must be an AOVFrame")
    if source_space != frame.colour_space:
        raise ValueError("OCIO source colour space differs from frame declaration")
    _string(source_space, "Source colour space")
    _string(target_space, "Target colour space")
    config = _pinned(config, config_sha256, suffix=".ocio")
    for resource in pinned_resources:
        if not isinstance(resource, (list, tuple)) or len(resource) != 2:
            raise ValueError("OCIO resource pins must be (path, sha256) pairs")
        _pinned(resource[0], resource[1])
    ocio = _ocio()
    cfg = ocio.Config.CreateFromFile(str(config))
    if cfg.getColorSpace(source_space) is None or cfg.getColorSpace(target_space) is None:
        raise ValueError("Colour space not declared in the pinned OCIO config")
    processor = cfg.getProcessor(source_space, target_space).getDefaultCPUProcessor()
    pixels = np.array(frame.rgb, dtype=np.float32, order="C", copy=True)
    processor.applyRGB(pixels)
    if not np.isfinite(pixels).all():
        raise ValueError("OCIO transform returned non-finite HDR data")
    # Detect modifications to config or pinned LUTs during processor evaluation.
    _pinned(config, config_sha256, suffix=".ocio")
    for path, digest in pinned_resources:
        _pinned(path, digest)
    return AOVFrame(pixels, target_space, frame.alpha, frame.depth, frame.normals, frame.velocity)


def frame_from_manifest(manifest):
    """Read a trusted, SHA-pinned .npy channel manifest; never deserialize pickle."""
    np = _numpy()
    location, data = _json_file(manifest)
    if not isinstance(data, dict) or set(data) != {"schema", "colour_space", "channels"}:
        raise ValueError("Manifest requires schema, colour_space and channels")
    if data["schema"] != MANIFEST_SCHEMA or not isinstance(data["channels"], dict):
        raise ValueError("Expected M12.1 channel manifest")
    channels = data["channels"]
    if "RGB" not in channels or set(channels) - set(_CHANNELS):
        raise ValueError("Manifest needs RGB and only allowed AOV channels")
    size = None
    values = {}
    files_to_recheck = []
    original_manifest_sha = _digest(location)
    root = location.parent.resolve()
    for name, ref in channels.items():
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256"} or not isinstance(ref["path"], str):
            raise ValueError("AOV channel reference requires path and SHA-256")
        item = root / ref["path"]
        file = _pinned(item, ref["sha256"], suffix=".npy")
        if not file.is_relative_to(root):
            raise ValueError("AOV channel must remain inside the manifest directory")
        # Mmap mode avoids eagerly accepting compressed or pickle data.
        data_array = np.load(file, mmap_mode="r", allow_pickle=False)
        dim = _CHANNELS[name]
        expected = (None, None, dim) if dim > 1 else (None, None)
        _float_array(data_array, name, expected, size)
        size = data_array.shape[:2]
        values[name] = np.asarray(data_array)
        files_to_recheck.append((file, ref["sha256"]))
    frame = AOVFrame(values["RGB"], data["colour_space"], values.get("A"),
                     values.get("Z"), values.get("N"), values.get("motion"))
    # Verify after detaching data, so manifest and arrays share one stable input identity.
    for file, digest in files_to_recheck:
        _pinned(file, digest, suffix=".npy")
    if _digest(location) != original_manifest_sha:
        raise ValueError("AOV manifest changed during loading")
    return frame, {"manifest_sha256": original_manifest_sha,
                   "inputs": {name: ref["sha256"].lower() for name, ref in channels.items()}}


def _exr_maps(frame):
    rgba = {"R": frame.rgb[..., 0], "G": frame.rgb[..., 1], "B": frame.rgb[..., 2]}
    if frame.alpha is not None:
        rgba["A"] = frame.alpha
    if frame.depth is not None:
        rgba["Z"] = frame.depth
    if frame.normals is not None:
        for name, n in (("N.X", 0), ("N.Y", 1), ("N.Z", 2)):
            rgba[name] = frame.normals[..., n]
    if frame.velocity is not None:
        for name, n in (("motion.X", 0), ("motion.Y", 1)):
            rgba[name] = frame.velocity[..., n]
    np = _numpy()
    return {name: np.ascontiguousarray(value) for name, value in rgba.items()}


def read_exr(path, *, sha256):
    """Read only bounded, single-part, flat scanline EXR with our explicit AOVs."""
    path = _pinned(path, sha256, suffix=".exr")
    exr = _openexr()
    with exr.File(str(path), separate_channels=True) as image:
        if len(image.parts) != 1 or image.header()["type"] != exr.scanlineimage:
            raise ValueError("Only single-part flat scanline EXR is supported")
        channels = image.channels()
        names = set(channels)
        if not {"R", "G", "B"} <= names or names - set(_EXR_CHANNELS):
            raise ValueError("Unexpected EXR channels")
        for group in (("N.X", "N.Y", "N.Z"), ("motion.X", "motion.Y")):
            if names.intersection(group) and not set(group) <= names:
                raise ValueError("Incomplete EXR AOV vector")
        np = _numpy()
        positions = image.header()["dataWindow"]
        if tuple(positions[0]) != (0, 0):
            raise ValueError("EXR dataWindow must start at zero")
        layers = {key: np.asarray(channels[key].pixels) for key in names}
        def combined(keys):
            return np.stack([layers[k] for k in keys], axis=-1)
        def one(key):
            return layers[key] if key in layers else None
        colour_space = image.header().get("framerudimentsColorSpace")
        if not isinstance(colour_space, str):
            raise ValueError("EXR lacks explicit FrameRudiments colour space")
        if image.header().get("framerudimentsSchema") != SCHEMA:
            raise ValueError("Unrecognized EXR AOV schema")
        frame = AOVFrame(combined(("R", "G", "B")), colour_space, one("A"), one("Z"),
                         combined(("N.X", "N.Y", "N.Z")) if "N.X" in names else None,
                         combined(("motion.X", "motion.Y")) if "motion.X" in names else None)
    _pinned(path, sha256, suffix=".exr")
    return frame


def write_exr(output, frame, *, source_evidence=None):
    """Publish lossless float32 EXR + receipt only after independent pixel readback."""
    if not isinstance(frame, AOVFrame):
        raise ValueError("EXR output needs an AOVFrame")
    target = Path(output).absolute()
    if target.suffix.lower() != ".exr" or not target.parent.is_dir():
        raise ValueError("EXR output needs an existing parent directory")
    receipt = Path(str(target) + ".receipt.json")
    if any(path.exists() or path.is_symlink() for path in (target, receipt)):
        raise ValueError("EXR or receipt exists; refusing to overwrite")
    exr = _openexr()
    fd, pending = tempfile.mkstemp(prefix=".fr-aov-", suffix=".exr", dir=target.parent)
    os.close(fd)
    fd, pending_receipt = tempfile.mkstemp(prefix=".fr-aov-", suffix=".json", dir=target.parent)
    os.close(fd)
    published = False
    try:
        header = {"compression": exr.ZIP_COMPRESSION, "type": exr.scanlineimage,
                  "framerudimentsSchema": SCHEMA,
                  "framerudimentsColorSpace": frame.colour_space,
                  "framerudimentsAssociation": "straight"}
        with exr.File(header, _exr_maps(frame)) as image:
            image.write(pending)
        digest = _digest(pending)
        read_back = read_exr(pending, sha256=digest)
        np = _numpy()
        if (read_back.colour_space != frame.colour_space
                or set(read_back.channels()) != set(frame.channels())
                or any(not np.array_equal(value, read_back.channels()[name])
                       for name, value in frame.channels().items())):
            raise ValueError("EXR independent lossless AOV roundtrip failed")
        provenance = frame.evidence()
        provenance.update({"exr_sha256": digest, "readback": "exact_float32",
                           "compression": "ZIP_lossless", "source": source_evidence or {}})
        Path(pending_receipt).write_text(json.dumps(provenance, sort_keys=True, indent=2,
                                                   allow_nan=False) + "\n", encoding="utf-8")
        if any(path.exists() or path.is_symlink() for path in (target, receipt)):
            raise ValueError("EXR destination appeared during encoding")
        os.link(pending, target)
        published = True
        os.link(pending_receipt, receipt)
        return provenance
    except BaseException:
        if published:
            target.unlink(missing_ok=True)
        raise
    finally:
        Path(pending).unlink(missing_ok=True)
        Path(pending_receipt).unlink(missing_ok=True)


def inspect_exr(path, *, sha256):
    frame = read_exr(path, sha256=sha256)
    return {"file_sha256": sha256.lower(), **frame.evidence()}
