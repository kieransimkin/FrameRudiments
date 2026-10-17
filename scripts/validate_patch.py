"""Validate a Git patch against a disposable worktree of an exact baseline.

Usage: python scripts/validate_patch.py path/to/change.patch --base-ref main

Neither the current checkout nor its index is modified, even on failure.
"""

import argparse
from pathlib import Path
import subprocess
import tempfile


def _git(directory, *args):
    result = subprocess.run(
        ["git", *map(str, args)], cwd=directory, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"git {' '.join(map(str, args))} failed ({result.returncode}):\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def validate_patch(repository, patch, base_ref="main", expected_sha=None):
    """Prove a patch applies, reverses, and contains no whitespace errors.

    Returns the baseline SHA and changed paths. The caller's checkout and index
    are never modified; a freshly created detached worktree is used instead.
    """
    repository = Path(repository).resolve()
    patch = Path(patch).resolve()
    if not patch.is_file():
        raise ValueError(f"Patch not found: {patch}")
    root = Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()
    baseline = _git(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if expected_sha and baseline != expected_sha:
        raise ValueError(
            f"Baseline changed: expected {expected_sha}, found {baseline} ({base_ref}). "
            "Update/rebuild the patch against current main."
        )

    # A sibling avoids mutating or requiring a clean working tree in the caller.
    worktree = Path(tempfile.mkdtemp(prefix="fr-patch-validation-", dir=root.parent))
    attached = False
    try:
        _git(root, "worktree", "add", "--detach", str(worktree), baseline)
        attached = True
        _git(worktree, "apply", "--check", "--index", str(patch))
        _git(worktree, "apply", "--index", str(patch))
        _git(worktree, "diff", "--cached", "--check")
        changed = _git(worktree, "diff", "--cached", "--name-only").splitlines()
        if not changed:
            raise ValueError("Patch contains no staged changes after application")
        _git(worktree, "apply", "--reverse", "--check", "--index", str(patch))
        return baseline, changed
    finally:
        if attached:
            try:
                _git(root, "worktree", "remove", "--force", str(worktree))
            finally:
                if worktree.exists():
                    try:
                        worktree.rmdir()
                    except OSError:
                        pass
        else:
            worktree.rmdir()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch", type=Path, help="Git-format patch to validate")
    parser.add_argument("--base-ref", default="main", help="Local baseline ref (default: main)")
    parser.add_argument("--expected-sha", help="Optional exact baseline SHA guard")
    args = parser.parse_args(argv)
    try:
        sha, changed = validate_patch(Path.cwd(), args.patch, args.base_ref, args.expected_sha)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"PATCH VALIDATION FAILED: {exc}\n")
    print(f"PASS: {len(changed)} files apply and reverse cleanly against {sha}")
    for name in changed:
        print(f"  {name}")


if __name__ == "__main__":
    main()
