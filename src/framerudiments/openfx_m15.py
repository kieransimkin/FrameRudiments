"""M15.3 opt-in OpenFX plugin admission and owned native child execution.

The ordinary renderer NEVER loads plug-in code. The parent validates trusted,
SHA-pinned local binaries without dlopen; only an OS-owned child negotiates the
standard OpenFX v1 Filter/RGBA8 subset. This is process failure containment,
not a secure sandbox for untrusted plug-ins: the child has the user's OS rights.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import multiprocessing
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
from tempfile import TemporaryDirectory

from PIL import Image

SCHEMA = "framerudiments.openfx-filter.v1"
RECEIPT_SCHEMA = "framerudiments.openfx-render.v1"
MAX_PIXELS = 4096 * 4096
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_TRANSFER_BYTES = 128 * 1024 * 1024
MAX_BINARY_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_TIMEOUT = 120
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,127}\Z")
SUPPORTED = {"api": "OfxImageEffectPluginAPI", "api_version": 1,
             "context": "OfxImageEffectContextFilter", "clip_names": ["Source", "Output"],
             "pixel_depth": "OfxBitDepthByte", "component": "OfxImageComponentRGBA",
             "alpha": "straight", "transfer": "sRGB", "tile_rendering": False,
             "frame_memory_abi": "CPU only", "parameter_suite": False,
             "host_suites": ["OfxPropertySuiteV1", "OfxImageEffectSuiteV1"]}


def _hash(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _hex(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError("OpenFX input requires an exact SHA-256 pin")
    return value.lower()


def _strict_json(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_MANIFEST_BYTES:
        raise ValueError("OpenFX manifest is missing or unbounded")

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError("Duplicate OpenFX manifest field")
            result[key] = value
        return result

    return json.loads(path.read_text("utf-8"), object_pairs_hook=pairs)


def _relative(base, value, suffix):
    if (not isinstance(value, str) or not value or len(value) > 512 or
            PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute() or
            "\\" in value or ":" in value or ".." in PurePosixPath(value).parts):
        raise ValueError("OpenFX paths must be relative to the manifest without traversal")
    root = base.resolve()
    candidate = (root / value).resolve(strict=True)
    if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.is_symlink():
        raise ValueError("OpenFX input must be a regular local file inside the manifest root")
    if candidate.suffix.lower() not in suffix:
        raise ValueError("OpenFX input has an unexpected file type")
    return candidate


def _pin(path, sha, limit):
    if not 0 < path.stat().st_size <= limit:
        raise ValueError("OpenFX pinned file exceeds its byte limit")
    if _hash(path) != _hex(sha):
        raise ValueError("OpenFX SHA-256 pin mismatch")
    return path


def _native_arch(path):
    """PE/ELF/Mach-O *metadata* scan only; NEVER load the binary here."""
    with path.open("rb") as stream:
        data = stream.read(4096)
    arch = platform.machine().lower()
    is_arm = arch in {"aarch64", "arm64"}
    is_x64 = arch in {"x86_64", "amd64", "x64"}
    if not (is_arm or is_x64):
        raise ValueError("OpenFX architecture has not been qualified")
    system = platform.system()
    if system == "Linux":
        if len(data) < 20 or data[:4] != b"\x7fELF" or data[4:6] != b"\x02\x01":
            raise ValueError("OpenFX requires a little-endian 64-bit ELF binary")
        machine = int.from_bytes(data[18:20], "little")
        if machine != (183 if is_arm else 62):
            raise ValueError("OpenFX ELF machine does not match the host")
        return "ELF64-aarch64" if is_arm else "ELF64-x86_64"
    if system == "Windows":
        if len(data) < 66 or data[:2] != b"MZ":
            raise ValueError("OpenFX Windows plugin must have a PE header")
        offset = int.from_bytes(data[0x3c:0x40], "little")
        if offset + 6 > len(data) or data[offset:offset+4] != b"PE\0\0":
            raise ValueError("OpenFX PE signature not found inside bounded header")
        machine = int.from_bytes(data[offset+4:offset+6], "little")
        if machine != (0xaa64 if is_arm else 0x8664):
            raise ValueError("OpenFX PE machine does not match the host")
        return "PE-aarch64" if is_arm else "PE-x86_64"
    if system == "Darwin":
        if len(data) < 8 or data[:4] != b"\xcf\xfa\xed\xfe":
            raise ValueError("Only thin little-endian 64-bit Mach-O is supported")
        cpu = int.from_bytes(data[4:8], "little")
        if cpu != (0x0100000c if is_arm else 0x01000007):
            raise ValueError("OpenFX Mach-O CPU differs from host")
        return "MachO64-aarch64" if is_arm else "MachO64-x86_64"
    raise ValueError("OpenFX platform is not supported")


@dataclass(frozen=True)
class Request:
    manifest: Path
    digest: str
    plugin: Path
    plugin_sha: str
    identifier: str
    declared_license: str
    architecture: str
    source: Path
    source_sha: str
    width: int
    height: int
    mode: str
    frame: int
    fps: Fraction
    timeout: int

    @property
    def seconds(self):
        return Fraction(self.frame, 1) / self.fps


def inspect(manifest):
    path = Path(manifest).resolve(strict=True)
    data = _strict_json(path)
    if not isinstance(data, dict) or set(data) != {"schema", "plugin", "source", "frame", "fps", "timeout_seconds", "pixel_contract"}:
        raise ValueError("OpenFX manifest fields do not match the supported schema")
    if data["schema"] != SCHEMA or not isinstance(data["plugin"], dict) or set(data["plugin"]) != {
            "path", "sha256", "identifier", "declared_license", "trusted_execution"}:
        raise ValueError("OpenFX plugin must be explicitly admitted")
    plugin = data["plugin"]
    if plugin["trusted_execution"] is not True or plugin["declared_license"] not in (
            "MIT", "BSD-3-Clause", "Apache-2.0", "ISC"):
        raise ValueError("OpenFX requires explicit trusted execution and reviewed licence declaration")
    if not isinstance(plugin["identifier"], str) or not _ID.fullmatch(plugin["identifier"]):
        raise ValueError("OpenFX plugin identifier is invalid")
    dll = _relative(path.parent, plugin["path"], (".so", ".dll", ".dylib"))
    _pin(dll, plugin["sha256"], MAX_BINARY_BYTES)
    architecture = _native_arch(dll)
    source = data["source"]
    if not isinstance(source, dict) or set(source) != {"path", "sha256"}:
        raise ValueError("OpenFX requires a pinned PNG Source")
    image_path = _relative(path.parent, source["path"], (".png",))
    _pin(image_path, source["sha256"], MAX_INPUT_BYTES)
    contract = data["pixel_contract"]
    if not isinstance(contract, dict) or set(contract) != {"mode", "alpha", "transfer"}:
        raise ValueError("OpenFX requires explicit RGB/A colour and alpha contracts")
    mode = contract["mode"]
    if (mode not in ("RGB8", "RGBA8") or contract["transfer"] != "sRGB" or
            contract["alpha"] != ("straight" if mode == "RGBA8" else "opaque")):
        raise ValueError("OpenFX supports only sRGB RGB8 opaque / RGBA8 straight")
    with Image.open(image_path) as image:
        if image.format != "PNG" or image.mode != mode.removesuffix("8"):
            raise ValueError("OpenFX image must be an actual RGB/RGBA PNG without conversion")
        width, height = image.size
        if not 16 <= width <= 4096 or not 16 <= height <= 4096 or width * height > MAX_PIXELS:
            raise ValueError("OpenFX image exceeds its bounded dimensions")
        if width * height * 4 * 2 > MAX_TRANSFER_BYTES:
            raise ValueError("OpenFX source and output pixel transfers exceed budget")
        image.verify()
    frame = data["frame"]
    if type(frame) is not int or not 0 <= frame < 10_000_000:
        raise ValueError("OpenFX frame index must be an unsigned bounded integer")
    fps_raw = data["fps"]
    if not isinstance(fps_raw, str) or not re.fullmatch(r"[1-9][0-9]{0,6}(?:/[1-9][0-9]{0,6})?", fps_raw):
        raise ValueError("OpenFX FPS must be a rational string")
    fps = Fraction(fps_raw)
    if not 1 <= fps <= 120:
        raise ValueError("OpenFX FPS is outside 1..120")
    timeout = data["timeout_seconds"]
    if type(timeout) is not int or not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError("OpenFX timeout must be an integer in 1..120 seconds")
    return Request(path, _hash(path), dll, _hex(plugin["sha256"]), plugin["identifier"],
                   plugin["declared_license"], architecture, image_path,
                   _hex(source["sha256"]), width, height, mode, frame, fps, timeout)


def describe(manifest):
    """Only read metadata, checksums and image headers. Never load a library."""
    r = inspect(manifest)
    return {"schema": SCHEMA, "execution": "not_performed", "binary_loaded": False,
            "plugin_identifier_claim": r.identifier, "declared_license": r.declared_license,
            "architecture": r.architecture, "plugin_sha256": r.plugin_sha,
            "source_sha256": r.source_sha, "dimensions": [r.width, r.height],
            "pixel_contract": {"mode": r.mode, "transfer": "sRGB", "alpha": "straight" if r.mode == "RGBA8" else "opaque"},
            "frame": r.frame, "fps": str(r.fps), "time_seconds": str(r.seconds),
            "supported_host": SUPPORTED, "licence_verified_externally": False,
            "plugin_abi_verified_only_after_execution": True}


def _worker(connection, manifest, output):
    """First establish owned Windows Job/POSIX process group; THEN dlopen."""
    from .preview_process_scope import child_enter_scope

    try:
        mode = child_enter_scope()
        if os.name == "posix":
            # A deliberately crashing plugin must not leave a massive core dump.
            import resource
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        connection.send({"scope": mode, "pid": os.getpid()})
        if not connection.poll(10) or connection.recv() != {"permit": True}:
            return
        r = inspect(manifest)
        from .openfx_host_m15 import run_plugin

        with Image.open(r.source) as image:
            image.load()
            converted = image.convert("RGBA") if r.mode == "RGB8" else image.copy()
        if _hash(r.source) != r.source_sha or _hash(r.plugin) != r.plugin_sha:
            raise ValueError("OpenFX media or plugin changed while entering the host")
        result, evidence = run_plugin(r.plugin, r.identifier, converted, r.frame, r.fps)
        if r.mode == "RGB8":
            result = result.convert("RGB")
        result.save(output, format="PNG")
        if _hash(r.source) != r.source_sha or _hash(r.plugin) != r.plugin_sha or _hash(r.manifest) != r.digest:
            raise ValueError("OpenFX media, plugin or manifest changed during execution")
        connection.send({"ok": True, "evidence": evidence, "sha256": _hash(output)})
    except BaseException as exc:
        try:
            # Do not send private paths, plugin logs or source material to clients.
            connection.send({"ok": False, "error_type": type(exc).__name__})
        except (OSError, BrokenPipeError, EOFError):
            pass
    finally:
        try:
            connection.poll(30) and connection.recv()  # stay alive until owned scope is killed
        except (OSError, EOFError):
            pass
        connection.close()


def _execute_worker(r, staging):
    from .preview_process_scope import ParentProcessScope

    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=True)
    process = ctx.Process(target=_worker, args=(child, str(r.manifest), str(staging)), daemon=False)
    scope = None
    try:
        process.start()
        child.close()
        if not parent.poll(min(r.timeout, 12)):
            raise TimeoutError("OpenFX worker did not enter an owned scope")
        ready = parent.recv()
        if (not isinstance(ready, dict) or ready.get("pid") != process.pid or
                ready.get("scope") not in {"posix_session", "windows_job"}):
            raise RuntimeError("OpenFX worker failed process ownership handshake")
        scope = ParentProcessScope(process, mode=ready["scope"])
        parent.send({"permit": True})
        if not parent.poll(r.timeout):
            raise TimeoutError("OpenFX render exceeded its worker deadline")
        result = parent.recv()
        if not isinstance(result, dict) or result.get("ok") is not True:
            typ = result.get("error_type") if isinstance(result, dict) else "WorkerError"
            raise RuntimeError(f"OpenFX plugin worker rejected the render ({typ})")
        return result
    except (EOFError, BrokenPipeError) as exc:
        raise RuntimeError("OpenFX native plugin crashed or disconnected") from exc
    finally:
        parent.close()
        child.close()
        if scope is not None:
            scope.terminate(timeout_seconds=3)
        elif process.pid is not None:
            # The worker has not been authorised to dlopen until scope ownership.
            process.terminate()
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=3)
        if process.pid is not None and not process.is_alive():
            process.close()


def render(manifest, output):
    """Bounded, transactionally published offline OpenFX single-frame filter."""
    r = inspect(manifest)
    target = Path(output).absolute()
    receipt_path = target.with_name(target.name + ".receipt.json")
    if (target.suffix.lower() != ".png" or not target.parent.is_dir() or
            any(p.exists() or p.is_symlink() for p in (target, receipt_path))):
        raise ValueError("OpenFX needs a new .png output and unused receipt path")
    # Preflight this exact destination filesystem before starting native work.
    with TemporaryDirectory(prefix=".fr-ofx-probe-", dir=target.parent) as probe:
        source = Path(probe) / "source"
        source.write_bytes(b"openfx-preflight")
        if source.read_bytes() != b"openfx-preflight":
            raise OSError("OpenFX output parent failed readback preflight")
        moved = source.with_name("renamed")
        source.replace(moved)
        if moved.read_bytes() != b"openfx-preflight":
            raise OSError("OpenFX output parent failed rename preflight")
        moved.unlink()
    created = []
    with TemporaryDirectory(prefix=".fr-ofx-", dir=target.parent) as owned:
        temp = Path(owned) / "output.png"
        report = _execute_worker(r, temp)
        if not temp.is_file() or temp.stat().st_size > MAX_INPUT_BYTES or _hash(temp) != report["sha256"]:
            raise ValueError("OpenFX worker output is absent or changed")
        with Image.open(temp) as image:
            if image.format != "PNG" or image.size != (r.width, r.height) or image.mode != r.mode.removesuffix("8"):
                raise ValueError("OpenFX output format or dimensions disagree")
            image.load()
        if _hash(r.manifest) != r.digest or _hash(r.source) != r.source_sha or _hash(r.plugin) != r.plugin_sha:
            raise ValueError("OpenFX source/plugin/manifest changed during render")
        receipt = {"schema": RECEIPT_SCHEMA, "manifest_sha256": r.digest,
                   "plugin_sha256": r.plugin_sha, "source_sha256": r.source_sha,
                   "output_sha256": report["sha256"], "plugin": report["evidence"],
                   "frame": r.frame, "fps": str(r.fps), "seconds": str(r.seconds),
                   "dimensions": [r.width, r.height], "mode": r.mode,
                   "source_transfer": "sRGB", "source_alpha": "straight" if r.mode == "RGBA8" else "opaque",
                   "native_plugin_execution": True, "licence_verified_externally": False,
                   "production_approved": False}
        note = Path(owned) / "receipt.json"
        note.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", "utf-8")
        try:
            for source, destination in ((temp, target), (note, receipt_path)):
                if destination.exists() or destination.is_symlink():
                    raise ValueError("OpenFX output appeared while publishing")
                os.link(source, destination)  # atomically publish no-overwrite
                created.append(destination)
            return receipt
        except BaseException:
            for item in reversed(created):
                item.unlink(missing_ok=True)
            raise
