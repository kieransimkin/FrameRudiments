"""M14.1 isolated, opt-in, pinned CPU ONNX inference for image analysis.

Outputs are *model estimates* and never native geometry or measured audio.
No model is fetched; the ordinary compositor and encoder never import this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

REQUEST_SCHEMA = "framerudiments.vision-request.v1"
RESULT_SCHEMA = "framerudiments.vision-estimate.v1"
TASKS = frozenset({"segmentation", "relative_depth", "optical_flow"})
MAX_MODEL_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_PIXELS = 1024 * 1024
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
MAX_WORK_SECONDS = 120
_SHA = re.compile(r"[a-fA-F0-9]{64}\Z")
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9.:/-]{0,127}\Z")


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _object(value, keys, label, required=()):
    if not isinstance(value, dict) or set(value) - set(keys) or set(required) - set(value):
        raise ValueError(f"Invalid {label} fields")
    return value


def _local_pinned(base, value, *, extension, limit):
    _object(value, ("path", "sha256"), "pinned file", ("path", "sha256"))
    raw, expected = value["path"], value["sha256"]
    if not isinstance(raw, str) or not raw or not isinstance(expected, str) or not _SHA.fullmatch(expected):
        raise ValueError("File requires a relative path and complete SHA-256")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts or ":" in raw or "\\" in raw:
        raise ValueError("Pinned file must be a local relative path without traversal")
    path = (base / relative).resolve(strict=True)
    if not path.is_relative_to(base) or not path.is_file() or path.suffix.lower() != extension:
        raise ValueError("Pinned input is outside its manifest directory or has the wrong format")
    if not 0 < path.stat().st_size <= limit or _hash(path) != expected.lower():
        raise ValueError("Pinned input size or SHA-256 differs")
    return path


def _triple(value, label, *, strictly_positive=False):
    if (not isinstance(value, list) or len(value) != 3 or
            any(type(x) not in (int, float) or not math.isfinite(x) for x in value) or
            (strictly_positive and any(x <= 0 or x > 1e4 for x in value))):
        raise ValueError(f"Invalid {label} RGB normalization triple")
    return tuple(float(v) for v in value)


def _image_info(path, width, height):
    with Image.open(path) as image:
        if image.format != "PNG" or image.mode != "RGB" or image.size != (width, height):
            raise ValueError("Inputs must be exactly-sized, RGB8, checksum-pinned PNG files")
        image.verify()


@dataclass(frozen=True)
class VisionRequest:
    manifest: Path
    model: Path
    current: Path
    previous: Path | None
    task: str
    input_names: tuple[str, ...]
    output_name: str
    width: int
    height: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    activation: str
    timeout_seconds: int
    input_hashes: dict
    manifest_sha256: str

    @property
    def channels(self):
        return 2 if self.task == "optical_flow" else 1

    def document(self):
        return {"schema": REQUEST_SCHEMA, "task": self.task, "shape": [self.height, self.width],
                "input_names": list(self.input_names), "output_name": self.output_name,
                "channel_count": self.channels, "model_sha256": self.input_hashes["model"],
                "inputs_sha256": dict(self.input_hashes), "manifest_sha256": self.manifest_sha256,
                "provider": "CPUExecutionProvider", "worker_timeout_seconds": self.timeout_seconds,
                "estimated_payload_bytes_limit": MAX_PAYLOAD_BYTES,
                "units": ("probability" if self.task == "segmentation" else
                          "unitless_model_relative_depth" if self.task == "relative_depth" else
                          "input_pixel_displacement_previous_to_current"),
                "model_estimated": True, "native_geometry": False,
                "spatial_resampling": "none"}


def inspect_request(manifest):
    path = Path(manifest).resolve(strict=True)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_MANIFEST_BYTES:
        raise ValueError("Inference manifest must be a small local JSON file")
    base = path.parent
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    _object(data, ("schema", "task", "model", "current", "previous", "input_names",
                   "output_name", "width", "height", "preprocess", "activation", "timeout_seconds"),
            "M14 request", ("schema", "task", "model", "current", "input_names", "output_name",
                             "width", "height"))
    if data["schema"] != REQUEST_SCHEMA or data["task"] not in TASKS:
        raise ValueError("Unsupported M14 request schema or task")
    width, height = data["width"], data["height"]
    if (type(width) is not int or type(height) is not int or
            not 16 <= width <= 1024 or not 16 <= height <= 1024 or
            width * height > MAX_OUTPUT_PIXELS):
        raise ValueError("M14 image dimensions must be 16..1024 with <=1 MiPixel")
    task = data["task"]
    names = data["input_names"]
    count = len(names) if isinstance(names, list) else 0
    if (count not in ((1, 2) if task == "optical_flow" else (1,)) or
            any(not isinstance(s, str) or not _NAME.fullmatch(s) for s in names) or
            len(set(names)) != count):
        raise ValueError("Invalid ONNX input names or unsupported pair-input topology")
    output = data["output_name"]
    if not isinstance(output, str) or not _NAME.fullmatch(output):
        raise ValueError("Invalid ONNX output tensor name")
    if (task == "optical_flow") != ("previous" in data):
        raise ValueError("Optical flow requires previous RGB input; other tasks forbid it")
    prep = _object(data.get("preprocess", {}), ("mean", "std"), "preprocess")
    mean = _triple(prep.get("mean", [0, 0, 0]), "mean")
    std = _triple(prep.get("std", [1, 1, 1]), "std", strictly_positive=True)
    activation = data.get("activation", "identity")
    if activation not in (("identity", "sigmoid") if task == "segmentation" else ("identity",)):
        raise ValueError("Only segmentation supports optional sigmoid activation")
    timeout = data.get("timeout_seconds", 60)
    if type(timeout) is not int or not 5 <= timeout <= MAX_WORK_SECONDS:
        raise ValueError("Inference timeout must be an integer in [5,120]")
    model = _local_pinned(base, data["model"], extension=".onnx", limit=MAX_MODEL_BYTES)
    current = _local_pinned(base, data["current"], extension=".png", limit=MAX_SOURCE_BYTES)
    previous = (_local_pinned(base, data["previous"], extension=".png", limit=MAX_SOURCE_BYTES)
                if task == "optical_flow" else None)
    _image_info(current, width, height)
    if previous is not None:
        _image_info(previous, width, height)
    # Admission reserves raw input, output, image decode and preview buffers. The
    # native runtime's internal arena is NOT claimed to be hard-memory-limited.
    buffers = (6 + (4 if previous else 0) + (2 if task == "optical_flow" else 1)) * width * height * 4
    if buffers > MAX_PAYLOAD_BYTES:
        raise ValueError("M14 estimated decoded payload exceeds 256 MiB")
    hashes = {"model": data["model"]["sha256"].lower(),
              "current": data["current"]["sha256"].lower()}
    if previous is not None:
        hashes["previous"] = data["previous"]["sha256"].lower()
    return VisionRequest(path, model, current, previous, task, tuple(names), output,
                         width, height, mean, std, activation, timeout, hashes, _hash(path))


def _tensor(image_path, spec, np):
    with Image.open(image_path) as image:
        image.load()
        image_data = np.asarray(image, dtype=np.float32) / np.float32(255.0)
    normalized = (image_data - np.asarray(spec.mean, dtype=np.float32)) / np.asarray(spec.std, dtype=np.float32)
    return np.ascontiguousarray(normalized.transpose(2, 0, 1)[None, ...], dtype=np.float32)


def _metadata_matches(meta, name, channels, height, width):
    return meta.name == name and meta.type == "tensor(float)" and list(meta.shape) == [1, channels, height, width]


def _postprocess(value, spec, np):
    arr = np.asarray(value)
    expected = (1, spec.channels, spec.height, spec.width)
    if arr.dtype != np.dtype("float32") or arr.shape != expected or not np.isfinite(arr).all():
        raise ValueError("ONNX output must be finite float32 NCHW with declared task channels")
    arr = np.ascontiguousarray(arr[0].transpose(1, 2, 0), dtype=np.float32)
    if spec.task == "segmentation":
        if spec.activation == "sigmoid":
            arr = (1 / (1 + np.exp(-np.clip(arr, -80, 80)))).astype(np.float32)
        if np.any((arr < 0) | (arr > 1)):
            raise ValueError("Segmentation probability outside [0,1]")
    elif spec.task == "relative_depth":
        if np.any((arr < 0) | (arr > 1e6)):
            raise ValueError("Relative depth must be finite, nonnegative and bounded")
    elif np.any(np.abs(arr) > 4096):
        raise ValueError("Optical flow exceeds the 4096 input-pixel displacement bound")
    return arr.copy()


def _execute_model(spec, *, runtime=None):
    import numpy as np

    if runtime is None:
        try:
            import onnxruntime as runtime
        except ImportError as exc:
            raise RuntimeError("Install the optional vision extra (onnxruntime CPU)") from exc
    so = runtime.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    so.enable_mem_pattern = False
    so.enable_cpu_mem_arena = False
    # Loading from bounded bytes rules out arbitrary auxiliary model file access.
    session = runtime.InferenceSession(spec.model.read_bytes(), sess_options=so,
                                       providers=["CPUExecutionProvider"])
    session.disable_fallback()
    if session.get_providers() != ["CPUExecutionProvider"]:
        raise ValueError("Strict CPU provider only; GPU or other fallbacks are not admitted in M14.1")
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != len(spec.input_names) or len(outputs) > 8:
        raise ValueError("ONNX tensor arity exceeds the declared request")
    channels = (6 if spec.previous is not None and len(inputs) == 1 else 3)
    if any(not _metadata_matches(meta, name, channels, spec.height, spec.width)
           for meta, name in zip(inputs, spec.input_names)):
        raise ValueError("ONNX input names, types or static dimensions do not match the pinned request")
    match = [meta for meta in outputs if meta.name == spec.output_name]
    if len(match) != 1 or not _metadata_matches(match[0], spec.output_name,
                                                spec.channels, spec.height, spec.width):
        raise ValueError("ONNX output tensor has unsupported name, type or shape")
    current = _tensor(spec.current, spec, np)
    if spec.previous is not None:
        previous = _tensor(spec.previous, spec, np)
        feeds = ({spec.input_names[0]: np.ascontiguousarray(np.concatenate((previous, current), axis=1))}
                 if len(spec.input_names) == 1 else
                 {spec.input_names[0]: previous, spec.input_names[1]: current})
    else:
        feeds = {spec.input_names[0]: current}
    result = session.run([spec.output_name], feeds)
    if len(result) != 1:
        raise ValueError("ONNX returned an unexpected number of tensors")
    return _postprocess(result[0], spec, np), str(getattr(runtime, "__version__", "unknown"))


def _preview(arr, spec):
    import numpy as np

    if spec.task == "segmentation":
        image = np.rint(np.clip(arr[:, :, 0], 0, 1) * 255).astype(np.uint8)
        return Image.fromarray(image, "L"), {"mapping": "probability_0_1_to_0_255"}
    if spec.task == "relative_depth":
        high = float(np.max(arr))
        image = np.rint((arr[:, :, 0] / high if high else arr[:, :, 0]) * 255).astype(np.uint8)
        return Image.fromarray(image, "L"), {"mapping": "relative_max_to_255", "max_relative_depth": high}
    magnitude = np.sqrt(arr[:, :, 0] ** 2 + arr[:, :, 1] ** 2)
    max_length = float(np.max(magnitude))
    factor = np.clip(magnitude / max_length, 0, 1) if max_length else np.zeros_like(magnitude)
    # Signed vector components in RGB: zero motion is dark, never a metric claim.
    rgba = np.stack((np.clip((arr[:, :, 0] / max_length + 1) / 2, 0, 1) if max_length else factor,
                     np.clip((arr[:, :, 1] / max_length + 1) / 2, 0, 1) if max_length else factor,
                     factor), axis=-1)
    return Image.fromarray(np.rint(rgba * 255).astype(np.uint8), "RGB"), {
        "mapping": "normalized_vector_components_R_G_and_relative_magnitude_B",
        "max_vector_length_input_pixels": max_length}


def _worker(spec_path, stage):
    import numpy as np

    spec = inspect_request(spec_path)
    arr, ort_version = _execute_model(spec)
    if _hash(spec.model) != spec.input_hashes["model"] or _hash(spec.current) != spec.input_hashes["current"]:
        raise ValueError("Inference input changed while processing")
    if spec.previous is not None and _hash(spec.previous) != spec.input_hashes["previous"]:
        raise ValueError("Previous frame changed during inference")
    np.save(stage / "estimate.npy", arr, allow_pickle=False)
    preview, preview_info = _preview(arr, spec)
    preview.save(stage / "preview.png")
    report = dict(spec.document(), schema=RESULT_SCHEMA,
                  model_runtime="onnxruntime", runtime_version=ort_version,
                  tensor_shape=list(arr.shape), tensor_dtype="float32",
                  estimate_sha256=_hash(stage / "estimate.npy"),
                  preview_sha256=_hash(stage / "preview.png"), preview_mapping=preview_info,
                  value_range=[float(np.min(arr)), float(np.max(arr))])
    (stage / "receipt.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def infer(manifest, output):
    """Time-limited subprocess; new-directory publication after independent checks."""
    import numpy as np

    spec = inspect_request(manifest)
    target = Path(output).absolute()
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("Inference output must be a new directory beneath an existing parent")
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        try:
            child = subprocess.run([sys.executable, "-m", "framerudiments.vision_m14", "--worker",
                                    str(spec.manifest), str(staging)], check=False,
                                   capture_output=True, text=True, timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("M14 inference exceeded the declared worker deadline") from exc
        if child.returncode:
            raise RuntimeError("ONNX inference worker failed: " + child.stderr[-1800:])
        expected = {"estimate.npy", "preview.png", "receipt.json"}
        if {p.name for p in staging.iterdir()} != expected:
            raise ValueError("Incomplete or unexpected M14 worker output")
        data = json.loads((staging / "receipt.json").read_text(encoding="utf-8"), object_pairs_hook=_unique)
        try:
            image = np.load(staging / "estimate.npy", allow_pickle=False)
        except (OSError, ValueError) as exc:
            raise ValueError("M14 worker receipt or prediction verification failed") from exc
        if (data.get("schema") != RESULT_SCHEMA or data.get("task") != spec.task or
                data.get("manifest_sha256") != spec.manifest_sha256 or
                data.get("input_names") != list(spec.input_names) or
                data.get("inputs_sha256") != spec.input_hashes or
                data.get("estimate_sha256") != _hash(staging / "estimate.npy") or
                data.get("preview_sha256") != _hash(staging / "preview.png") or
                image.dtype != np.dtype("float32") or
                image.shape != (spec.height, spec.width, spec.channels) or
                not np.isfinite(image).all()):
            raise ValueError("M14 worker receipt or prediction verification failed")
        _postprocess(image.transpose(2, 0, 1)[None, ...], spec, np)
        with Image.open(staging / "preview.png") as preview:
            if preview.size != (spec.width, spec.height):
                raise ValueError("M14 preview dimensions mismatch")
            preview.verify()
        if _hash(spec.manifest) != spec.manifest_sha256 or any(
                _hash(getattr(spec, key)) != value for key, value in spec.input_hashes.items()):
            raise ValueError("M14 pinned input changed during inference")
        if target.exists() or target.is_symlink():
            raise ValueError("Inference destination appeared while worker ran")
        # A directory rename is atomic on supported local filesystems, but not
        # a no-replace primitive; concurrent writers must use separate outputs.
        staging.rename(target)
        return data
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _entry():
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise ValueError("This module is a subprocess worker; use framerudiments-vision")
    _worker(sys.argv[2], Path(sys.argv[3]))


if __name__ == "__main__":
    _entry()
