"""Select an installed projectM CMake toolchain without guessing the VS year.

Invoked by Run-FullPresetAnalysis.ps1 after locating CMake and Ninja. Only the
selected -G option is written to stdout; diagnostics go to stderr. A previous
incompatible CMake *build* directory is archived, never destroyed. Downloaded
models, installed presets and the pinned projectM source are not modified.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4


# vswhere's installationVersion major, not a guessed release based on its presence.
VISUAL_STUDIO = {
    18: "Visual Studio 18 2026",
    17: "Visual Studio 17 2022",
    16: "Visual Studio 16 2019",
}
VC_TOOLS = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"


class ToolchainError(RuntimeError):
    """A reproducible projectM compiler or CMake generator is unavailable."""


def available_generators(cmake_help: str) -> set[str]:
    """Read actual `cmake --help` output, not CMake version assumptions."""
    choices = (*VISUAL_STUDIO.values(), "Ninja")
    return {
        choice
        for choice in choices
        if re.search(r"(?m)^\s*\*?\s*" + re.escape(choice) + r"\s*=", cmake_help)
    }


def visual_studio_versions(instances: object) -> list[int]:
    """Return only VS installations reported to include the VC C++ toolset."""
    if not isinstance(instances, list):
        raise ToolchainError("vswhere returned something other than a JSON installation list")
    versions = set()
    for instance in instances:
        if not isinstance(instance, dict):
            continue
        version = instance.get("installationVersion")
        location = instance.get("installationPath")
        if not isinstance(version, str) or not isinstance(location, str) or not location.strip():
            continue
        if match := re.match(r"^(\d+)\.", version):
            versions.add(int(match.group(1)))
    return sorted(versions, reverse=True)


def ninja_compiler_ready(which=shutil.which, env=None) -> bool:
    """Ninja requires an active C and C++ toolchain, not just ninja.exe."""
    env = os.environ if env is None else env
    mingw = bool(which("gcc.exe") and which("g++.exe"))
    llvm = bool(which("clang.exe") and which("clang++.exe"))
    # cl.exe alone is not enough: it must be running in a developer environment.
    msvc = bool(which("cl.exe") and which("link.exe") and env.get("INCLUDE") and env.get("LIB"))
    return mingw or llvm or msvc


def select_generator(cmake_help: str, instances: object, *, compiler_ready: bool) -> str:
    """Prefer installed and CMake-supported Visual Studio; otherwise real Ninja."""
    available = available_generators(cmake_help)
    versions = visual_studio_versions(instances)
    for version in versions:
        generator = VISUAL_STUDIO.get(version)
        if generator in available:
            return generator
    if "Ninja" in available and compiler_ready:
        return "Ninja"
    if 18 in versions and "Visual Studio 18 2026" not in available:
        raise ToolchainError(
            "Visual Studio 2026 C++ tools are installed, but this CMake does not support "
            "'Visual Studio 18 2026'. Upgrade to CMake 4.2 or newer "
            "(winget upgrade --id Kitware.CMake), then rerun."
        )
    if versions:
        raise ToolchainError(
            f"Detected Visual Studio C++ version(s) {versions}, but CMake has no matching "
            "generator. Update CMake, or open a supported compiler developer prompt for Ninja."
        )
    raise ToolchainError(
        "No Visual Studio C++ installation with VC tools was detected, and Ninja has no "
        "usable C/C++ compiler. Install Visual Studio C++ Build Tools or configure "
        "a supported compiler developer prompt."
    )


def vswhere_location(env=None, exists=None) -> Path | None:
    env = os.environ if env is None else env
    exists = Path.is_file if exists is None else exists
    candidates = []
    for key in ("ProgramFiles(x86)", "ProgramFiles"):
        if root := env.get(key):
            candidates.append(Path(root) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe")
    discovered = shutil.which("vswhere.exe")
    if discovered:
        candidates.append(Path(discovered))
    return next((candidate for candidate in candidates if exists(candidate)), None)


def discovered_instances(vswhere: Path | None) -> list[dict]:
    if vswhere is None:
        return []
    result = subprocess.run(
        [str(vswhere), "-products", "*", "-requires", VC_TOOLS, "-format", "json"],
        capture_output=True, text=True, errors="replace", check=False,
    )
    if result.returncode:
        raise ToolchainError("vswhere could not enumerate installed Visual Studio C++ tools: "
                             + result.stderr.strip()[:400])
    try:
        return json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ToolchainError("vswhere returned malformed installation metadata") from exc


def prior_generator(cache: Path) -> str | None:
    if not cache.is_file():
        return None
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("CMAKE_GENERATOR:INTERNAL="):
            return line.split("=", 1)[1].strip()
    return None


def archive_incompatible_build(runtime: Path, new_generator: str) -> Path | None:
    """Keep a previous failed build for diagnosis when changing generator."""
    build = runtime / "build"
    if build.is_symlink():
        raise ToolchainError("Refusing to manage a symlinked projectM build directory")
    current = prior_generator(build / "CMakeCache.txt")
    if not current or current == new_generator:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    archived = runtime / f"build-previous-{stamp}-{uuid4().hex[:8]}"
    if archived.exists():
        raise ToolchainError("Refusing to replace an existing projectM build archive")
    build.rename(archived)
    return archived


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cmake", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path,
                        help="Existing project-local models/runtimes/projectm folder")
    args = parser.parse_args(argv)
    try:
        result = subprocess.run([str(args.cmake), "--help"], capture_output=True,
                                text=True, errors="replace", check=False)
        if result.returncode:
            raise ToolchainError("Could not query available generators from CMake: "
                                 + result.stderr.strip()[:400])
        installed = discovered_instances(vswhere_location())
        selected = select_generator(result.stdout, installed, compiler_ready=ninja_compiler_ready())
        archive_incompatible_build(args.runtime, selected)
        # Only the generator is printed on stdout. PowerShell 5.1 can treat
        # native stderr as a terminating error under ErrorActionPreference=Stop.
        print("-G" + selected)
        return 0
    except (OSError, ToolchainError) as exc:
        print(f"projectM toolchain setup: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
