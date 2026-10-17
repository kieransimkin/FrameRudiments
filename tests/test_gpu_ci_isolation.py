"""Prevent reintroduction of EGL/GLX context mixing in GPU acceptance.

OpenGL mesh tests use a standalone ModernGL context; projectM uses GLFW/GLX.
Both suites still run on the real GPU CI runner and must not be skipped.
"""

from pathlib import Path


CI = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


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

    command = "xvfb-run -a python -X faulthandler -m pytest -vv -s"
    assert mesh.count(command) == 1
    assert legacy.count(command) == 1
    assert gpu_job.count(command) == 2
    assert "integration/test_music_spatial_gpu.py" in mesh
    assert "--ignore=integration/test_music_spatial_gpu.py" in legacy
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
