"""Install the exact visual-analysis release wheels recorded in analysis-releases.json.

No inference or model download. Packages are installed without their unrelated
GUI/face dependencies. The owning project's models/ holds downloaded wheels.
"""
from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

REPOSITORIES = {"pixelcue": "kieransimkin/PixelCue", "prismmoves": "kieransimkin/PrismMoves"}


def file_hash(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_lock(document):
    if document.get("schema") != "framerudiments.analysis-releases.v1":
        raise ValueError("Unsupported analysis release lock")
    rows = document.get("packages", [])
    if len(rows) != 2 or {p.get("name") for p in rows} != set(REPOSITORIES):
        raise ValueError("Expected exactly the PixelCue and PrismMoves releases")
    for row in rows:
        name, version = row["name"], row.get("version", "")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("Only final release versions may be pinned")
        filename = f"{name}-{version}-py3-none-any.whl"
        repository = REPOSITORIES[name]
        expected = f"https://github.com/{repository}/releases/download/v{version}/{filename}"
        if (row.get("filename") != filename or row.get("url") != expected
                or row.get("repository") != repository or row.get("tag") != "v" + version):
            raise ValueError("Release URL, filename or repository does not match the lock")
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", "")):
            raise ValueError("A real release wheel SHA-256 is required")
        if not re.fullmatch(r"[0-9a-f]{40}", row.get("commit", "")):
            raise ValueError("A resolved release commit is required")
    return rows


def validate_wheel(path, row):
    if file_hash(path) != row["sha256"]:
        raise ValueError(f"Checksum mismatch: {Path(path).name}")
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError("Wheel metadata is missing or ambiguous")
        metadata = email.message_from_bytes(archive.read(names[0]))
        if metadata["Name"].lower() != row["name"] or metadata["Version"] != row["version"]:
            raise ValueError("Wheel identity differs from the release lock")


def acquire_wheel(row, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / row["filename"]
    if path.exists():
        validate_wheel(path, row)
        return path
    # Retain a failed transfer for inspection, but never treat it as a valid wheel.
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".partial", delete=False) as stream:
        partial = Path(stream.name)
        request = urllib.request.Request(row["url"], headers={"User-Agent": "FrameRudiments-release-installer"})
        with urllib.request.urlopen(request, timeout=120) as response:
            if not response.url.startswith("https://"):
                raise ValueError("Non-HTTPS release redirect rejected")
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > 64 * 1024**2:
                    raise ValueError("Analysis wheel exceeds the 64 MiB safety limit")
                stream.write(block)
    validate_wheel(partial, row)
    # An existing valid copy wins a concurrent download; never overwrite a mismatch.
    if path.exists():
        validate_wheel(path, row)
        partial.unlink()
    else:
        partial.rename(path)
    return path


def install(root, python, *, uv=None, sequence=False):
    root = Path(root).resolve()
    # Resolving a POSIX venv's python symlink would select its base interpreter
    # and install into the wrong environment. Preserve the executable pathname.
    python = Path(os.path.abspath(python))
    if not python.is_file():
        raise ValueError("Target Python executable is missing")
    rows = validate_lock(json.loads((root / "analysis-releases.json").read_text(encoding="utf-8")))
    cache = root / "models" / "release-wheels"
    if not cache.resolve().is_relative_to(root):
        raise ValueError("Wheel cache must remain inside the owning project")
    wheels = [(row, acquire_wheel(row, cache)) for row in rows]
    requirements = cache / "locked-analysis-install.txt"
    requirements.write_text("\n".join(
        f'{row["name"]} @ {path.as_uri()} --hash=sha256:{row["sha256"]}' for row, path in wheels
    ) + "\n", encoding="utf-8")
    executable = uv or shutil.which("uv")
    if executable:
        command = [str(executable), "pip", "install", "--python", str(python)]
    else:
        subprocess.run([str(python), "-m", "ensurepip", "--upgrade"], check=True)
        command = [str(python), "-m", "pip", "install"]
    subprocess.run([*command, "--no-deps", "--force-reinstall", "--require-hashes", "-r", str(requirements)], check=True)
    if sequence:
        # OpenCV is a bounded runtime dependency, not a neural checkpoint. This
        # lock pins the two project wheels, not the complete transitive environment.
        subprocess.run([*command, "opencv-python-headless>=4.10,<5"], check=True)
    check = (
        "from importlib.metadata import version; "
        + "; ".join(f"assert version({r['name']!r}) == {r['version']!r}" for r in rows)
        + "; from pixelcue.visual_sequence import analyse_sequences; "
        "from prismmoves.preset_pipeline import analyse_capture_manifest"
    )
    if sequence:
        check += "; import cv2"
    subprocess.run([str(python), "-c", check], check=True)
    print("Verified pinned analysis packages: " + ", ".join(f"{r['name']} {r['version']}" for r in rows), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--uv")
    parser.add_argument("--with-sequence", action="store_true")
    args = parser.parse_args(argv)
    try:
        install(args.root, args.python, uv=args.uv, sequence=args.with_sequence)
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"Analysis release installation stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
