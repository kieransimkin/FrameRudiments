"""M14.4: provider-scoped, independently sealed GPU inference cache.

Only already-qualified M14.3 batch outputs can be imported. A GPU cache miss
never triggers inference or substitutes a CPU estimate. This is a provenance
and numeric-parity receipt, not certification of a physical device or VRAM cap.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import secrets
import shutil

from PIL import Image

from .vision_batch_m14 import CPU_PROVIDER, GPU_PROVIDERS, _verify, inspect_batch
from .vision_graph_m14 import _sha_document, _unique, MAX_CACHE_BYTES
from .vision_m14 import _hash, _postprocess, inspect_request

SCHEMA = "framerudiments.vision-provider-cache.v1"


def provider_cache_key(profile, spec, provider, batch_sha256, frame):
    """Include provider, batch receipt, input pins and producer source identity."""
    from . import vision_batch_m14, vision_m14

    if provider not in GPU_PROVIDERS:
        raise ValueError("M14.4 provider cache requires explicit GPU provenance")
    if type(frame) is not int or frame < 0:
        raise ValueError("M14.4 provider cache needs an exact native frame")
    return _sha_document({"schema": SCHEMA, "profile_sha256": profile.digest,
                          "request_sha256": spec.manifest_sha256,
                          "input_hashes": spec.input_hashes, "provider": provider,
                          "batch_receipt_sha256": batch_sha256, "source_frame": frame,
                          "worker_sha256": _hash(vision_m14.__file__),
                          "batch_worker_sha256": _hash(vision_batch_m14.__file__)})


def _folder(owner, key):
    if owner.cache.is_symlink() or not owner.cache.resolve().is_relative_to(owner.root):
        raise ValueError("M14.4 provider cache root changed or escaped the project")
    return owner.cache / ("gpu-" + key)


def _read(folder, profile, spec, provider, batch_sha, frame, key):
    """Independent all-bytes readback; no ONNX Runtime required for playback."""
    import numpy as np

    if not folder.is_dir() or folder.is_symlink():
        raise ValueError("M14.4 GPU provider prediction is missing or untrusted")
    names = {"estimate.npy", "preview.png", "receipt.json", "cache.json"}
    if {p.name for p in folder.iterdir()} != names or any(
            p.is_symlink() or not p.is_file() for p in folder.iterdir()):
        raise ValueError("M14.4 provider cache entry is incomplete or symlinked")
    if (sum(p.stat().st_size for p in folder.iterdir()) > MAX_CACHE_BYTES or
            max((folder / name).stat().st_size for name in ("cache.json", "receipt.json")) > 64 * 1024):
        raise ValueError("M14.4 provider cache entry exceeds its bounded payload")
    seal = json.loads((folder / "cache.json").read_text("utf-8"), object_pairs_hook=_unique)
    receipt = json.loads((folder / "receipt.json").read_text("utf-8"), object_pairs_hook=_unique)
    if (not isinstance(seal, dict) or seal.get("schema") != SCHEMA or seal.get("key") != key or
            seal.get("provider") != provider or seal.get("batch_receipt_sha256") != batch_sha or
            seal.get("request_sha256") != spec.manifest_sha256 or
            seal.get("model_sha256") != spec.input_hashes["model"] or
            seal.get("source_frame") != frame or seal.get("model_estimated") is not True or
            seal.get("native_geometry") is not False or
            seal.get("frame_receipt_sha256") != _hash(folder / "receipt.json") or
            receipt.get("frame") != frame or
            receipt.get("request_sha256") != spec.manifest_sha256 or
            receipt.get("inputs_sha256") != spec.input_hashes or
            receipt.get("estimate_sha256") != _hash(folder / "estimate.npy") or
            receipt.get("preview_sha256") != _hash(folder / "preview.png") or
            receipt.get("model_estimated") is not True):
        raise ValueError("M14.4 provider cache provenance mismatch")
    parity, thresholds = seal.get("parity"), seal.get("parity_thresholds")
    if (parity != receipt.get("parity") or
            not isinstance(parity, dict) or set(parity) != {"max_abs", "mean_abs"} or
            not isinstance(thresholds, dict) or set(thresholds) != set(parity) or
            any(type(parity[n]) not in (int, float) or not math.isfinite(parity[n]) or
                parity[n] < 0 or type(thresholds[n]) not in (int, float) or
                not math.isfinite(thresholds[n]) or parity[n] > thresholds[n]
                for n in parity)):
        raise ValueError("M14.4 GPU parity receipt is missing or failed")
    with Image.open(folder / "preview.png") as preview:
        if preview.size != (spec.width, spec.height) or preview.format != "PNG":
            raise ValueError("M14.4 GPU preview dimensions differ")
        preview.verify()
    array = np.load(folder / "estimate.npy", allow_pickle=False)
    if array.dtype != np.dtype("float32") or array.shape != (
            spec.height, spec.width, spec.channels):
        raise ValueError("M14.4 GPU float32 output shape differs")
    _postprocess(array.transpose(2, 0, 1)[None], spec, np)
    return array


def load_provider_estimate(owner, profile, spec, provider, batch_sha256, frame):
    """Reject missing GPU cache entries; never invoke an inference runtime."""
    if inspect_request(spec.manifest) != spec:
        raise ValueError("M14.4 GPU model or image was modified after preparation")
    key = provider_cache_key(profile, spec, provider, batch_sha256, frame)
    result = _read(_folder(owner, key), profile, spec, provider, batch_sha256, frame, key)
    if inspect_request(spec.manifest) != spec:
        raise ValueError("M14.4 GPU model or image changed during cache readback")
    return result


def seed_provider_graph_cache(owner, manifest, batch_output):
    """Atomically publish preverified GPU predictions for explicitly pinned sources.

    Must receive a real M14.3 batch with both a provider execution receipt and
    CPU numerical reference. Existing entries are validated, never replaced.
    Newly published entries are rolled back together on any failure.
    """
    plan = inspect_batch(manifest)
    if plan.provider not in GPU_PROVIDERS:
        raise ValueError("M14.4 GPU seed requires a qualified GPU batch")
    output = Path(batch_output).resolve(strict=True)
    if not output.is_dir() or output.is_symlink():
        raise ValueError("M14.4 batch result must be a trusted local directory")
    receipt = _verify(output, plan)
    digest = _hash(output / "batch.json")
    primary, cpu = receipt.get("execution"), receipt.get("cpu_reference")
    if (not isinstance(primary, dict) or not isinstance(cpu, dict) or
            receipt.get("numeric_parity_passed") is not True or
            primary.get("provider") != plan.provider or cpu.get("provider") != CPU_PROVIDER or
            type(primary.get("provider_node_events")) is not int or
            primary["provider_node_events"] < 1 or
            primary.get("model_sessions") != 1):
        raise ValueError("M14.4 batch lacks verified GPU kernels or CPU reference")
    if not getattr(owner, "provider_pins", None):
        raise ValueError("M14.4 project declares no provider-scoped graph sources")
    matches = {}
    for item in plan.items:
        supported = False
        for name, (profile, _missing, _resample, mapping) in owner.sources.items():
            if (owner.provider_pins.get(name) != (plan.provider, digest) or
                    mapping.get(item.frame) != item.spec):
                continue
            supported = True
            key = provider_cache_key(profile, item.spec, plan.provider, digest, item.frame)
            matches[key] = (item, profile)
        if not supported:
            raise ValueError(f"M14.4 batch frame {item.frame} is not pinned by the project")
    if not callable(getattr(owner.project, "verify_inputs", None)):
        raise ValueError("M14.4 provider import requires project pin verification")
    owner.project.verify_inputs()
    if owner.cache.is_symlink() or not owner.cache.resolve().is_relative_to(owner.root):
        raise ValueError("M14.4 cache root is not trusted")
    owner.cache.mkdir(parents=True, exist_ok=True)
    # The existing CPU cache quota counts immediate child payloads. GPU entries
    # are stored at the same level so neither provider escapes that total.
    used = sum(p.stat().st_size for child in owner.cache.iterdir()
               if child.is_dir() and not child.is_symlink()
               for p in child.iterdir() if p.is_file() and not p.is_symlink())
    prepared = []
    committed = []
    existing_count = 0
    try:
        for key, (item, profile) in sorted(matches.items()):
            target = _folder(owner, key)
            if target.exists() or target.is_symlink():
                _read(target, profile, item.spec, plan.provider, digest, item.frame, key)
                existing_count += 1
                continue
            stage = owner.cache / (".stage-gpu-" + secrets.token_hex(12))
            stage.mkdir()
            prepared.append((stage, target, item, profile, key))
            frame_dir = output / "frames" / f"{item.frame:08d}"
            for name in ("estimate.npy", "preview.png", "receipt.json"):
                shutil.copyfile(frame_dir / name, stage / name)
            stamp = json.loads((frame_dir / "receipt.json").read_text("utf-8"),
                               object_pairs_hook=_unique)
            metadata = {"schema": SCHEMA, "key": key, "provider": plan.provider,
                        "batch_receipt_sha256": digest, "request_sha256": item.spec.manifest_sha256,
                        "model_sha256": item.spec.input_hashes["model"], "source_frame": item.frame,
                        "frame_receipt_sha256": _hash(stage / "receipt.json"),
                        "parity": stamp["parity"], "parity_thresholds": {
                            "max_abs": plan.max_abs_error, "mean_abs": plan.mean_abs_error},
                        "model_estimated": True, "native_geometry": False,
                        "runtime_version": primary.get("runtime_version", "unknown"),
                        "device_acceptance": "provider-kernel-and-CPU-parity-only"}
            (stage / "cache.json").write_text(json.dumps(metadata, sort_keys=True) + "\n", "utf-8")
            _read(stage, profile, item.spec, plan.provider, digest, item.frame, key)
        required = sum(p.stat().st_size for stage, *_ in prepared for p in stage.iterdir())
        if used + required > owner.max_cache_bytes:
            raise ValueError("M14.4 provider cache exceeds the total project quota")
        owner.project.verify_inputs()
        if _hash(output / "batch.json") != digest or _verify(output, plan) != receipt:
            raise ValueError("M14.4 batch evidence changed before cache publication")
        for stage, target, item, profile, key in prepared:
            if target.exists() or target.is_symlink():
                raise ValueError("M14.4 GPU cache destination appeared during import")
            stage.rename(target)
            committed.append(target)
        return {"schema": "framerudiments.vision-provider-import.v1", "provider": plan.provider,
                "batch_receipt_sha256": digest, "new_entries": len(committed),
                "existing_entries": existing_count, "numeric_parity_passed": True,
                "physical_device_certified": False}
    except BaseException:
        for target in reversed(committed):
            shutil.rmtree(target)
        raise
    finally:
        for stage, *_ in prepared:
            if stage.exists():
                shutil.rmtree(stage)
