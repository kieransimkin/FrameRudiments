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
    [Alias('ReanalyseExisting')][switch]$Reanalyse,
    [ValidateRange(0,64)][int]$SemanticBudget = 24,
    [ValidateSet('cpu','cuda','mps')][string]$Device = 'cpu',
    [switch]$NoSemantics,
    [switch]$AllowModelDownloads,
    [switch]$Status,
    [switch]$Verify
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$forward = @{ Limit = $Limit; SemanticBudget = $SemanticBudget; Device = $Device }
foreach ($key in $PSBoundParameters.Keys) {
    if ($key -eq 'Reanalyse') { $forward['ReanalyseExisting'] = $Reanalyse }
    else { $forward[$key] = $PSBoundParameters[$key] }
}
& (Join-Path $PSScriptRoot 'Run-HybridPresetAnalysis.ps1') @forward
