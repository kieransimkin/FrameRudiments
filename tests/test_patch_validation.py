"""Regression: validate shipped patches on a real detached Git worktree."""

from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_patch import validate_patch  # noqa: E402


def git(directory, *args):
    return subprocess.run(["git", *args], cwd=directory, check=True,
                          capture_output=True, text=True).stdout.strip()


def sample_repository(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Patch QA")
    git(repo, "config", "user.email", "qa@example.invalid")
    (repo / "existing.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    git(repo, "add", "existing.py")
    git(repo, "commit", "-qm", "baseline")
    baseline = git(repo, "rev-parse", "HEAD")
    (repo / "existing.py").write_text("x = 1\ny = 3\nz = 3\n", encoding="utf-8")
    (repo / "new.py").write_text("value = 4\n", encoding="utf-8")
    git(repo, "add", "-N", "new.py")
    patch = tmp_path / "change.patch"
    patch.write_bytes(subprocess.run(["git", "diff", "--binary"], cwd=repo,
                                      check=True, capture_output=True).stdout)
    return repo, patch, baseline


def test_worktree_validator_applies_and_reverses_without_touching_caller(tmp_path):
    repo, patch, baseline = sample_repository(tmp_path)
    status_before = git(repo, "status", "--porcelain")
    sha, paths = validate_patch(repo, patch, "HEAD", expected_sha=baseline)
    assert sha == baseline
    assert paths == ["existing.py", "new.py"]
    assert git(repo, "status", "--porcelain") == status_before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["change.patch", "repository"]


def test_worktree_validator_rejects_stale_base_and_does_not_touch_caller(tmp_path):
    repo, patch, baseline = sample_repository(tmp_path)
    status_before = git(repo, "status", "--porcelain")
    with pytest.raises(ValueError, match="Baseline changed"):
        validate_patch(repo, patch, "HEAD", expected_sha="0" * 40)
    assert git(repo, "status", "--porcelain") == status_before
    assert git(repo, "rev-parse", "HEAD") == baseline


def test_worktree_validator_rejects_nonmatching_patch(tmp_path):
    repo, patch, baseline = sample_repository(tmp_path)
    damaged = tmp_path / "bad.patch"
    damaged.write_text(patch.read_text().replace("-y = 2", "-y = 999"), encoding="utf-8")
    status_before = git(repo, "status", "--porcelain")
    with pytest.raises(RuntimeError, match="apply"):
        validate_patch(repo, damaged, "HEAD", expected_sha=baseline)
    assert git(repo, "status", "--porcelain") == status_before
