"""M14.2: opt-in pinned ONNX profiles and frame-addressed effect-graph surfaces.

Every graph source is derived from a M14.1 request (never from an untrusted URL).
Content-addressed caches store verified native estimates; RGB8 projections are
explicit display/matte *encodings*, not native depth or motion AOVs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import shutil
import secrets

from PIL import Image, ImageChops

from .vision_m14 import (
    RESULT_SCHEMA, TASKS, _hash, _postprocess,
    _unique, infer, inspect_request,
)

SCHEMA = "framerudiments.vision-graph.v1"
PROFILE_SCHEMA = "framerudiments.vision-profile.v1"
CACHE_SCHEMA = "framerudiments.vision-graph-cache.v1"
MAX_PROFILES = 16
MAX_SOURCES = 16
MAX_FRAME_REQUESTS = 256
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_CACHE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_HEX = re.compile(r"[a-fA-F0-9]{64}\Z")


def _object(value, allowed, label, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError(f"Invalid M14.2 {label}")
    return value


def _name(name, label):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(f"Invalid M14.2 {label}")
    return name


def _path_beneath(base, raw, *, file=False):
    if (not isinstance(raw, str) or not raw or raw.startswith(("/", "\\"))
            or ":" in raw or "\\" in raw or ".." in Path(raw).parts):
        raise ValueError("M14.2 requires a relative local path without traversal")
    root = Path(base).resolve()
    candidate = (root / raw).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("M14.2 path leaves the project root")
    if file and not candidate.is_file():
        raise ValueError("M14.2 pinned manifest does not exist")
    return candidate


def _hex(value, label):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError(f"Invalid {label} SHA-256")
    return value.lower()


def _fractional_range(value, *, task):
    default = [0, 1] if task == "segmentation" else [0, 10] if task == "relative_depth" else [-32, 32]
    values = value if value is not None else default
    if (not isinstance(values, list) or len(values) != 2 or
            any(type(x) not in (float, int) or not math.isfinite(x) for x in values) or
            not -4096 <= values[0] < values[1] <= 4096):
        raise ValueError("Invalid M14.2 display range")
    return (float(values[0]), float(values[1]))


def _triple(value, label, *, positive=False):
    if (not isinstance(value, list) or len(value) != 3 or
            any(type(x) not in (int, float) or not math.isfinite(x) for x in value) or
            (positive and any(x <= 0 or x > 10000 for x in value))):
        raise ValueError(f"Invalid M14.2 {label}")
    return tuple(float(x) for x in value)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha_document(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _onnx_runtime_version():
    # Importing the native ONNX runtime is neither necessary nor permitted on
    # the compositor's legacy path. Its version is still part of cache identity.
    try:
        return importlib.metadata.version("onnxruntime")
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


@dataclass(frozen=True)
class VisionProfile:
    name: str
    task: str
    model_sha256: str
    width: int
    height: int
    input_names: tuple[str, ...]
    output_name: str
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    activation: str
    display_range: tuple[float, float]
    digest: str

    @classmethod
    def parse(cls, doc):
        _object(doc, ("schema", "id", "task", "model_sha256", "width", "height",
                      "input_names", "output_name", "preprocess", "activation", "display_range"),
                "profile", ("schema", "id", "task", "model_sha256", "width", "height",
                             "input_names", "output_name"))
        if doc["schema"] != PROFILE_SCHEMA or doc["task"] not in TASKS:
            raise ValueError("Unknown M14.2 profile schema or task")
        task = doc["task"]
        w, h = doc["width"], doc["height"]
        if (type(w) is not int or type(h) is not int or
                not 16 <= w <= 1024 or not 16 <= h <= 1024 or w * h > 1024 * 1024):
            raise ValueError("Invalid M14.2 inference profile dimensions")
        inputs = doc["input_names"]
        if (not isinstance(inputs, list) or len(inputs) not in ((1, 2) if task == "optical_flow" else (1,)) or
                any(not isinstance(n, str) or not n or len(n) > 128 for n in inputs) or
                len(set(inputs)) != len(inputs)):
            raise ValueError("Invalid M14.2 profile input tensor names")
        output = doc["output_name"]
        if not isinstance(output, str) or not output or len(output) > 128:
            raise ValueError("Invalid M14.2 profile output tensor name")
        prep = _object(doc.get("preprocess", {}), ("mean", "std"), "preprocess")
        mean = _triple(prep.get("mean", [0, 0, 0]), "mean")
        std = _triple(prep.get("std", [1, 1, 1]), "std", positive=True)
        activation = doc.get("activation", "identity")
        if activation not in (("identity", "sigmoid") if task == "segmentation" else ("identity",)):
            raise ValueError("Invalid M14.2 profile activation")
        return cls(_name(doc["id"], "profile ID"), task, _hex(doc["model_sha256"], "model"),
                   w, h, tuple(inputs), output, mean, std, activation,
                   _fractional_range(doc.get("display_range"), task=task), _sha_document(doc))

    def matches(self, request):
        return (request.task == self.task and request.input_hashes["model"] == self.model_sha256
                and (request.width, request.height) == (self.width, self.height)
                and request.input_names == self.input_names and request.output_name == self.output_name
                and request.mean == self.mean and request.std == self.std
                and request.activation == self.activation)


class VisionGraphSources:
    """Validated model-estimate input surfaces, independent of graph node types.

    M14.1 worker output is cached at an immutable content key, then verified on
    every use. Graph and source edits are global S5/S6 cache dependencies.
    """

    def __init__(self, project, config):
        _object(config, ("schema", "cache_dir", "profiles", "sources", "max_cache_bytes",
                         "element_masks"), "graph sources",
                ("schema", "cache_dir", "profiles", "sources"))
        if config["schema"] != SCHEMA:
            raise ValueError("Unsupported M14.2 vision source schema")
        self.project = project
        self.root = Path(project.base).resolve()
        self.cache = _path_beneath(self.root, config["cache_dir"])
        if self.cache == self.root:
            raise ValueError("M14.2 cache directory cannot be the project root")
        self.max_cache_bytes = config.get("max_cache_bytes", 512 * 1024 * 1024)
        if (type(self.max_cache_bytes) is not int or
                not 64 * 1024 * 1024 <= self.max_cache_bytes <= MAX_CACHE_TOTAL_BYTES):
            raise ValueError("M14.2 cache quota must be 64 MiB..2 GiB")
        profiles = config["profiles"]
        entries = config["sources"]
        if (not isinstance(profiles, list) or not 1 <= len(profiles) <= MAX_PROFILES or
                not isinstance(entries, list) or not 1 <= len(entries) <= MAX_SOURCES):
            raise ValueError("M14.2 needs 1..16 profiles and graph sources")
        self.profiles = {p.name: p for p in (VisionProfile.parse(row) for row in profiles)}
        if len(self.profiles) != len(profiles):
            raise ValueError("Duplicate M14.2 profile ID")
        self.sources = {}
        # M14.4 policies are opt-in and kept separate from the historical
        # (profile, missing, resample, frame-map) tuple to preserve M14.2 users.
        self.provider_pins = {}
        self.preview_ages = {}
        self.evidence = {"schema": SCHEMA, "profile_digests": {k: v.digest for k, v in self.profiles.items()},
                         "model_estimated": True, "native_geometry": False}
        for row in entries:
            _object(row, ("id", "profile", "frames", "missing", "resample",
                          "provider", "preview_max_age_frames"), "source",
                    ("id", "profile", "frames"))
            name = "vision:" + _name(row["id"], "source ID")
            if name in self.sources or row["profile"] not in self.profiles:
                raise ValueError("Duplicate M14.2 source or unknown profile")
            profile = self.profiles[row["profile"]]
            provider = row.get("provider")
            if provider is not None:
                _object(provider, ("name", "batch_sha256"), "GPU provider",
                        ("name", "batch_sha256"))
                if provider["name"] not in ("CUDAExecutionProvider", "DmlExecutionProvider"):
                    raise ValueError("M14.4 provider cache requires an explicit GPU provider")
                self.provider_pins[name] = (provider["name"], _hex(provider["batch_sha256"],
                                                                 "GPU batch receipt"))
            ages = row.get("preview_max_age_frames")
            if ages is not None:
                if (not isinstance(ages, list) or len(ages) != 6 or
                        any(type(age) is not int or not 0 <= age <= 64 for age in ages) or
                        ages[-1] != 0 or any(x < y for x, y in zip(ages, ages[1:]))):
                    raise ValueError("M14.4 preview ages must be six decreasing integers ending in 0")
                if profile.task == "optical_flow" and any(ages):
                    raise ValueError("M14.4 cannot reuse optical flow across distinct frames")
                self.preview_ages[name] = tuple(ages)
            resample = row.get("resample", "none")
            if resample not in ("none", "nearest", "bilinear"):
                raise ValueError("M14.2 requires an explicit supported resampling rule")
            if (profile.width, profile.height) != (project.width, project.height):
                if resample == "none" or profile.task == "optical_flow":
                    raise ValueError("M14.2 resolution mismatch requires explicit non-flow resampling")
            missing = row.get("missing", "error")
            if missing not in ("error", "transparent", "hold"):
                raise ValueError("Invalid M14.2 missing-frame policy")
            frames = row["frames"]
            if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAME_REQUESTS:
                raise ValueError("M14.2 needs 1..256 exact frame requests")
            mapping = {}
            for item in frames:
                _object(item, ("frame", "request"), "frame entry", ("frame", "request"))
                index = item["frame"]
                if type(index) is not int or not 0 <= index < project.frame_count or index in mapping:
                    raise ValueError("Invalid or duplicate M14.2 frame index")
                ref = project.ref(item["request"])
                path = Path(ref["path"]).resolve(strict=True)
                if path.suffix.lower() != ".json" or not path.is_relative_to(self.root):
                    raise ValueError("M14.2 request manifest must be local JSON")
                spec = inspect_request(path)
                if not profile.matches(spec) or spec.manifest_sha256 != ref["sha256"]:
                    raise ValueError("M14.2 request disagrees with its named profile")
                for asset in (spec.model, spec.current, spec.previous):
                    if asset is not None:
                        key = ("model" if asset == spec.model else
                               "current" if asset == spec.current else "previous")
                        project.inputs.append({"path": str(asset), "sha256": spec.input_hashes[key]})
                mapping[index] = spec
            self.sources[name] = (profile, missing, resample, mapping)
        self.source_ids = frozenset(self.sources)
        self.element_masks = {}
        masks = config.get("element_masks", [])
        if not isinstance(masks, list) or len(masks) > MAX_SOURCES:
            raise ValueError("M14.3 element masks require at most sixteen bindings")
        known_layers = {"layer:" + layer["id"] for layer in getattr(project, "data", {}).get("layers", [])
                        if isinstance(layer, dict) and isinstance(layer.get("id"), str)}
        for row in masks:
            _object(row, ("element", "source"), "element mask", ("element", "source"))
            element, source = row["element"], row["source"]
            if (not isinstance(element, str) or element not in known_layers or
                    not isinstance(source, str) or source not in self.sources or
                    element in self.element_masks):
                raise ValueError("M14.3 element mask must bind one known layer to one vision source")
            if self.sources[source][0].task != "segmentation":
                raise ValueError("M14.3 element alpha mask requires a segmentation profile")
            self.element_masks[element] = source
        self.evidence["element_masks"] = dict(self.element_masks)
        self.evidence["gpu_provider_pins"] = {name: {"provider": provider,
            "batch_receipt_sha256": digest} for name, (provider, digest) in self.provider_pins.items()}
        self.evidence["preview_max_age_frames"] = dict(self.preview_ages)
        self.last_used = {}

    def _identity(self, profile, spec):
        from . import vision_m14
        return _sha_document({"schema": CACHE_SCHEMA, "profile_sha256": profile.digest,
                              "request_sha256": spec.manifest_sha256,
                              "model_and_images": spec.input_hashes,
                              "worker_source_sha256": _hash(vision_m14.__file__),
                              "onnxruntime_version": _onnx_runtime_version()})

    def _load(self, folder, profile, spec, key):
        import numpy as np
        if not folder.is_dir() or folder.is_symlink():
            raise ValueError("M14.2 cache must be an ordinary directory")
        expected = {"estimate.npy", "preview.png", "receipt.json", "cache.json"}
        if {x.name for x in folder.iterdir()} != expected:
            raise ValueError("M14.2 cache contains incomplete or unexpected files")
        if any(x.is_symlink() or not x.is_file() for x in folder.iterdir()):
            raise ValueError("M14.2 cache refuses symlinked payloads")
        if sum(x.stat().st_size for x in folder.iterdir()) > MAX_CACHE_BYTES:
            raise ValueError("M14.2 cache payload exceeds the file budget")
        if (folder / "cache.json").stat().st_size > 64 * 1024 or (folder / "receipt.json").stat().st_size > 64 * 1024:
            raise ValueError("M14.2 cache metadata is unreasonably large")
        cache = json.loads((folder / "cache.json").read_text("utf-8"), object_pairs_hook=_unique)
        receipt = json.loads((folder / "receipt.json").read_text("utf-8"), object_pairs_hook=_unique)
        if (cache != {"schema": CACHE_SCHEMA, "key": key,
                      "receipt_sha256": _hash(folder / "receipt.json")} or
                receipt.get("schema") != RESULT_SCHEMA or
                receipt.get("manifest_sha256") != spec.manifest_sha256 or
                receipt.get("inputs_sha256") != spec.input_hashes or
                receipt.get("task") != profile.task or
                receipt.get("estimate_sha256") != _hash(folder / "estimate.npy") or
                receipt.get("preview_sha256") != _hash(folder / "preview.png") or
                receipt.get("model_estimated") is not True):
            raise ValueError("M14.2 cached model estimate fails provenance verification")
        with Image.open(folder / "preview.png") as preview:
            if preview.size != (spec.width, spec.height):
                raise ValueError("M14.2 cached preview dimensions differ")
            preview.verify()
        array = np.load(folder / "estimate.npy", allow_pickle=False)
        if array.dtype != np.dtype("float32") or array.shape != (spec.height, spec.width, spec.channels):
            raise ValueError("M14.2 cached output has unexpected dtype or shape")
        _postprocess(array.transpose(2, 0, 1)[None], spec, np)
        return array

    def _estimate(self, profile, spec):
        if not self.cache.is_relative_to(self.root) or self.cache.is_symlink():
            raise ValueError("M14.2 cache root escaped the project directory")
        # Re-read all pins on every access. An edited model or PNG never hits a
        # stale cached prediction, even if the Compositor instance is retained.
        if inspect_request(spec.manifest) != spec:
            raise ValueError("M14.2 source model or image changed after preparation")
        key = self._identity(profile, spec)
        folder = self.cache / key
        if folder.exists() or folder.is_symlink():
            return self._load(folder, profile, spec, key)
        self.cache.mkdir(parents=True, exist_ok=True)
        if self.cache.is_symlink() or self.cache.resolve() != self.cache:
            raise ValueError("M14.2 cache root changed while opening")
        used = sum(p.stat().st_size for folder in self.cache.iterdir()
                   if folder.is_dir() and not folder.is_symlink()
                   for p in folder.iterdir() if p.is_file() and not p.is_symlink())
        estimate_bytes = spec.width * spec.height * (spec.channels * 4 + 4) + 8192
        if used + estimate_bytes > self.max_cache_bytes:
            raise ValueError("M14.2 content-addressed cache quota exceeded; prune old entries")
        staged = self.cache / (".stage-" + secrets.token_hex(12))
        try:
            infer(spec.manifest, staged)
            self._load_unsealed(staged, spec)
            if used + sum(p.stat().st_size for p in staged.iterdir()) > self.max_cache_bytes:
                raise ValueError("M14.2 cache quota exceeded by the completed inference")
            (staged / "cache.json").write_text(json.dumps(
                {"schema": CACHE_SCHEMA, "key": key,
                 "receipt_sha256": _hash(staged / "receipt.json")}, sort_keys=True) + "\n", "utf-8")
            # Refuse cache overwrites; a racing producer may finish first.
            if folder.exists():
                return self._load(folder, profile, spec, key)
            staged.rename(folder)
            return self._load(folder, profile, spec, key)
        finally:
            if staged.exists():
                shutil.rmtree(staged)

    @staticmethod
    def _load_unsealed(staged, spec):
        if {p.name for p in staged.iterdir()} != {"estimate.npy", "preview.png", "receipt.json"}:
            raise ValueError("Invalid M14.2 native worker output before sealing")
        receipt = json.loads((staged / "receipt.json").read_text("utf-8"), object_pairs_hook=_unique)
        if (receipt.get("schema") != RESULT_SCHEMA or
                receipt.get("manifest_sha256") != spec.manifest_sha256 or
                receipt.get("inputs_sha256") != spec.input_hashes or
                receipt.get("estimate_sha256") != _hash(staged / "estimate.npy") or
                receipt.get("preview_sha256") != _hash(staged / "preview.png")):
            raise ValueError("Invalid M14.2 native worker receipt before sealing")

    def selection(self, source_id, frame, *, quality=None):
        """Plan one exact or explicitly quality-bounded source frame; no inference.

        `quality` is Q0..Q5 only when explicitly requested by a preview host.
        The normal compositor never supplies this argument, so authored output
        preserves the original M14.2 missing-frame contract byte-for-byte.
        """
        if source_id not in self.sources:
            raise ValueError("Unknown M14.4 graph source")
        if type(frame) is not int or not 0 <= frame < self.project.frame_count:
            raise ValueError("M14.4 source needs an exact output frame")
        if quality is not None and (type(quality) is not int or not 0 <= quality <= 5):
            raise ValueError("M14.4 explicit preview quality must be Q0..Q5")
        profile, missing, _resample, mapping = self.sources[source_id]
        selected_at = frame if frame in mapping else None
        reason = "exact" if selected_at is not None else "missing"
        if selected_at is None:
            older = [n for n in mapping if n <= frame]
            if quality is None and missing == "hold" and older:
                selected_at, reason = max(older), "authored_hold"
            elif quality is not None and source_id in self.preview_ages and older:
                age = self.preview_ages[source_id][quality]
                candidate = max(older)
                if frame - candidate <= age:
                    selected_at, reason = candidate, "preview_reuse"
        if selected_at is None:
            if missing == "transparent":
                return None, None, "transparent"
            raise ValueError(f"Missing M14.4 inference request for {source_id} frame {frame}")
        return selected_at, mapping[selected_at], reason

    def source(self, source_id, frame, *, quality=None):
        selected_frame, selected, reason = self.selection(source_id, frame, quality=quality)
        if selected is None:
            self.last_used.pop(source_id, None)
            return Image.new("RGBA", (self.project.width, self.project.height))
        profile, _missing, resample, _mapping = self.sources[source_id]
        pin = self.provider_pins.get(source_id)
        if pin is None:
            estimated = self._estimate(profile, selected)
            key = self._identity(profile, selected)
            provider = "CPUExecutionProvider"
        else:
            # Qualified GPU outputs must have been independently computed and
            # seeded; NEVER fall back to a CPU inference on a GPU cache miss.
            from .vision_provider_cache_m14 import provider_cache_key, load_provider_estimate

            provider, batch_sha256 = pin
            key = provider_cache_key(profile, selected, provider, batch_sha256, selected_frame)
            estimated = load_provider_estimate(self, profile, selected, provider,
                                               batch_sha256, selected_frame)
        self.last_used[source_id] = {"frame": frame, "source_frame": selected_frame,
                                     "selection": reason, "preview_quality": quality,
                                     "provider": provider, "profile_sha256": profile.digest,
                                     "request_sha256": selected.manifest_sha256,
                                     "cache_key": key, "model_estimated": True}
        image = encode_graph_surface(estimated, profile)
        if resample != "none" and image.size != (self.project.width, self.project.height):
            method = (Image.Resampling.NEAREST if resample == "nearest" else Image.Resampling.BILINEAR)
            image = image.resize((self.project.width, self.project.height), resample=method)
        return image

    def mask_elements(self, sources, frame):
        """Mask *isolated graph layer sources*, not the already-composited frame.

        This never mutates caller-owned RGBA surfaces, never interprets relative
        depth/flow as alpha and preserves existing straight-colour values.
        """
        if not self.element_masks:
            return sources
        updated = dict(sources)
        for element, prediction in self.element_masks.items():
            original = updated.get(element)
            if original is None:
                original = Image.new("RGBA", (self.project.width, self.project.height))
            if original.mode != "RGBA" or original.size != (self.project.width, self.project.height):
                raise ValueError("M14.3 element requires a native-sized RGBA source")
            matte = updated.get(prediction)
            if matte is None:
                matte = self.source(prediction, frame)
            if matte.mode != "RGBA" or matte.size != original.size:
                raise ValueError("M14.3 predicted matte size or format mismatch")
            painted = original.copy()
            painted.putalpha(ImageChops.multiply(original.getchannel("A"), matte.getchannel("A")))
            updated[element] = painted
        return updated


def encode_graph_surface(estimate, profile):
    """An explicit RGBA display/matte encoding, not a float HDR or geometry pass."""
    import numpy as np
    arr = np.asarray(estimate)
    if arr.dtype != np.dtype("float32") or arr.shape != (
            profile.height, profile.width, 2 if profile.task == "optical_flow" else 1):
        raise ValueError("M14.2 graph estimate shape or dtype differs")
    if not np.isfinite(arr).all():
        raise ValueError("Nonfinite M14.2 graph estimate")
    lo, hi = profile.display_range
    if profile.task == "segmentation":
        if np.any((arr < 0) | (arr > 1)):
            raise ValueError("M14.2 probability outside [0,1]")
        alpha = np.rint(arr[:, :, 0] * 255).astype("uint8")
        colour = np.full_like(alpha, 255)
        rgba = np.stack((colour, colour, colour, alpha), axis=-1)
    elif profile.task == "relative_depth":
        if np.any(arr < 0):
            raise ValueError("M14.2 negative model-relative depth")
        colour = np.rint(np.clip((arr[:, :, 0] - lo) / (hi - lo), 0, 1) * 255).astype("uint8")
        rgba = np.stack((colour, colour, colour, np.full_like(colour, 255)), axis=-1)
    else:
        vector = np.rint(np.clip((arr - lo) / (hi - lo), 0, 1) * 255).astype("uint8")
        length = np.sqrt(np.sum(arr * arr, axis=-1))
        maxlen = max(abs(lo), abs(hi), 1)
        magnitude = np.rint(np.clip(length / maxlen, 0, 1) * 255).astype("uint8")
        rgba = np.stack((vector[:, :, 0], vector[:, :, 1], magnitude,
                         np.full_like(magnitude, 255)), axis=-1)
    return Image.fromarray(rgba, "RGBA")
