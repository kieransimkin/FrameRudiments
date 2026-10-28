# Repaired FrameRudiments v16: choose cumulative v14 -> v16 or incremental v15 -> v16.
# Run from the repository root. Never resets, cleans or discards work.
param(
    [switch]$Commit,
    [switch]$SkipFullTests,
    [switch]$RequireGPU
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Exit([string]$Operation) {
    if ($LASTEXITCODE -ne 0) { throw "$Operation failed (exit $LASTEXITCODE). No commit was made." }
}

$GraphPath = 'src/framerudiments/effect_graph.py'
$RoadmapPath = 'docs/unified-effects-transitions-rendering-roadmap.md'
$GraphV14 = '716d9c886039d1ecc904c707cea1d2bc173c4ad8'
$RoadmapV14 = '4e076e310e11e93cd6979d471afe8d6277e2debe'
$GraphV15 = '6e8366fc6b0791cc97bf811abdf3f3a84c4e7000'
$RoadmapV15 = '887bc01c70480fdde3c057724f6c74371563261d'
$CompositorV14V15 = '28e150963c512970a7c4ecf5c65f263d7494b2bb'
$ProjectV14V15 = '56c4cd8da278cbd2c50950858ee77d747cb5fb67'

try {
    $Repo = (& git rev-parse --show-toplevel).Trim()
    Assert-Exit 'Find Git repository'
    if ([IO.Path]::GetFullPath((Get-Location).Path).TrimEnd('\','/') -ne
        [IO.Path]::GetFullPath($Repo).TrimEnd('\','/')) {
        throw 'Run this script from the FrameRudiments repository root.'
    }
    $GraphSha = (& git hash-object -- $GraphPath).Trim()
    Assert-Exit 'Inspect effect_graph.py'
    $RoadmapSha = (& git hash-object -- $RoadmapPath).Trim()
    Assert-Exit 'Inspect development roadmap'

    if ($GraphSha -eq $GraphV14 -and $RoadmapSha -eq $RoadmapV14) {
        $Baseline = 'v14'
        $PatchFile = 'framerudiments-effect-graph-v16-cumulative-v14-fixed.patch'
    } elseif ($GraphSha -eq $GraphV15 -and $RoadmapSha -eq $RoadmapV15) {
        $Baseline = 'v15'
        $PatchFile = 'framerudiments-effect-graph-v16-incremental-v15-fixed.patch'
    } else {
        throw @"
Unsupported starting revision; no files changed.
  effect_graph.py: $GraphSha
  roadmap:         $RoadmapSha
Expected a matching v14 or v15 pair. Your branch has different changes;
please share 'git rev-parse HEAD' and 'git status --short' for a proper rebase.
"@
    }

    # The compositor/project changes require identical inputs on both supported branches.
    $BaseHashes = [ordered]@{
        'src/framerudiments/compositor.py' = $CompositorV14V15
        'src/framerudiments/project.py' = $ProjectV14V15
    }
    foreach ($file in $BaseHashes.Keys) {
        $sha = (& git hash-object -- $file).Trim()
        Assert-Exit "Inspect $file"
        if ($sha -ne $BaseHashes[$file]) {
            throw "Compositor/project baseline has changed: $file is $sha. No files changed."
        }
    }

    $Patch = Join-Path $PSScriptRoot $PatchFile
    if (-not (Test-Path -LiteralPath $Patch)) { throw "Missing patch beside installer: $PatchFile" }
    Write-Host "Detected $Baseline; using $PatchFile" -ForegroundColor Cyan

    # Collect the exact patch paths, not a broad 'git add .' or an unrelated working tree.
    $Affected = @(& git apply --numstat -- $Patch | ForEach-Object {
        $parts = $_ -split "`t"
        if ($parts.Length -ne 3) { throw "Cannot parse patch numstat: $_" }
        $parts[2]
    })
    Assert-Exit 'Read patch metadata'
    if ($Affected.Count -eq 0) { throw 'Patch has no changes.' }

    & git diff --quiet -- $Affected
    if ($LASTEXITCODE -eq 1) { throw 'Uncommitted changes overlap the patch files. No files changed.' }
    Assert-Exit 'Check working-tree overlap'
    & git diff --cached --quiet -- $Affected
    if ($LASTEXITCODE -eq 1) { throw 'Staged changes overlap the patch files. No files changed.' }
    Assert-Exit 'Check staged overlap'

    Write-Host 'Checking complete patch and whitespace before modifying files...' -ForegroundColor Cyan
    & git apply --check --whitespace=error -- $Patch
    Assert-Exit 'Patch preflight'
    & git apply --whitespace=error -- $Patch
    Assert-Exit 'Patch application'
    & git diff --check
    Assert-Exit 'Whitespace validation'

    Write-Host 'Running CPU/reference graph regression tests...' -ForegroundColor Cyan
    & python -m pytest -q `
        tests/test_effect_graph.py `
        tests/test_effect_graph_mattes.py `
        tests/test_effect_graph_alpha_lineage.py `
        tests/test_effect_graph_alpha_visuals.py `
        tests/test_effect_graph_transitions.py `
        tests/test_effect_graph_modulation.py `
        tests/test_effect_graph_m3_modulation.py `
        tests/test_effect_graph_gpu_dispatch.py `
        tests/test_effect_graph_gpu_integration.py `
        tests/test_effect_graph_gpu_resources.py `
        tests/test_effect_graph_gpu_project.py `
        tests/test_effect_graph_project_integration.py
    Assert-Exit 'Focused regression tests'

    if ($RequireGPU) {
        Write-Host 'Requiring a functioning ModernGL context and real GPU parity...' -ForegroundColor Cyan
        & python -c 'from framerudiments.effect_graph_gpu import GLMatteBackend; g = GLMatteBackend(); print(g.capabilities); g.close()'
        Assert-Exit 'ModernGL context creation'
        & python -m pytest -q tests/test_effect_graph_gpu_parity.py
        Assert-Exit 'GPU parity tests'
    } else {
        Write-Host 'GPU parity NOT verified locally unless -RequireGPU is specified; dedicated EGL CI must run.'
    }

    if (-not $SkipFullTests) {
        & python -m pytest -q tests
        Assert-Exit 'Full Python tests'
    }
    if ($Commit) {
        & git add -- $Affected
        Assert-Exit 'Stage only files from patch'
        & git commit -m 'Complete M3 alpha lineage and add optional GPU matte/key backend'
        Assert-Exit 'Commit repaired v16'
    }
    Write-Host "Successfully applied repaired v16 over $Baseline." -ForegroundColor Green
} catch {
    Write-Error $_
    exit 1
}
