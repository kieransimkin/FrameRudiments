# Rebuild the historic bundle in a disposable Git worktree.
# Does not change any files on the current branch.
$ErrorActionPreference = 'Stop'
$repo = (git rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Run from a FrameRudiments Git checkout.' }
$bundle = Join-Path $repo 'FrameRudiments-phases1-4-restored.patch'
$repair = Join-Path $repo 'apply_framerudiments_bundle_v4.py'
$parser = Join-Path $repo 'rebuild_framerudiments_patch_v3.py'
foreach ($f in @($bundle,$repair,$parser)) { if (-not (Test-Path $f)) { throw "Missing $f; extract FrameRudiments-repair-v4.zip into repository root" } }
$baseline = (git -C $repo rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify HEAD.' }
$parent = Split-Path $repo -Parent
$worktree = Join-Path $parent ("FrameRudiments-repair-check-" + $PID)
$out = Join-Path $repo 'FrameRudiments-repaired-native.patch'
$added = $false
try {
    git -C $repo worktree add --detach $worktree $baseline
    if ($LASTEXITCODE -ne 0) { throw 'Could not create verification worktree' }
    $added = $true
    Push-Location $worktree
    try {
        python $repair $bundle --output 'FrameRudiments-repaired-native.patch'
        if ($LASTEXITCODE -ne 0) { throw 'Source transformation rejected; original repository unchanged' }
        git diff --check
        if ($LASTEXITCODE -ne 0) { throw 'Whitespace validation failed' }
        # The native diff must undo cleanly and then apply to the baseline.
        git apply --reverse --check 'FrameRudiments-repaired-native.patch'
        if ($LASTEXITCODE -ne 0) { throw 'Reverse validation failed' }
        git apply --reverse 'FrameRudiments-repaired-native.patch'
        if ($LASTEXITCODE -ne 0) { throw 'Could not reverse generated changes' }
        git apply --check 'FrameRudiments-repaired-native.patch'
        if ($LASTEXITCODE -ne 0) { throw 'Generated patch does not apply to exact baseline' }
        git apply 'FrameRudiments-repaired-native.patch'
        if ($LASTEXITCODE -ne 0) { throw 'Reapplication failed' }
        python -m compileall -q src/framerudiments tests
        if ($LASTEXITCODE -ne 0) { throw 'Python compilation failed' }
        Copy-Item 'FrameRudiments-repaired-native.patch' $out -Force
    } finally { Pop-Location }
    Write-Host "Native patch structurally verified against $baseline : $out"
    Write-Host 'No files in your original working tree were changed. Full tests/CI still required.'
} finally {
    if ($added) {
        # Worktree contains only disposable generated edits; never clean/reset the user's checkout.
        git -C $repo worktree remove --force $worktree
        if ($LASTEXITCODE -ne 0) { Write-Warning "Review and remove disposable worktree manually: $worktree" }
    }
}
