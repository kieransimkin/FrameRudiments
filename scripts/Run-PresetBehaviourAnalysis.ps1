#Requires -Version 5.1
<#
.SYNOPSIS
Dense capture and temporal analysis, with a small semantic budget per preset.
.EXAMPLE
.\scripts\Run-PresetBehaviourAnalysis.ps1 -Limit 2
.EXAMPLE
.\scripts\Run-PresetBehaviourAnalysis.ps1 -ArtworkDirectory 'Z:\My Songs\Covers' -Limit 2
.EXAMPLE
.\scripts\Run-PresetBehaviourAnalysis.ps1 -DescribeExisting
#>
[CmdletBinding()]
param(
    [string]$Suite,
    [string]$ArtworkDirectory,
    [string]$Font,
    [ValidateRange(160,1024)][int]$RenderEdge = 384,
    [ValidateRange(5,60)][int]$CaptureFps = 15,
    [ValidateRange(0.1,60)][double]$Seconds = 3,
    [ValidateRange(0,64)][int]$SemanticBudget = 24,
    [ValidateRange(0,2147483647)][int]$Limit = 0,
    [ValidateSet('cpu','cuda','mps')][string]$Device = 'cpu',
    [string]$OutputDirectory,
    [switch]$RetryFailed,
    [switch]$DescribeExisting
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$render = Join-Path $repo 'models\.venv-render\Scripts\python.exe'
$analysis = Join-Path $repo 'models\.venv-analysis\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $analysis)) {
    throw 'Create the project-local environments with Run-FullPresetAnalysis.ps1 -InstallOnly, then install this update into the analysis environment.'
}
& $analysis -c 'from prismmoves.preset_pipeline import analyse_capture_manifest; from pixelcue.visual_sequence import analyse_sequences; import cv2'
if ($LASTEXITCODE -ne 0) {
    throw 'Install the updated PrismMoves and PixelCue sequence APIs (and OpenCV) into models\.venv-analysis first. Do not reinstall the old pinned analysis wheels afterwards.'
}
if ($DescribeExisting) {
    if (-not $OutputDirectory) {
        $OutputDirectory = Join-Path $repo ('visualiser-analysis\legacy-descriptions-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    }
    & $analysis -m prismmoves.preset_pipeline legacy `
        --reports (Join-Path $repo 'visualiser-analysis\full-catalogue\reports') `
        --out $OutputDirectory --budget $SemanticBudget
    if ($LASTEXITCODE -ne 0) { throw 'Existing-report replay failed; original evidence is unchanged.' }
    Write-Host ('Open: ' + (Join-Path $OutputDirectory 'index.html')) -ForegroundColor Green
    return
}
if (-not (Test-Path -LiteralPath $render)) { throw 'Project-local render Python is missing.' }
& $render (Join-Path $PSScriptRoot 'ensure_projectm_runtime.py') --repository $repo
if ($LASTEXITCODE -ne 0) { throw 'Project-local projectM runtime bundling failed.' }
# This registers the existing browser/FFmpeg/DLL paths without reinstalling the old
# PixelCue/PrismMoves wheels or downloading any models. Setup now bundles MinGW DLLs.
& (Join-Path $PSScriptRoot 'Run-FullPresetAnalysis.ps1') -SkipSetup -InstallOnly
if ($LASTEXITCODE -ne 0) { throw 'Renderer runtime preflight failed.' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repo 'visualiser-analysis\behaviour-catalogue' }
$params = @((Join-Path $PSScriptRoot 'preset_behaviour_pipeline.py'),
    '--render-python', $render, '--analysis-python', $analysis,
    '--output', $OutputDirectory, '--seconds', $Seconds.ToString([Globalization.CultureInfo]::InvariantCulture),
    '--capture-fps', [string]$CaptureFps, '--render-edge', [string]$RenderEdge,
    '--semantic-budget', [string]$SemanticBudget, '--limit', [string]$Limit, '--device', $Device)
if ($Suite) { $params += @('--suite', (Resolve-Path -LiteralPath $Suite).Path) }
if ($ArtworkDirectory) { $params += @('--artwork-directory', (Resolve-Path -LiteralPath $ArtworkDirectory).Path) }
if ($Font) { $params += @('--font', (Resolve-Path -LiteralPath $Font).Path) }
if ($RetryFailed) { $params += '--retry-failed' }
& $render @params
if ($LASTEXITCODE -ne 0) { throw 'Behaviour analysis ended with process failures; read the saved logs.' }
Write-Host ('Open: ' + (Join-Path $OutputDirectory 'index.html')) -ForegroundColor Green
