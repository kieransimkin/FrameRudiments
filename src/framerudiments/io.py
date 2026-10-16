"""Strict documents and content identities."""
import hashlib
import json
import math
from pathlib import Path


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    def bad(value):
        raise ValueError(f"Non-finite JSON number: {value}")
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"Duplicate JSON key: {key}")
            out[key] = value
        return out
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      parse_constant=bad, object_pairs_hook=unique)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                          encoding="utf-8")


def number(value, name, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"{name} outside [{minimum}, {maximum}]")
    return float(value)


def integer(value, name, *, minimum=0, maximum=2**31 - 1):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def fields(data, allowed, label):
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be an object")
    extra = set(data) - set(allowed)
    if extra:
        raise ValueError(f"Unknown {label} fields: {sorted(extra)}")


def file_ref(data, base):
    fields(data, {"path", "sha256"}, "file reference")
    path = (Path(base) / data["path"]).resolve()
    expected = data["sha256"]
    if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected):
        raise ValueError(f"A SHA-256 is required for {path}")
    actual = sha256(path)
    if actual != expected.lower():
        raise ValueError(f"Input hash mismatch: {path}")
    return {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}
