"""Prevent reintroduction of EGL/GLX context mixing in GPU acceptance.

OpenGL mesh tests use a standalone ModernGL context; projectM uses GLFW/GLX.
Both suites still run on the real GPU CI runner and must not be skipped.
"""

from pathlib import Path
import re
from shlex import split as shell_split


CI = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


def _xvfb_commands(section):
    return [
        shell_split(line.strip().removesuffix("\\").rstrip())
        for line in section.splitlines()
        if line.strip().startswith("xvfb-run ")
    ]


def test_gpu_mesh_and_projectm_run_in_separate_xvfb_processes():
    workflow = CI.read_text(encoding="utf-8")
    gpu_job = workflow.split("  visualiser-acceptance:\n", 1)[1]
    mesh = gpu_job.split("      - name: Native OpenGL mesh, shadow and volume acceptance", 1)[1]
    mesh, legacy = mesh.split(
        "      - name: Butterchurn, projectM, profiling and encoded QA", 1
    )
    legacy, verification = legacy.split(
        "      - name: Verify both GPU suites ran without skipped tests", 1
    )

    # Each suite owns a fresh display and Python process, while the required
    # wrapper owns its temporary-directory probe and unconditional cleanup.
    # Faulthandler is inherited through the job environment rather than -X.
    assert len(_xvfb_commands(gpu_job)) == 2
    assert "PYTHONFAULTHANDLER: '1'" in gpu_job
    for section in (mesh, legacy):
        commands = _xvfb_commands(section)
        assert len(commands) == 1
        command = commands[0]
        assert command[:3] == ["xvfb-run", "-a", "pwsh"]
        assert "-NoLogo" in command and "-NoProfile" in command
        assert command[command.index("-File") + 1] == "scripts/Invoke-Tests.ps1"
        assert command[command.index("-Python") + 1] == "python"
        assert "-vv" in command and "-s" in command
        assert "set -euo pipefail" in section
        assert "-m pytest" not in section
    assert "integration/test_music_spatial_gpu.py" in mesh
    assert "integration/test_fire_volume_gpu.py" in mesh
    mesh_inputs = set(re.findall(r"integration/test_\w+\.py", mesh))
    legacy_inputs = set(re.findall(r"integration/test_\w+\.py", legacy))
    assert mesh_inputs.isdisjoint(legacy_inputs)
    assert {"integration/test_visualisers_gpu.py", "integration/test_preview_stateful_gpu.py",
            "integration/test_preset_analysis.py", "integration/test_released_preset_analysis.py"} <= legacy_inputs
    assert "--junitxml=gpu-mesh-results.xml" in mesh
    assert "--junitxml=gpu-results.xml" in legacy
    assert "gpu-mesh-results.xml" in verification
    assert "gpu-results.xml" in verification
    assert "root.findall('.//skipped')" in verification


def test_gpu_acceptance_artifact_keeps_both_logs():
    workflow = CI.read_text(encoding="utf-8")
    gpu_job = workflow.split("  visualiser-acceptance:\n", 1)[1]
    artifact = gpu_job.split("      - uses: actions/upload-artifact@v6", 1)[1]
    for path in (
        "gpu-mesh-results.xml",
        "gpu-mesh-console.log",
        "gpu-results.xml",
        "gpu-console.log",
    ):
        assert path in artifact


def test_release_cpu_gate_matches_the_validated_native_dependency_boundary():
    workflow = CI.read_text(encoding="utf-8")
    release = CI.with_name("release.yml").read_text(encoding="utf-8")
    cpu = workflow.split("  render-tests:\n", 1)[1].split("  effect-graph-parity:\n", 1)[0]
    def excluded(text):
        return set(re.findall(r"--ignore=(tests/test_[\w]+\.py)", text))
    assert excluded(cpu) == excluded(release)
    assert "tests/test_gpu_spatial_aov_m12.py" in excluded(cpu)
    native = CI.with_name("colour-aov-m12.yml").read_text(encoding="utf-8")
    assert "tests/test_gpu_spatial_aov_m12.py" in native
    assert 'FRAMERUDIMENTS_REQUIRE_GPU_AOV' in native
    for text in (cpu, release):
        assert ".[dev,colour_aov,timeline_otio,vision]" in text
        assert "'onnx>=1.17,<2'" in text
        assert "scripts/Invoke-Tests.ps1 -Python python -q" in text
        assert "assert not r.findall('.//skipped')" in text
