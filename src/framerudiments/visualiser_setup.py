"""Explicit, pinned optional runtime setup. Rendering itself never downloads tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from .io import sha256, write_json
from .projectm_backend import PROJECTM_COMMIT
from .visualisers import ASSETS

PROJECTM_EVAL_COMMIT = "22fb0cfd8f2dfbcd2b68f2443e7f44e19b32c09a"


def setup_butterchurn(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise ValueError("Install Node.js >=22.12 including npm first")
    for name in ("package.json", "package-lock.json"):
        target = directory / name
        if target.exists() and sha256(target) != sha256(ASSETS / name):
            raise ValueError(f"Existing runtime file differs: {target}; use a new directory")
        shutil.copyfile(ASSETS / name, target)
    subprocess.run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=directory, check=True)
    return dict(
        engine="butterchurn",
        version="2.6.7",
        runtime=str(directory),
        package_lock_sha256=sha256(directory / "package-lock.json"),
    )


def setup_projectm(directory, *, cmake_args=(), parallel=4):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source, build, install = directory / "source", directory / "build", directory / "install"
    if not source.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "https://github.com/projectM-visualizer/projectm.git",
                str(source),
            ],
            check=True,
        )
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", PROJECTM_COMMIT], check=True)
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True)
    if head != PROJECTM_COMMIT or dirty:
        raise ValueError(
            "projectM source is modified or differs from the required pin; use a new setup directory"
        )
    subprocess.run(
        ["git", "-C", str(source), "submodule", "update", "--init", "vendor/projectm-eval"], check=True
    )
    evaluator = source / "vendor/projectm-eval"
    eval_head = subprocess.check_output(["git", "-C", str(evaluator), "rev-parse", "HEAD"], text=True).strip()
    eval_dirty = subprocess.check_output(["git", "-C", str(evaluator), "status", "--porcelain"], text=True)
    if eval_head != PROJECTM_EVAL_COMMIT or eval_dirty:
        raise ValueError("projectM evaluator differs from the pinned submodule")
    subprocess.run(
        [
            "cmake",
            "-S",
            str(source),
            "-B",
            str(build),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DBUILD_SHARED_LIBS=ON",
            "-DENABLE_SYSTEM_PROJECTM_EVAL=OFF",
            "-DENABLE_PLAYLIST=OFF",
            "-DENABLE_SDL_UI=OFF",
            "-DBUILD_TESTING=OFF",
            f"-DCMAKE_INSTALL_PREFIX={install}",
            *cmake_args,
        ],
        check=True,
    )
    subprocess.run(
        ["cmake", "--build", str(build), "--config", "Release", "--parallel", str(parallel)], check=True
    )
    subprocess.run(["cmake", "--install", str(build), "--config", "Release"], check=True)
    candidates = [
        path
        for path in install.rglob("*")
        if path.is_file()
        and (
            path.name in {"libprojectM-4.dll", "projectM-4.dll"}
            or path.name.startswith("libprojectM-4.so")
            or path.name.startswith("libprojectM-4.")
            and path.suffix == ".dylib"
        )
    ]
    if not candidates:
        raise RuntimeError("Built projectM shared library was not found under the install directory")
    library = sorted(candidates, key=lambda path: (path.is_symlink(), len(path.name)))[0]
    receipt = dict(
        engine="projectm",
        version="4.2.0-dev",
        source_commit=head,
        eval_commit=eval_head,
        library_sha256=sha256(library),
        unmodified_upstream=True,
        upstream="https://github.com/projectM-visualizer/projectm",
    )
    write_json(library.parent / "framerudiments-projectm.json", receipt)
    return dict(receipt, library=str(library))


def runtime_pins():
    return dict(
        projectm=dict(source_commit=PROJECTM_COMMIT, eval_commit=PROJECTM_EVAL_COMMIT, version="4.2.0-dev"),
        butterchurn=dict(
            version="2.6.7",
            puppeteer_core="25.14.0",
            package_lock_sha256=sha256(ASSETS / "package-lock.json"),
        ),
        presets=["feedback", "kaleidoscope", "julia"],
    )


def format_result(result):
    return json.dumps(result, indent=2)
