[CmdletBinding()]
param(
    [Parameter()]
    [string] $Python,
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]] $PytestArgs = @()
)

$ErrorActionPreference = "Stop"
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$projectPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not $Python) {
    $Python = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { "python" }
}
$projectSrc = Join-Path $repoRoot "src"
$tempParent = Join-Path $repoRoot ".test-tmp"
$runName = "pytest-{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([guid]::NewGuid().ToString("N"))
$tempRoot = [IO.Path]::GetFullPath((Join-Path $tempParent $runName))

if (-not $tempRoot.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe pytest temp path: $tempRoot"
}

$exitCode = 1
$previousPythonPath = $env:PYTHONPATH
try {
    New-Item -ItemType Directory -Path $tempParent -Force | Out-Null
    Write-Host "pytest temp root: $tempRoot"
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$projectSrc$([IO.Path]::PathSeparator)$previousPythonPath"
    } else {
        $projectSrc
    }
    Push-Location -LiteralPath $repoRoot
    try {
        Write-Host "pytest interpreter: $Python"
        & $Python -m pytest @PytestArgs "--basetemp=$tempRoot"
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $tempRoot) {
        throw "pytest temp cleanup failed: $tempRoot"
    }
    if ((Test-Path -LiteralPath $tempParent) -and -not (Get-ChildItem -LiteralPath $tempParent -Force)) {
        Remove-Item -LiteralPath $tempParent -Force
    }
    Write-Host "pytest temp cleanup complete: $tempRoot"
}

exit $exitCode
