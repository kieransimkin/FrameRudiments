#Requires -Version 5.1
<#
.SYNOPSIS
Reference-free preset portraits, contact sheets, motion and controlled audio responses.
.EXAMPLE
.\scripts\Run-FullPresetAnalysis.ps1 -Limit 2
.EXAMPLE
.\scripts\Run-FullPresetAnalysis.ps1 -ReanalyseExisting -OutputDirectory '.\visualiser-analysis\audio-response-catalogue'
#>
[CmdletBinding()]
param(
    [ValidateRange(0,2147483647)][int]$Limit = 0,
    [ValidateRange(3,6)][int]$Trials = 3,
    [ValidateSet(5,10,15,20,30)][int]$CaptureFps = 15,
    [ValidateRange(64,1024)][int]$RenderEdge = 384,
    [ValidateRange(0,64)][int]$SemanticBudget = 24,
    [ValidateSet('cpu','cuda','mps')][string]$Device = 'cpu',
    [string]$OutputDirectory,
    [string]$AudioFile,
    [ValidateRange(0,86400)][double]$AudioStart = 0,
    [ValidateSet('butterchurn','projectm')][string]$Engine,
    [string[]]$PresetId,
    [UInt64]$OrderSeed = 0,
    [ValidateRange(1,86400)][int]$CaptureTimeout = 1800,
    [switch]$Extended,
    [switch]$TextureControls,
    [switch]$RetryFailed,
    [Alias('Reanalyse')][switch]$ReanalyseExisting,
    [switch]$NoSemantics,
    [switch]$AllowModelDownloads,
    [switch]$Status,
    [switch]$Verify
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$render = Join-Path $repo 'models\.venv-render\Scripts\python.exe'
$analysis = Join-Path $repo 'models\.venv-hybrid-analysis\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $analysis)) {
    throw 'Install the hybrid update bundle first (apply_hybrid_update.py). Existing model weights and stable analysis pins are preserved.'
}
# Model roots are always owned by this FrameRudiments checkout.
$env:PIXELCUE_MODELS_DIR = Join-Path $repo 'models'
$env:HF_HOME = Join-Path $repo 'models\.cache\huggingface'
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME 'hub'
$env:HUGGINGFACE_HUB_CACHE = $env:HF_HUB_CACHE
$env:TORCH_HOME = Join-Path $repo 'models\.cache\torch'
$env:XDG_CACHE_HOME = Join-Path $repo 'models\.cache'
if (Test-Path Env:PIXELCUE_MODEL_DIR) { Remove-Item Env:PIXELCUE_MODEL_DIR }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repo 'visualiser-analysis\full-catalogue\hybrid' }
if (-not [IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory = Join-Path (Get-Location).Path $OutputDirectory }
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if (-not $OutputDirectory.StartsWith((Join-Path $repo 'visualiser-analysis') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Output must remain inside this checkout visualiser-analysis directory.'
}
if ($NoSemantics) { $SemanticBudget = 0 }
& $analysis -I -c 'from prismmoves.hybrid_response import analyse; from pixelcue.visual_contact_sheet import export_contact_sheets; import cv2'
if ($LASTEXITCODE -ne 0) { throw 'Hybrid analysis environment is incomplete; rerun its bundle installer.' }
Write-Host 'HYBRID: numerical analysis -> contact sheets -> selected appearance -> complete preset paragraph' -ForegroundColor Cyan
if ($ReanalyseExisting -or $Status -or $Verify) {
    $command = 'reanalyse'
    if ($Status) { $command = 'status' }
    if ($Verify) { $command = 'verify' }
    $arguments = @('-I','-u','-m','prismmoves.hybrid_response',$command,'--out',$OutputDirectory,
        '--models-dir',(Join-Path $repo 'models'),'--budget',[string]$SemanticBudget,'--device',$Device,'--limit',[string]$Limit)
    if ($AllowModelDownloads) { $arguments += '--allow-download' }
    & $analysis @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Hybrid report operation failed. Captures were not rerendered or deleted.' }
    return
}
if (-not (Test-Path -LiteralPath $render)) { throw 'Missing project-local render Python.' }
# Setup-only bypasses the full-launcher dispatcher. This call never starts the
# legacy sweep and never recursively calls the hybrid launcher.
& (Join-Path $PSScriptRoot 'Run-FullPresetAnalysis.ps1') -Legacy -SkipSetup -InstallOnly
if ($LASTEXITCODE -ne 0) { throw 'Native renderer preflight failed.' }
$params = @((Join-Path $PSScriptRoot 'controlled_preset_analysis.py'),
    '--analysis-python',$analysis,'--analysis-module','prismmoves.hybrid_response',
    '--output',$OutputDirectory,'--limit',[string]$Limit,'--trials',[string]$Trials,
    '--capture-fps',[string]$CaptureFps,'--edge',[string]$RenderEdge,
    '--semantic-budget',[string]$SemanticBudget,'--device',$Device,'--timeout',[string]$CaptureTimeout)
if ($AudioFile) { $params += @('--audio',(Resolve-Path -LiteralPath $AudioFile).Path,'--audio-start',$AudioStart.ToString([Globalization.CultureInfo]::InvariantCulture)) }
if ($Engine) { $params += @('--engine',$Engine) }
foreach ($id in $PresetId) { $params += @('--id',$id) }
if ($PSBoundParameters.ContainsKey('OrderSeed')) { $params += @('--order-seed',[string]$OrderSeed) }
if ($Extended) { $params += '--extended' }
if ($TextureControls) { $params += '--texture-controls' }
if ($RetryFailed) { $params += '--retry-failed' }
if ($AllowModelDownloads) { $params += '--allow-model-downloads' }
& $render @params
if ($LASTEXITCODE -eq 2) { Write-Warning 'Some capture conditions are partial; inspect the saved reports.' }
elseif ($LASTEXITCODE -ne 0) { throw 'Hybrid analysis stopped; inspect the current preset analysis.log.' }
Write-Host ('Open: ' + (Join-Path $OutputDirectory 'index.html')) -ForegroundColor Green
