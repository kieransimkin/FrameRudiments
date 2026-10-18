"""Prevent mismatched runtime versions and incomplete analysis source releases."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "frame_release_packages", ROOT / "scripts/check_release_packages.py"
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def test_package_and_runtime_versions_agree():
    assert CHECK.source_version(ROOT)


def test_analysis_release_inputs_are_declared_for_the_source_archive():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    top_level = set()
    script_patterns = set()
    for line in manifest.splitlines():
        fields = line.split()
        if fields and fields[0] == "include":
            top_level.update(fields[1:])
        if fields[:2] == ["recursive-include", "scripts"]:
            script_patterns.update(fields[2:])
    assert {"analysis-releases.json", "RELEASE_NOTES.md"} <= top_level
    assert {"*.py", "*.ps1"} <= script_patterns
    assert "prune models" in manifest
    assert "prune visualiser-analysis" in manifest
    assert CHECK.required_source_files(ROOT)


def test_release_workflow_checks_packages_before_publication():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    guard = "python scripts/check_release_packages.py --dist dist"
    assert workflow.count(guard) == 1
    assert workflow.index("python -m build") < workflow.index(guard)
    assert workflow.index(guard) < workflow.index("gh release create")
