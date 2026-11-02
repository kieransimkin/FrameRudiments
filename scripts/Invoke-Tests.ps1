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
$onWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
$tempParent = if ($onWindows -and $env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "FrameRudiments\pytest-tmp"
} else {
    Join-Path $repoRoot ".test-tmp"
}
$runName = "pytest-{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), ([guid]::NewGuid().ToString("N"))
$tempRoot = [IO.Path]::GetFullPath((Join-Path $tempParent $runName))

if (-not $tempRoot.StartsWith([IO.Path]::GetFullPath($tempParent) + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe pytest temp path: $tempRoot"
}

$exitCode = 1
$previousPythonPath = $env:PYTHONPATH
try {
    New-Item -ItemType Directory -Path $tempParent -Force | Out-Null
    $probe = Join-Path $tempParent ("probe-" + [guid]::NewGuid().ToString("N"))
    $renamedProbe = "$probe-renamed"
    try {
        New-Item -ItemType Directory -Path $probe | Out-Null
        $probeFile = Join-Path $probe "probe.txt"
        [IO.File]::WriteAllText($probeFile, "pytest-temp-probe")
        if ([IO.File]::ReadAllText($probeFile) -ne "pytest-temp-probe") {
            throw "Pytest temp probe readback failed: $probe"
        }
        Move-Item -LiteralPath $probe -Destination $renamedProbe
    }
    finally {
        if (Test-Path -LiteralPath $renamedProbe) {
            Remove-Item -LiteralPath $renamedProbe -Recurse -Force
        }
        if (Test-Path -LiteralPath $probe) {
            Remove-Item -LiteralPath $probe -Recurse -Force
        }
    }
    Write-Host "pytest temp probe passed: $tempParent"
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
    Write-Host "pytest temp cleanup complete: $tempRoot"
}

exit $exitCode
