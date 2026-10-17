# FrameRudiments patch and integration workflow

## Why this exists

In October 2026, cumulative patches were assembled by concatenating independent
unified diffs against the same files. `git apply` rejected repeated `:1`
hunks. Subsequent patch-rebuilder scripts failed on duplicated source snippets,
incorrect `/dev/null` headers for added files, and context/line-ending drift.
**Do not reuse those generated cumulative patches as canonical merge artifacts.**

## Rules for all subsequent changes

1. **Start from a known branch and commit**, ideally a new branch based on the
   latest `main`. Record `git rev-parse HEAD` in the change notes. Never assume
   the recipient's working tree matches an old patch baseline.
2. **Modify actual source files in a real Git checkout.** Do not concatenate
   individual diffs to produce a cumulative patch. Combine changes in the
   checkout, then generate **one Git-native diff** with `git diff --binary`.
3. **Check source state first.** Inspect `git status --short`. Preserve all
   unrelated modifications; never silently reset, stash, clean, or delete files.
4. **Use a clean disposable worktree for patch validation.** Reapply the
   generated patch to the *exact* baseline and test it; merely parsing a patch
   or passing `git apply --stat` is insufficient.
5. **Validate quality and behaviour** with `ruff check .`, the full `pytest`
   suite (including Windows and Linux CI), and relevant integration/gallery
   tests. A syntax check is not a successful integration test.
6. **Keep compatibility:** StemLab produces source-bound pinned analytical data,
   DanceRudiments supplies native movement, and FrameRudiments renders it.
   No duplicated DSP or alternate motion semantics.
7. **Do not claim verified** before the patch applies cleanly and CI actually
   passes. Record the branch, commit, exact run URL, results, and known skips.
8. **Avoid committing generated patch files/ZIPs** into the product repository
   unless explicitly required. Prefer `docs/` for permanent procedure and
   release artifacts for distribution.

## Standard local workflow (PowerShell)

Before any change:

```powershell
$ErrorActionPreference = 'Stop'
git status --short
git switch main
git pull --ff-only
# Only do this if the working tree is clean; do not overwrite ongoing work.
git switch -c feature/visualisation-improvements
$baseline = git rev-parse HEAD
Write-Host "Base commit: $baseline"
```

After editing and running targeted tests:

```powershell
ruff check .
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace check failed' }
# Stage only intended files. Review `git status` before staging.
git status --short
```

For a **tracked-file-only** patch, create a single native diff:

```powershell
git diff --binary > FrameRudiments-changes.patch
```

**Important:** New untracked files are excluded from `git diff`. To include
new files without staging actual content, `git add -N -- <paths>` and then
`git diff --binary`; or stage intended changes and use
`git diff --cached --binary` for a complete patch. Review before doing either.
For PowerShell on Windows, avoid redirection encodings that produce UTF-16 or
an unwanted BOM; use Git-native output captured explicitly as UTF-8 where
necessary.

Validate against a separate clean worktree at `$baseline`:

```powershell
# Run from the original repository root; choose a fresh sibling directory.
$check = Join-Path (Split-Path (Get-Location)) 'FrameRudiments-patch-check'
git worktree add --detach $check $baseline
# Copy the patch file to the temporary worktree before applying it.
Copy-Item .\FrameRudiments-changes.patch (Join-Path $check 'changes.patch')
Push-Location $check
try {
    git apply --check .\changes.patch
    if ($LASTEXITCODE -ne 0) { throw 'Patch does not apply to baseline' }
    git apply .\changes.patch
    if ($LASTEXITCODE -ne 0) { throw 'Patch application failed' }
    git diff --check
    if ($LASTEXITCODE -ne 0) { throw 'Whitespace check failed' }
    # Install development dependencies and run tests for this checkout.
    python -m pip install -e '.[dev]'
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
}
finally { Pop-Location }
# After inspecting the temporary worktree, manually remove it with:
# git worktree remove <path>
```

Commit and push only after review and successful checks:

```powershell
git status --short
git add <explicit-files-or-directories>
git diff --cached --check
git commit -m "feat: improve visualisation coverage"
git push -u origin feature/visualisation-improvements
```

Check the actual GitHub Actions run for **that exact commit**. Do not mistake
success of another branch or older commit for proof of this change.

## Recovering the historical Phase 1–4 bundle

The original `FrameRudiments-phases1-4-restored.patch` is **not suitable for
`git apply` directly**. Repair utilities v1–v3 were unsuccessful. The v4 tool
`apply_framerudiments_bundle_v4.py` is a **candidate recovery tool**, not
verified as working on the recipient's checkout. Its proposed workflow applies
source transforms in memory, then uses Git to generate a real diff, with
rollback on validation failures. It must be reviewed and tested in a disposable
worktree **before** attempting to run it against work that matters.

Do not blindly force `git apply --reject`, do not use `git reset --hard` to
resolve conflicts, and do not accept a partial patch without tests and review.
If a match is ambiguous, stop and reconstruct that change manually from the
actual source and feature requirements.

## CI acceptance checklist

- All newly created files are included and import correctly.
- Existing project-format configurations remain backwards-compatible.
- Every registered renderer is exercised in the gallery and acceptance tests.
- Deterministic random-access and temporal subframe behavior are tested.
- New multi-stem input sources preserve StemLab provenance and hashes.
- New motion routes preserve DanceRudiments timing, including variable BPM.
- Compositing masks, stage-routing and scenes have isolated and combined tests.
- The Ubuntu/Windows and supported Python-version jobs complete successfully.
- README/feature documentation matches **implemented and verified** behavior.

## Required validation for future patches

The repository's `patch-contract` CI job exports a **single Git-native diff**
against its actual commit parent (or PR merge base), validates it in a detached
worktree using `scripts/validate_patch.py`, and publishes the tested patch as
`verified-git-apply-patch`. The validator is covered by
`tests/test_patch_validation.py` on Windows and Linux.

**For a patch intended for the recipient's current `main`:** generate it in a
full clone of *that exact* `main` commit, not using temporary source excerpts.
On Windows, use `git diff --binary --output=change.patch` rather than PowerShell
redirection. Run `python scripts/validate_patch.py change.patch --base-ref main
--expected-sha <40-character-SHA>` on that exact commit before distribution.
This validation guarantees clean application, not functional correctness;
CI and renderer tests are still required. If `main` moves, rebuild the patch.
