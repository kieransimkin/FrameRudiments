"""M14.3: bounded multi-frame ONNX inference and explicit provider parity.

Analysis is always opt-in, uses local SHA-pinned inputs and publishes no partial
results. GPU execution is *qualified*, not assumed from a provider name. Model
outputs are estimates, never native geometry/depth/flow observations.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import Image

from .vision_m14 import (
    MAX_MANIFEST_BYTES, MAX_PAYLOAD_BYTES, _hash, _local_pinned,
    _postprocess, _preview, _tensor, _unique, inspect_request,
)

SCHEMA = "framerudiments.vision-batch.v1"
RESULT_SCHEMA = "framerudiments.vision-batch-result.v1"
CPU_PROVIDER = "CPUExecutionProvider"
GPU_PROVIDERS = frozenset(("CUDAExecutionProvider", "DmlExecutionProvider"))
MAX_REQUESTS = 16
MAX_BATCH = 8
MAX_TOTAL_PIXELS = 8 * 1024 * 1024
MAX_TIME_SECONDS = 600
MAX_BATCH_MANIFEST_BYTES = 128 * 1024


@dataclass(frozen=True)
class BatchItem:
    frame: int
    request_sha256: str
    spec: object


@dataclass(frozen=True)
class BatchPlan:
    manifest: Path
    digest: str
    provider: str
    batch_size: int
    gpu_mem_limit_mib: int
    timeout: int
    max_abs_error: float
    mean_abs_error: float
    items: tuple[BatchItem, ...]

    def document(self):
        return {"schema": SCHEMA, "provider": self.provider, "batch_size": self.batch_size,
                "requests": [{"frame": item.frame, "request_sha256": item.request_sha256}
                             for item in self.items], "model_sha256": self.items[0].spec.input_hashes["model"],
                "task": self.items[0].spec.task, "size": [self.items[0].spec.width,
                self.items[0].spec.height], "gpu_parity_required": self.provider != CPU_PROVIDER,
                "cpu_comparison": self.provider != CPU_PROVIDER,
                "max_abs_error": self.max_abs_error, "mean_abs_error": self.mean_abs_error,
                "model_estimated": True, "native_geometry": False}


def _valid_number(value, lower, upper, label):
    if type(value) not in (float, int) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"Invalid M14.3 {label}")
    return float(value)


def inspect_batch(path):
    manifest = Path(path).resolve(strict=True)
    if not manifest.is_file() or not 0 < manifest.stat().st_size <= MAX_BATCH_MANIFEST_BYTES:
        raise ValueError("M14.3 batch manifest must be a bounded local JSON file")
    doc = json.loads(manifest.read_text("utf-8"), object_pairs_hook=_unique)
    if (not isinstance(doc, dict) or set(doc) - {"schema", "provider", "batch_size", "items",
            "timeout_seconds", "gpu_mem_limit_mib", "parity"} or
            set(("schema", "provider", "items")) - set(doc) or doc["schema"] != SCHEMA):
        raise ValueError("Invalid M14.3 batch manifest fields")
    provider = doc["provider"]
    if provider != CPU_PROVIDER and provider not in GPU_PROVIDERS:
        raise ValueError("M14.3 requires an explicitly supported execution provider")
    batch_size = doc.get("batch_size", 4)
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH:
        raise ValueError("M14.3 batch_size must be 1..8")
    timeout = doc.get("timeout_seconds", 120)
    if type(timeout) is not int or not 5 <= timeout <= MAX_TIME_SECONDS:
        raise ValueError("M14.3 timeout_seconds must be 5..600")
    gpu_limit = doc.get("gpu_mem_limit_mib", 512)
    if type(gpu_limit) is not int or not 128 <= gpu_limit <= 8192:
        raise ValueError("M14.3 GPU arena limit must be 128..8192 MiB")
    parity = doc.get("parity", {})
    if (not isinstance(parity, dict) or set(parity) - {"max_abs", "mean_abs"}):
        raise ValueError("Invalid M14.3 CPU/GPU parity settings")
    max_error = _valid_number(parity.get("max_abs", 0.02), 0, .1, "max_abs")
    mean_error = _valid_number(parity.get("mean_abs", 0.005), 0, .02, "mean_abs")
    rows = doc["items"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_REQUESTS:
        raise ValueError("M14.3 accepts 1..16 pinned frame requests")
    found = set()
    items = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"frame", "request"}:
            raise ValueError("M14.3 frame rows require exact frame and request fields")
        frame = row["frame"]
        if type(frame) is not int or not 0 <= frame <= 10_000_000 or frame in found:
            raise ValueError("M14.3 requires unique integer encoded frames")
        found.add(frame)
        request = _local_pinned(manifest.parent, row["request"], extension=".json",
                                limit=MAX_MANIFEST_BYTES)
        spec = inspect_request(request)
        if _hash(request) != row["request"]["sha256"].lower():
            raise ValueError("M14.3 request manifest changed during validation")
        items.append(BatchItem(frame, row["request"]["sha256"].lower(), spec))
    items.sort(key=lambda row: row.frame)
    first = items[0].spec
    identity = (first.model, first.input_hashes["model"], first.task, first.width, first.height,
                first.input_names, first.output_name, first.mean, first.std, first.activation)
    for item in items[1:]:
        s = item.spec
        if (s.model, s.input_hashes["model"], s.task, s.width, s.height,
                s.input_names, s.output_name, s.mean, s.std, s.activation) != identity:
            raise ValueError("M14.3 batch must use one pinned model, topology and normalization")
    if first.width * first.height * len(items) > MAX_TOTAL_PIXELS:
        raise ValueError("M14.3 total decoded frame budget exceeded")
    # Conservative host payload budget: 2 inputs + output, staging and comparison.
    per_frame = first.width * first.height * (18 + 4 * first.channels) * 4
    if per_frame * min(len(items), batch_size) > MAX_PAYLOAD_BYTES:
        raise ValueError("M14.3 bounded inference group exceeds the 256 MiB working payload")
    return BatchPlan(manifest, _hash(manifest), provider, batch_size, gpu_limit, timeout,
                     max_error, mean_error, tuple(items))


def _process_peak_rss_bytes():
    """Process high-water RSS when the OS exposes it; not GPU device VRAM."""
    try:
        if os.name == "nt":
            import ctypes

            class MemoryCounters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(MemoryCounters)
            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            handle = kernel.GetCurrentProcess()
            if not ctypes.windll.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.PeakWorkingSetSize)
        import resource
        import sys

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    except (AttributeError, ImportError, OSError, ValueError):
        return None


def _run_provider(plan, provider, stage, *, runtime=None):
    """Construct exactly one ONNX session and refuse provider/graph fallback.

    A static N=1 model is processed sequentially *within the same session*;
    dynamic N models use real batch tensors. Both modes are recorded explicitly.
    """
    import numpy as np

    if runtime is None:
        try:
            import onnxruntime as runtime
        except ImportError as exc:
            raise RuntimeError("M14.3 needs the optional native ONNX Runtime") from exc
    if provider not in runtime.get_available_providers():
        raise RuntimeError(f"Requested ONNX execution provider is unavailable: {provider}")
    first = plan.items[0].spec
    opts = runtime.SessionOptions()
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    opts.enable_mem_pattern = False
    opts.enable_cpu_mem_arena = False
    if hasattr(runtime, "ExecutionMode"):
        opts.execution_mode = runtime.ExecutionMode.ORT_SEQUENTIAL
    opts.enable_profiling = True
    opts.profile_file_prefix = str(stage / ("gpu" if provider != CPU_PROVIDER else "cpu"))
    if provider != CPU_PROVIDER:
        # disable_fallback() only prevents runtime recovery, not CPU kernel
        # placement. This extra config forbids even initial CPU node fallback.
        opts.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
    ep = ([(provider, {"gpu_mem_limit": str(plan.gpu_mem_limit_mib * 1024 * 1024)})]
          if provider == "CUDAExecutionProvider" else [provider])
    started = time.perf_counter()
    session = runtime.InferenceSession(first.model.read_bytes(), sess_options=opts, providers=ep)
    if not callable(getattr(session, "disable_fallback", None)):
        raise RuntimeError("M14.3 needs a runtime supporting disabled provider fallback")
    session.disable_fallback()
    actual = session.get_providers()
    if actual != [provider]:
        raise RuntimeError(f"M14.3 provider fallback detected: requested {provider}, attached {actual}")
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != len(first.input_names) or len(outputs) > 8:
        raise ValueError("M14.3 input/output count differs from the pinned request")
    expected_channels = 6 if first.previous is not None and len(inputs) == 1 else 3
    dynamic = None
    for meta, name in zip(inputs, first.input_names):
        shape = list(meta.shape)
        if (meta.name != name or meta.type != "tensor(float)" or len(shape) != 4 or
                shape[1:] != [expected_channels, first.height, first.width]):
            raise ValueError("M14.3 requires declared float32 NCHW static spatial dimensions")
        batch_dim = shape[0]
        current = "dynamic" if batch_dim is None or isinstance(batch_dim, str) else batch_dim
        if current not in (1, "dynamic") or (dynamic is not None and dynamic != current):
            raise ValueError("M14.3 unsupported or inconsistent ONNX batch dimensions")
        dynamic = current
    chosen = [meta for meta in outputs if meta.name == first.output_name]
    if len(chosen) != 1:
        raise ValueError("M14.3 missing declared output tensor")
    output = chosen[0]
    out_shape = list(output.shape)
    if (output.type != "tensor(float)" or len(out_shape) != 4 or
            out_shape[1:] != [first.channels, first.height, first.width] or
            (out_shape[0] not in (1, None, dynamic) and not isinstance(out_shape[0], str))):
        raise ValueError("M14.3 unsupported output tensor contract")
    groups = []
    estimates = {}
    inference_ms = 0.0
    for index in range(0, len(plan.items), plan.batch_size if dynamic == "dynamic" else 1):
        batch = plan.items[index:index + (plan.batch_size if dynamic == "dynamic" else 1)]
        tensors = []
        for item in batch:
            spec = item.spec
            current = _tensor(spec.current, spec, np)
            if spec.previous is not None:
                previous = _tensor(spec.previous, spec, np)
                if len(inputs) == 1:
                    tensors.append((np.concatenate((previous, current), axis=1),))
                else:
                    tensors.append((previous, current))
            else:
                tensors.append((current,))
        feeds = {name: np.ascontiguousarray(np.concatenate([row[i] for row in tensors], axis=0))
                 for i, name in enumerate(first.input_names)}
        start = time.perf_counter()
        values = session.run([first.output_name], feeds)
        inference_ms += 1000 * (time.perf_counter() - start)
        if len(values) != 1:
            raise ValueError("M14.3 ONNX provider returned incorrect output arity")
        values = np.asarray(values[0])
        expected_shape = (len(batch), first.channels, first.height, first.width)
        if values.dtype != np.dtype("float32") or values.shape != expected_shape:
            raise ValueError("M14.3 provider output shape/precision differs")
        for number, item in enumerate(batch):
            estimates[item.frame] = _postprocess(values[number:number + 1], item.spec, np)
        groups.append(len(batch))
    trace_path = session.end_profiling()
    # Privacy: consume provider assignments and delete the unbounded native trace.
    try:
        trace = json.loads(Path(trace_path).read_text("utf-8"))
        ops = [e["args"].get("provider") for e in trace
               if e.get("cat") == "Node" and isinstance(e.get("args"), dict)
               and "provider" in e["args"]]
        if not ops or any(p != provider for p in ops):
            raise RuntimeError("M14.3 provider graph assignment is missing or includes CPU fallback")
    finally:
        Path(trace_path).unlink(missing_ok=True)
    del session
    return estimates, {"provider": provider, "model_sessions": 1, "batches": groups,
                       "execution_mode": "dynamic_batch" if dynamic == "dynamic" else "shared_session_single",
                       "provider_node_events": len(ops),
                       "session_and_run_ms": round(1000 * (time.perf_counter() - started), 3),
                       "inference_ms": round(inference_ms, 3),
                       "process_peak_rss_bytes": _process_peak_rss_bytes(),
                       "gpu_arena_limit_mib": plan.gpu_mem_limit_mib if provider == "CUDAExecutionProvider" else None,
                       "gpu_memory_hard_bounded": False,
                       "runtime_version": str(getattr(runtime, "__version__", "unknown"))}


def _worker(manifest, stage):
    import numpy as np

    plan = inspect_batch(manifest)
    estimates, primary = _run_provider(plan, plan.provider, stage)
    if plan.provider != CPU_PROVIDER:
        references, cpu = _run_provider(plan, CPU_PROVIDER, stage)
    else:
        references, cpu = {}, None
    rows = []
    folder = stage / "frames"
    folder.mkdir()
    for item in plan.items:
        result = estimates[item.frame]
        stats = None
        if cpu is not None:
            delta = np.abs(result.astype(np.float64) - references[item.frame].astype(np.float64))
            stats = {"max_abs": float(delta.max()), "mean_abs": float(delta.mean())}
            if stats["max_abs"] > plan.max_abs_error or stats["mean_abs"] > plan.mean_abs_error:
                raise ValueError(f"M14.3 GPU/CPU numerical parity failed on frame {item.frame}")
        child = folder / f"{item.frame:08d}"
        child.mkdir()
        np.save(child / "estimate.npy", result, allow_pickle=False)
        preview, mapping = _preview(result, item.spec)
        preview.save(child / "preview.png")
        row = {"frame": item.frame, "request_sha256": item.request_sha256,
               "inputs_sha256": item.spec.input_hashes, "estimate_sha256": _hash(child / "estimate.npy"),
               "preview_sha256": _hash(child / "preview.png"), "value_range":
               [float(np.min(result)), float(np.max(result))],
               "preview_mapping": mapping, "parity": stats, "model_estimated": True}
        (child / "receipt.json").write_text(json.dumps(row, sort_keys=True, indent=2) + "\n", "utf-8")
        rows.append({"frame": item.frame, "receipt_sha256": _hash(child / "receipt.json")})
    receipt = {"schema": RESULT_SCHEMA, "manifest_sha256": plan.digest,
               "model_sha256": plan.items[0].spec.input_hashes["model"], "provider": plan.provider,
               "execution": primary, "cpu_reference": cpu, "frames": rows,
               "numeric_parity_passed": cpu is not None,
               "model_estimated": True, "native_geometry": False,
               "resource_scope": "worker runtime timing and CUDA arena hint; not physical GPU peak VRAM"}
    (stage / "batch.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", "utf-8")


def _verify(stage, plan):
    import numpy as np

    if {p.name for p in stage.iterdir()} != {"batch.json", "frames"}:
        raise ValueError("Incomplete M14.3 batch worker output")
    receipt = json.loads((stage / "batch.json").read_text("utf-8"), object_pairs_hook=_unique)
    if (receipt.get("schema") != RESULT_SCHEMA or receipt.get("manifest_sha256") != plan.digest or
            receipt.get("model_sha256") != plan.items[0].spec.input_hashes["model"] or
            receipt.get("provider") != plan.provider or receipt.get("model_estimated") is not True or
            not isinstance(receipt.get("frames"), list) or
            [r.get("frame") for r in receipt["frames"]] != [x.frame for x in plan.items]):
        raise ValueError("M14.3 batch receipt fails identity verification")
    expected_names = {f"{item.frame:08d}" for item in plan.items}
    frames = stage / "frames"
    if frames.is_symlink() or {p.name for p in frames.iterdir()} != expected_names:
        raise ValueError("M14.3 batch contains unexpected frame directories")
    for item, row in zip(plan.items, receipt["frames"]):
        folder = frames / f"{item.frame:08d}"
        if folder.is_symlink() or {p.name for p in folder.iterdir()} != {"estimate.npy", "preview.png", "receipt.json"}:
            raise ValueError("M14.3 incomplete prediction files")
        if any(p.is_symlink() or not p.is_file() for p in folder.iterdir()):
            raise ValueError("M14.3 symlinked worker output is forbidden")
        if (folder / "receipt.json").stat().st_size > 64 * 1024:
            raise ValueError("M14.3 oversized frame receipt")
        if _hash(folder / "receipt.json") != row.get("receipt_sha256"):
            raise ValueError("M14.3 corrupted frame receipt")
        details = json.loads((folder / "receipt.json").read_text("utf-8"), object_pairs_hook=_unique)
        if (details.get("frame") != item.frame or details.get("request_sha256") != item.request_sha256 or
                details.get("inputs_sha256") != item.spec.input_hashes or
                details.get("estimate_sha256") != _hash(folder / "estimate.npy") or
                details.get("preview_sha256") != _hash(folder / "preview.png")):
            raise ValueError("M14.3 prediction SHA-256 mismatch")
        prediction = np.load(folder / "estimate.npy", allow_pickle=False)
        if prediction.dtype != np.dtype("float32") or prediction.shape != (
                item.spec.height, item.spec.width, item.spec.channels):
            raise ValueError("M14.3 invalid float32 prediction")
        _postprocess(prediction.transpose(2, 0, 1)[None], item.spec, np)
        with Image.open(folder / "preview.png") as preview:
            if preview.size != (item.spec.width, item.spec.height):
                raise ValueError("M14.3 invalid preview size")
            preview.verify()
    if _hash(plan.manifest) != plan.digest:
        raise ValueError("M14.3 batch manifest changed during worker execution")
    for item in plan.items:
        if inspect_request(item.spec.manifest) != item.spec:
            raise ValueError("M14.3 pinned model or image changed during inference")
    return receipt


def infer_batch(manifest, output):
    plan = inspect_batch(manifest)
    target = Path(output).absolute()
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("M14.3 output must be a new directory in an existing parent")
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        try:
            child = subprocess.run([sys.executable, "-m", "framerudiments.vision_batch_m14",
                                    "--worker", str(plan.manifest), str(stage)],
                                   capture_output=True, text=True, timeout=plan.timeout)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("M14.3 batch worker exceeded its deadline") from exc
        if child.returncode:
            raise RuntimeError("M14.3 worker failed: " + child.stderr[-1600:])
        receipt = _verify(stage, plan)
        if target.exists() or target.is_symlink():
            raise ValueError("M14.3 output destination appeared while processing")
        stage.rename(target)
        return receipt
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def seed_graph_cache(owner, manifest, batch_output):
    """Explicitly import independently verified CPU batch estimates into M14.2.

    One batch can prewarm several exact frames sharing a model. GPU predictions
    are deliberately refused: M14.2's cache key is CPU-specific and must not
    silently change when a different execution provider is used.
    """
    import secrets
    from .vision_graph_m14 import CACHE_SCHEMA, _onnx_runtime_version

    plan = inspect_batch(manifest)
    if plan.provider != CPU_PROVIDER:
        raise ValueError("M14.3 GPU estimates cannot seed the CPU-only graph cache")
    folder = Path(batch_output).resolve(strict=True)
    result = _verify(folder, plan)
    if result.get("execution", {}).get("runtime_version") != _onnx_runtime_version():
        raise ValueError("M14.3 batch runtime and graph cache runtime differ")
    expected = []
    keys = set()
    for item in plan.items:
        matches = [(profile, spec) for profile, _missing, _resample, mapping in owner.sources.values()
                   for frame, spec in mapping.items() if frame == item.frame and spec == item.spec]
        if not matches:
            raise ValueError("M14.3 batch includes a frame not owned by the project's graph")
        for profile, spec in matches:
            key = owner._identity(profile, spec)
            if key not in keys:
                expected.append((item, profile, spec, key))
                keys.add(key)
    if owner.cache.is_symlink() or not owner.cache.resolve().is_relative_to(owner.root):
        raise ValueError("M14.3 graph cache root is not a trusted local directory")
    owner.project.verify_inputs()
    owner.cache.mkdir(parents=True, exist_ok=True)
    existing = sum(p.stat().st_size for child in owner.cache.iterdir()
                   if child.is_dir() and not child.is_symlink()
                   for p in child.iterdir() if p.is_file() and not p.is_symlink())
    prepared = []
    added = []
    try:
        for item, profile, spec, key in expected:
            target = owner.cache / key
            if target.exists() or target.is_symlink():
                owner._load(target, profile, spec, key)  # existing entry must be valid
                continue
            staged = owner.cache / (".stage-" + secrets.token_hex(12))
            staged.mkdir()
            prepared.append((staged, target, profile, spec, key))
            source = folder / "frames" / f"{item.frame:08d}"
            shutil.copyfile(source / "estimate.npy", staged / "estimate.npy")
            shutil.copyfile(source / "preview.png", staged / "preview.png")
            frame_receipt = json.loads((source / "receipt.json").read_text("utf-8"),
                                       object_pairs_hook=_unique)
            receipt = dict(spec.document(), schema="framerudiments.vision-estimate.v1",
                           model_runtime="onnxruntime", runtime_version=result["execution"]["runtime_version"],
                           tensor_shape=[spec.height, spec.width, spec.channels],
                           tensor_dtype="float32", estimate_sha256=_hash(staged / "estimate.npy"),
                           preview_sha256=_hash(staged / "preview.png"),
                           preview_mapping=frame_receipt["preview_mapping"],
                           value_range=frame_receipt["value_range"])
            (staged / "receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n", "utf-8")
            owner._load_unsealed(staged, spec)
            (staged / "cache.json").write_text(json.dumps(
                {"schema": CACHE_SCHEMA, "key": key,
                 "receipt_sha256": _hash(staged / "receipt.json")}, sort_keys=True) + "\n", "utf-8")
            owner._load(staged, profile, spec, key)
        required = sum(p.stat().st_size for staged, *_ in prepared for p in staged.iterdir())
        if required + existing > owner.max_cache_bytes:
            raise ValueError("M14.3 batch exceeds the existing cache quota")
        owner.project.verify_inputs()
        for staged, target, profile, spec, key in prepared:
            if target.exists() or target.is_symlink():
                raise ValueError("M14.3 graph cache entry appeared during publication")
            staged.rename(target)
            added.append(target)
        return {"schema": "framerudiments.vision-batch-cache.v1", "new_entries": len(added),
                "existing_entries": len(expected) - len(added),
                "model_estimated": True, "provider": CPU_PROVIDER}
    except BaseException:
        for target in reversed(added):
            shutil.rmtree(target)
        raise
    finally:
        for staged, *_ in prepared:
            if staged.exists():
                shutil.rmtree(staged)


def _entry():
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise ValueError("M14.3 module is a subprocess worker; use framerudiments-vision")
    _worker(sys.argv[2], Path(sys.argv[3]))


if __name__ == "__main__":
    _entry()
