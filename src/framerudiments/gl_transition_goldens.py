"""M9.5: reproducible, reviewable RGBA8 reference cases for pinned GL transitions.

The PNGs are independently generated host references, not evidence of GPU
execution. Mandatory EGL tests compare the *actual* shader pixels against them.
No input audio, private project media, wall clock, network or GPU is needed.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from PIL import Image

from .gl_shader_guard import POLICY_VERSION

SCHEMA = "framerudiments.gl-transition-goldens.v1"
SOURCE_SCHEMA = "framerudiments.gl-transition-pattern.v1"
SIZE = (64, 48)
MANIFEST = Path("docs/media/gl-goldens/manifest.json")

# IDs are stable addresses: never derive approved fixtures from a glob order.
CASES = (
    ("crossfade-quarter", ("gl-transition-crossfade.glsl",), 0.25, 1.25, 30),
    ("crossfade-half", ("gl-transition-crossfade.glsl",), 0.5, 1.25, 30),
    ("reinforce-two-pass", ("gl-transition-crossfade.glsl", "gl-transition-reinforce.glsl"), 0.5, 1.25, 30),
    ("timed-split-before", ("gl-transition-timed-split.glsl",), 0.5, 0.0, 0),
    ("timed-split-after", ("gl-transition-timed-split.glsl",), 0.5, 1.25, 30),
)


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def synthetic_sources():
    """Opaque, exact-multiple-of-16 pixels avoid undefined 8-bit tie handling.

    Repeated horizontal/vertical ramp patterns cover image orientation, channel
    permutation and branch immutability without depending on PIL draw methods.
    """
    width, height = SIZE
    outgoing = bytearray()
    incoming = bytearray()
    for y in range(height):
        for x in range(width):
            outgoing.extend(((x % 16) * 16, (y % 16) * 16, ((x + y) % 16) * 16, 255))
            incoming.extend(((15 - x % 16) * 16, ((3 * x + 5 * y) % 16) * 16,
                             ((2 * x + 7 * y) % 16) * 16, 255))
    return Image.frombytes("RGBA", SIZE, bytes(outgoing)), Image.frombytes("RGBA", SIZE, bytes(incoming))


def reference_image(case_id: str):
    """Independent integer reference for *these exact* reviewed GLSL fixtures.

    Only the two crossfade shaders use interpolation. Opaque inputs contain
    multiples of 16, so quarter/half/three-quarter operations have exact 8-bit
    results with no floating-point rounding ambiguity on conforming RGBA8 GL.
    """
    a, b = synthetic_sources()
    left, right = a.tobytes(), b.tobytes()
    if case_id in {"crossfade-quarter", "crossfade-half", "reinforce-two-pass"}:
        numerator = {"crossfade-quarter": 1, "crossfade-half": 2, "reinforce-two-pass": 3}[case_id]
        data = bytes(((4 - numerator) * x + numerator * y) // 4 for x, y in zip(left, right, strict=True))
    elif case_id == "timed-split-before":
        data = left
    elif case_id == "timed-split-after":
        # The GL fixture selects source B for the right half only when explicit
        # TIME >= 1 and FRAMEINDEX >= 1. No implicit video-frame history.
        stride = SIZE[0] * 4
        data = b"".join(
            left[y * stride:y * stride + SIZE[0] // 2 * 4] +
            right[y * stride + SIZE[0] // 2 * 4:(y + 1) * stride]
            for y in range(SIZE[1])
        )
    else:
        raise ValueError(f"Unknown GL golden case: {case_id}")
    return Image.frombytes("RGBA", SIZE, data)


def shader_config(case: dict, root: Path):
    """Construct the regular production ABI, checked against the manifest pin."""
    passes = case["shaders"]
    for entry in passes:
        path = (root / entry["path"]).resolve()
        if not path.is_file() or _digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError(f"Pinned GL golden shader differs: {entry['path']}")
    return {
        "transition_style": "gl_transition", "trusted_shader": True,
        "shader_path": str((root / passes[0]["path"]).resolve()),
        "shader_sha256": passes[0]["sha256"],
        "shader_passes": [
            {"shader_path": str((root / item["path"]).resolve()),
             "shader_sha256": item["sha256"]} for item in passes[1:]
        ],
        "progress": case["progress"], "time_seconds": case["time_seconds"],
        "time_delta_seconds": case["time_delta_seconds"],
        "frame_index": case["frame_index"],
    }


def verify_manifest(root: Path):
    """Fail closed on metadata, source, image or pixel changes; no GL required."""
    root = Path(root).resolve()
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    a, b = synthetic_sources()
    if (set(manifest) != {"schema", "reference_kind", "source_schema", "size", "policy", "sources", "cases"}
            or manifest["schema"] != SCHEMA or manifest["reference_kind"] != "deterministic_host_reference_not_gpu_capture"
            or manifest["source_schema"] != SOURCE_SCHEMA or manifest["size"] != list(SIZE)
            or manifest["policy"] != POLICY_VERSION
            or manifest["sources"] != {"outgoing_rgba_sha256": _digest(a.tobytes()),
                                       "incoming_rgba_sha256": _digest(b.tobytes())}):
        raise ValueError("GL golden manifest or source contract changed")
    if [entry["id"] for entry in manifest["cases"]] != [case[0] for case in CASES]:
        raise ValueError("GL golden cases must match the reviewed order and IDs")
    for row, (case_id, shaders, progress, time_seconds, frame_index) in zip(manifest["cases"], CASES, strict=True):
        paths = ["examples/" + name for name in shaders]
        if (set(row) != {"id", "shaders", "progress", "time_seconds", "time_delta_seconds",
                         "frame_index", "golden", "png_sha256", "rgba_sha256"}
                or [item["path"] for item in row["shaders"]] != paths
                or row["progress"] != progress or row["time_seconds"] != time_seconds
                or row["frame_index"] != frame_index
                or row["time_delta_seconds"] != 1 / 24
                or row["golden"] != f"docs/media/gl-goldens/{case_id}.png"):
            raise ValueError(f"GL golden metadata changed for {case_id}")
        shader_config(row, root)
        gold_path = root / row["golden"]
        if not gold_path.is_file() or _digest(gold_path.read_bytes()) != row["png_sha256"]:
            raise ValueError(f"GL golden PNG SHA-256 mismatch for {case_id}")
        with Image.open(gold_path) as png:
            png.load()
            if png.format != "PNG" or png.mode != "RGBA" or png.size != SIZE:
                raise ValueError(f"GL golden PNG format mismatch for {case_id}")
            expected = reference_image(case_id)
            if png.tobytes() != expected.tobytes() or _digest(png.tobytes()) != row["rgba_sha256"]:
                raise ValueError(f"GL golden RGBA pixel mismatch for {case_id}")
    return manifest
