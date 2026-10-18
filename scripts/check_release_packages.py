"""Verify source/module version agreement and required release archive contents.

Uses only the standard library; never imports the renderer or downloads models.
Run from a checkout with --dist dist before publishing wheel and source archives.
"""
from __future__ import annotations

import argparse
import ast
import email
from pathlib import Path, PurePosixPath
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SOURCE = (
    "pyproject.toml", "MANIFEST.in", "analysis-releases.json", "RELEASE_NOTES.md",
    "scripts/Run-PresetBehaviourAnalysis.ps1", "scripts/Run-FullPresetAnalysis.ps1",
    "scripts/install_analysis_releases.py", "scripts/ensure_projectm_runtime.py",
    "scripts/preset_behaviour_pipeline.py", "scripts/check_release_packages.py",
)


def module_version(data: bytes) -> str:
    assignments = []
    for node in ast.parse(data).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            assignments.append(ast.literal_eval(node.value))
    if len(assignments) != 1 or not isinstance(assignments[0], str):
        raise ValueError("Expected one literal __version__ assignment")
    return assignments[0]


def source_version(root: Path = ROOT) -> str:
    info = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if info["name"] != "framerudiments":
        raise ValueError("Not a FrameRudiments source tree")
    current = module_version((root / "src/framerudiments/__init__.py").read_bytes())
    if current != info["version"]:
        raise ValueError(f"Source/module versions disagree: {info['version']} versus {current}")
    return current


def required_source_files(root: Path) -> set[str]:
    names = set(REQUIRED_SOURCE)
    for folder, patterns in ((root / "scripts", ("*.py", "*.ps1")),
                             (root / "src/framerudiments", ("*.py",))):
        for pattern in patterns:
            names.update(path.relative_to(root).as_posix() for path in folder.rglob(pattern))
    missing = sorted(name for name in names if not (root / name).is_file())
    if missing:
        raise ValueError("Missing release source files: " + ", ".join(missing))
    return names


def _check_members(names: list[str]) -> None:
    if len(names) != len(set(names)):
        raise ValueError("Duplicate archive members")
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("Unsafe archive member: " + name)
        if "models" in path.parts or "visualiser-analysis" in path.parts:
            raise ValueError("Local models or analysis results must not be packaged")


def _check_metadata(data: bytes, version: str) -> None:
    metadata = email.message_from_bytes(data)
    if metadata["Name"] != "framerudiments" or metadata["Version"] != version:
        raise ValueError("Distribution metadata does not match the release source")


def check_packages(root: Path, dist: Path) -> dict[str, str]:
    root, dist = Path(root).resolve(), Path(dist).resolve()
    version = source_version(root)
    wheel = dist / f"framerudiments-{version}-py3-none-any.whl"
    sdist = dist / f"framerudiments-{version}.tar.gz"
    if set(dist.glob("*.whl")) != {wheel} or set(dist.glob("*.tar.gz")) != {sdist}:
        raise ValueError("Expected exactly one matching wheel and source distribution")
    required = required_source_files(root)
    with zipfile.ZipFile(wheel) as archive:
        _check_members(archive.namelist())
        metas = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metas) != 1:
            raise ValueError("Missing or ambiguous wheel metadata")
        _check_metadata(archive.read(metas[0]), version)
        for source in sorted(name for name in required if name.startswith("src/")):
            member = source.removeprefix("src/")
            if member not in archive.namelist() or archive.read(member) != (root / source).read_bytes():
                raise ValueError("Wheel source differs or is missing: " + member)
    prefix = f"framerudiments-{version}"
    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getmembers()
        _check_members([m.name for m in members])
        if any(not (m.isfile() or m.isdir()) for m in members):
            raise ValueError("Links and special files are forbidden in the source archive")
        by_name = {m.name: m for m in members}
        for source in sorted(required | {"PKG-INFO"}):
            member = by_name.get(prefix + "/" + source)
            if member is None or not member.isfile():
                raise ValueError("Source archive is missing: " + source)
            if member.size > 16 * 1024**2:
                raise ValueError("Required source member exceeds the inspection limit")
            with archive.extractfile(member) as stream:
                data = stream.read()
            if source == "PKG-INFO":
                _check_metadata(data, version)
            elif data != (root / source).read_bytes():
                raise ValueError("Packaged source differs: " + source)
    return {"version": version, "wheel": wheel.name, "sdist": sdist.name}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args(argv)
    version = source_version(args.root)
    if args.dist is not None:
        check_packages(args.root, args.dist)
    print(f"FrameRudiments {version}: source/module identity and requested package checks passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"Release package verification failed: {exc}") from exc
