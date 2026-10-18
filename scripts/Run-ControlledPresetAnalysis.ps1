#Requires -Version 5.1
<#
.SYNOPSIS
Measure standalone preset effects and repeated audio responses, without reference captions.
.EXAMPLE
.\scripts\Run-ControlledPresetAnalysis.ps1 -Limit 2
.EXAMPLE
.\scripts\Run-ControlledPresetAnalysis.ps1 -Extended -TextureControls -Limit 2
.EXAMPLE
.\scripts\Run-ControlledPresetAnalysis.ps1 -AudioFile 'Z:\My Songs\track.wav' -Limit 2
#>
[CmdletBinding()]
param(
    [ValidateRange(0,2147483647)][int]$Limit = 2,
    [ValidateRange(3,6)][int]$Trials = 3,
    [ValidateSet(5,10,15,20,30)][int]$CaptureFps = 15,
    [ValidateRange(64,1024)][int]$RenderEdge = 384,
    [string]$OutputDirectory,
    [string]$AudioFile,
    [ValidateRange(0,86400)][double]$AudioStart = 0,
    [ValidateSet('butterchurn','projectm')][string]$Engine,
    [string[]]$PresetId,
    [switch]$Extended,
    [switch]$TextureControls,
    [switch]$RetryFailed,
    [switch]$Reanalyse
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$render = Join-Path $repo 'models\.venv-render\Scripts\python.exe'
$analysis = Join-Path $repo 'models\.venv-audio-effects\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $render) -or -not (Test-Path -LiteralPath $analysis)) {
    throw 'Install the controlled-analysis update first. The existing analysis environment is not replaced.'
}
& $analysis -I -c 'import cv2; from prismmoves.controlled_response import analyse'
if ($LASTEXITCODE -ne 0) { throw 'Controlled-response analysis API is not installed in the isolated environment.' }
# The existing skip-setup path locates FFmpeg/browser/Node and validates projectM.
# It performs no Python dependency or neural checkpoint installation.
& (Join-Path $PSScriptRoot 'Run-FullPresetAnalysis.ps1') -SkipSetup -InstallOnly
if ($LASTEXITCODE -ne 0) { throw 'Renderer preflight failed.' }
$params = @((Join-Path $PSScriptRoot 'controlled_preset_analysis.py'),
    '--analysis-python', $analysis, '--limit', [string]$Limit, '--trials', [string]$Trials,
    '--capture-fps', [string]$CaptureFps, '--edge', [string]$RenderEdge)
if ($OutputDirectory) { $params += @('--output',$OutputDirectory) }
if ($AudioFile) {
    $params += @('--audio',(Resolve-Path -LiteralPath $AudioFile).Path,
        '--audio-start',$AudioStart.ToString([Globalization.CultureInfo]::InvariantCulture))
}
if ($Engine) { $params += @('--engine',$Engine) }
foreach ($id in $PresetId) { $params += @('--id',$id) }
if ($Extended) { $params += '--extended' }
if ($TextureControls) { $params += '--texture-controls' }
if ($RetryFailed) { $params += '--retry-failed' }
if ($Reanalyse) { $params += '--reanalyse' }
& $render @params
if ($LASTEXITCODE -eq 2) { Write-Warning 'Some conditions are partial; inspect the gallery and saved logs.' }
elseif ($LASTEXITCODE -ne 0) { throw 'Controlled analysis stopped. Read the saved stage logs.' }
