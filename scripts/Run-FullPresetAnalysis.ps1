#Requires -Version 5.1
<#
.SYNOPSIS
Single-command, resumable PrismMoves/PixelCue analysis of EVERY FrameRudiments preset.
.EXAMPLE
.\scripts\Run-FullPresetAnalysis.ps1
.EXAMPLE
.\scripts\Run-FullPresetAnalysis.ps1 -Limit 5
.EXAMPLE
.\scripts\Run-FullPresetAnalysis.ps1 -ArtworkDirectory 'Z:\My Songs\Covers' -UpgradeCoverage -Limit 5
.EXAMPLE
.\scripts\Run-FullPresetAnalysis.ps1 -Status
.EXAMPLE
.\scripts\Run-FullPresetAnalysis.ps1 -RetryFailed -CommitWhenComplete
.NOTES
All analysis outputs: ./visualiser-analysis/full-catalogue/
Downloaded models, runtimes and caches: ./models/ (Git ignored).
The sweep can take days. Rerunning the same command safely resumes it.
#>
[CmdletBinding()]
param(
    [string]$Suite,
    [string]$ArtworkDirectory,
    [string]$Font,
    [double[]]$Intervals = @(0.0, 0.5, 1.5, 3.0),
    [switch]$UpgradeCoverage,
    [ValidateRange(0,2147483647)][int]$Limit = 0,
    # Omit for a fresh random order every invocation; supply to replay an order.
    [UInt64]$OrderSeed = 0,
    [switch]$Status,
    [switch]$Verify,
    [switch]$InstallOnly,
    [switch]$SkipSetup,
    [switch]$RetryFailed,
    [switch]$CommitWhenComplete,
    [switch]$FullConditions,
    [Alias('NoPixelCueTagging')][switch]$NoVLM,
    [switch]$AllVLMs,
    [ValidateSet('smolvlm-256m','smolvlm-500m','smolvlm2-256m','smolvlm2-500m','joycaption')]
    [string]$PixelCueTagger = 'smolvlm-256m',
    [ValidateSet('cpu','cuda','mps')][string]$Device = 'cpu',
    [ValidateRange(64,2048)][int]$SemanticEdge = 384,
    # 0 means all output images; sampled mode is an explicit performance trade-off.
    [ValidateRange(0,10000)][int]$SemanticImages = 0,
    [ValidateRange(1,1000)][int]$CheckpointEvery = 25,
    [switch]$RequireAllSuccessful,
    [ValidateRange(1,86400)][int]$CaptureTimeout = 1800,
    [ValidateRange(1,86400)][int]$AnalysisTimeout = 900,
    [ValidateRange(1,86400)][int]$SemanticTimeout = 1200
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$models = Join-Path $repo 'models'
$output = Join-Path $repo 'visualiser-analysis/full-catalogue'
$pythonScript = Join-Path $PSScriptRoot 'full_preset_analysis.py'
$uvEnv = Join-Path $models '.venv-render'
$analysisEnv = Join-Path $models '.venv-analysis'
$renderPython = Join-Path $uvEnv 'Scripts/python.exe'
$analysisPython = Join-Path $analysisEnv 'Scripts/python.exe'
$catalogue = Join-Path $models 'preset-library/catalogue.json'

if (-not (Test-Path (Join-Path $repo 'pyproject.toml'))) { throw 'Not running inside FrameRudiments.' }
if (-not (Test-Path $pythonScript)) { throw 'Missing scripts/full_preset_analysis.py.' }
[void](New-Item -ItemType Directory -Force -Path $models)

# Children inherit project-local model and cache roots; no global HF/Torch weights.
$env:PIXELCUE_MODELS_DIR = $models
$env:HF_HOME = Join-Path $models '.cache/huggingface'
$env:HF_HUB_CACHE = Join-Path $env:HF_HOME 'hub'
$env:HUGGINGFACE_HUB_CACHE = $env:HF_HUB_CACHE
$env:HF_XET_CACHE = Join-Path $env:HF_HOME 'xet'
$env:HF_DATASETS_CACHE = Join-Path $env:HF_HOME 'datasets'
# Legacy global override would force several PixelCue VLMs into the same model directory.
if (Test-Path Env:PIXELCUE_MODEL_DIR) { Remove-Item Env:PIXELCUE_MODEL_DIR }
$env:TRANSFORMERS_CACHE = $env:HF_HUB_CACHE
$env:TORCH_HOME = Join-Path $models '.cache/torch'
$env:XDG_CACHE_HOME = Join-Path $models '.cache'
$env:UV_CACHE_DIR = Join-Path $models '.cache/uv'
$env:PIP_CACHE_DIR = Join-Path $models '.cache/pip'
$env:TESSDATA_PREFIX = Join-Path $models 'tesseract'
$env:FRAMERUDIMENTS_BROWSER_PROFILE_ROOT = Join-Path $models 'browser-profiles'
foreach ($path in @($env:HF_HOME, $env:HF_HUB_CACHE, $env:HF_XET_CACHE,
                    $env:HF_DATASETS_CACHE, $env:TORCH_HOME, $env:UV_CACHE_DIR,
                    $env:PIP_CACHE_DIR, $env:TESSDATA_PREFIX, $env:FRAMERUDIMENTS_BROWSER_PROFILE_ROOT)) {
    [void](New-Item -ItemType Directory -Force -Path $path)
}

function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    Write-Host ('> ' + $Command + ' ' + ($Arguments -join ' ')) -ForegroundColor DarkGray
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw ("Command failed (exit {0}): {1}" -f $LASTEXITCODE, $Command) }
}
function Find-Executable {
    param([string[]]$Names, [string[]]$KnownPaths = @())
    foreach ($name in $Names) {
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        # An alias or function may have a blank Source; accept real applications only.
        foreach ($command in @(Get-Command -Name $name -CommandType Application -ErrorAction SilentlyContinue)) {
            if ($command -and -not [string]::IsNullOrWhiteSpace($command.Source) -and
                (Test-Path -LiteralPath $command.Source -PathType Leaf)) {
                return [string]$command.Source
            }
        }
    }
    foreach ($path in $KnownPaths) {
        if (-not [string]::IsNullOrWhiteSpace($path) -and
            (Test-Path -LiteralPath $path -PathType Leaf)) {
            $env:PATH = (Split-Path -LiteralPath $path) + [IO.Path]::PathSeparator + $env:PATH
            return [string]$path
        }
    }
    return $null
}

function Get-TesseractKnownPaths {
    # Winget may install system-wide or per-user, without updating this shell's PATH.
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not [string]::IsNullOrWhiteSpace($root)) {
            Join-Path -Path $root -ChildPath 'Tesseract-OCR/tesseract.exe'
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        Join-Path -Path $env:LOCALAPPDATA -ChildPath 'Programs/Tesseract-OCR/tesseract.exe'
        Join-Path -Path $env:LOCALAPPDATA -ChildPath 'Microsoft/WinGet/Links/tesseract.exe'
    }
    # Respect a custom installation location recorded by the OCR installer.
    foreach ($registryRoot in @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )) {
        foreach ($entry in @(Get-ItemProperty -Path $registryRoot -ErrorAction SilentlyContinue)) {
            # Some uninstall entries lack these properties; StrictMode forbids
            # accessing a missing member, even for an unrelated application.
            if ($entry -and $entry.PSObject.Properties['DisplayName'] -and
                $entry.PSObject.Properties['InstallLocation'] -and
                $entry.DisplayName -like '*Tesseract*' -and
                -not [string]::IsNullOrWhiteSpace($entry.InstallLocation)) {
                Join-Path -Path $entry.InstallLocation -ChildPath 'tesseract.exe'
            }
        }
    }
}
function Require-WindowsTool {
    param([string]$Name, [string[]]$Binaries, [string]$WingetId,
          [string[]]$KnownPaths = @())
    $value = Find-Executable -Names $Binaries -KnownPaths $KnownPaths
    if ($value) { return $value }
    $winget = Find-Executable -Names @('winget.exe','winget')
    if (-not $winget) { throw "$Name is not installed. Install $WingetId and rerun this script." }
    Write-Host "Installing required $Name using winget ($WingetId)..." -ForegroundColor Yellow
    # PowerShell functions emit native stdout as return values. Without Out-Host,
    # winget progress/blank lines are captured alongside the executable path and
    # the caller's Split-Path can fail with 'Path is an empty string'.
    & $winget install --id $WingetId --exact --accept-source-agreements --accept-package-agreements --silent | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "winget could not install $Name ($WingetId)." }
    # The installer's registry/PATH entries may have appeared only during this call.
    if ($Name -eq 'Tesseract OCR') { $KnownPaths = @(Get-TesseractKnownPaths) }
    $value = Find-Executable -Names $Binaries -KnownPaths $KnownPaths
    if (-not $value) { throw "$Name was installed but its executable was not found. Check the installation location and rerun; existing models are preserved." }
    if ($value -isnot [string] -or -not (Test-Path -LiteralPath $value -PathType Leaf)) {
        throw "$Name discovery did not return exactly one executable path."
    }
    return [string]$value
}
function Download-PinnedWheel {
    param([string]$Url, [string]$Name, [string]$Sha256, [string]$Directory)
    [void](New-Item -ItemType Directory -Force -Path $Directory)
    $dest = Join-Path $Directory $Name
    if (Test-Path $dest) {
        if ((Get-FileHash -Algorithm SHA256 -Path $dest).Hash.ToLowerInvariant() -eq $Sha256) { return $dest }
        throw "Pinned analysis wheel checksum changed: $dest"
    }
    $partial = "$dest.partial"
    Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing -TimeoutSec 240
    if ((Get-FileHash -Algorithm SHA256 -Path $partial).Hash.ToLowerInvariant() -ne $Sha256) {
        throw "Official release wheel checksum mismatch; partial download retained: $partial"
    }
    Move-Item -Path $partial -Destination $dest
    return $dest
}

if ($Status -or $Verify) {
    $python = Find-Executable -Names @('python.exe','python','py.exe')
    if (Test-Path $renderPython) { $python = $renderPython }
    if (-not $python) { throw 'Python is needed to inspect the analysis status.' }
    $action = if ($Verify) { 'verify' } else { 'status' }
    $verifyArgs = @($pythonScript,$action,'--output',$output)
    if ($RequireAllSuccessful) { $verifyArgs += '--require-success' }
    & $python @verifyArgs
    exit $LASTEXITCODE
}

if ($NoVLM -and $AllVLMs) { throw 'Cannot combine -NoVLM and -AllVLMs.' }
if (($NoVLM -or $AllVLMs) -and $PSBoundParameters.ContainsKey('PixelCueTagger')) {
    throw 'Choose -PixelCueTagger, -AllVLMs, or -NoPixelCueTagging, not a combination.'
}
# Default is ON: lightweight PixelCue SmolVLM tags for every analysed preset.
$taggers = @()
if ($AllVLMs) {
    $taggers = @('joycaption','smolvlm-256m','smolvlm-500m','smolvlm2-256m','smolvlm2-500m')
}
elseif (-not $NoVLM) {
    $taggers = @($PixelCueTagger)
}
if ($taggers.Count) {
    Write-Host ('PixelCue tagging enabled: ' + ($taggers -join ', ')) -ForegroundColor Cyan
}
else {
    Write-Host 'PixelCue tagging disabled; other visual analysis models remain enabled.' -ForegroundColor Yellow
}

if (-not $SkipSetup) {
    $uv = Find-Executable -Names @('uv.exe','uv') -KnownPaths @(
        (Join-Path $HOME '.local/bin/uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft/WinGet/Links/uv.exe'),
        (Join-Path $env:USERPROFILE '.cargo/bin/uv.exe')
    )
    if (-not $uv) {
        $uv = Require-WindowsTool -Name 'uv' -Binaries @('uv.exe','uv') -WingetId 'astral-sh.uv' -KnownPaths @(
            (Join-Path $HOME '.local/bin/uv.exe'),
            (Join-Path $env:LOCALAPPDATA 'Microsoft/WinGet/Links/uv.exe'),
            (Join-Path $env:USERPROFILE '.cargo/bin/uv.exe'))
    }
    Invoke-Checked $uv @('python','install','3.11','3.13')
    if (-not (Test-Path $renderPython)) { Invoke-Checked $uv @('venv','--python','3.11',$uvEnv) }
    if (-not (Test-Path $analysisPython)) { Invoke-Checked $uv @('venv','--python','3.13',$analysisEnv) }

    $ffmpeg = Require-WindowsTool -Name 'FFmpeg' -Binaries @('ffmpeg.exe','ffmpeg') -WingetId 'Gyan.FFmpeg' `
        -KnownPaths @((Join-Path $env:LOCALAPPDATA 'Microsoft/WinGet/Links/ffmpeg.exe'))
    $node = Require-WindowsTool -Name 'Node.js' -Binaries @('node.exe','node') -WingetId 'OpenJS.NodeJS.LTS' `
        -KnownPaths @((Join-Path $env:PROGRAMFILES 'nodejs/node.exe'))
    $cmake = Require-WindowsTool -Name 'CMake' -Binaries @('cmake.exe','cmake') -WingetId 'Kitware.CMake' `
        -KnownPaths @((Join-Path $env:PROGRAMFILES 'CMake/bin/cmake.exe'))
    $ninja = Require-WindowsTool -Name 'Ninja' -Binaries @('ninja.exe','ninja') -WingetId 'Ninja-build.Ninja' `
        -KnownPaths @((Join-Path $env:LOCALAPPDATA 'Microsoft/WinGet/Links/ninja.exe'))
    $chrome = Find-Executable -Names @('chrome.exe','msedge.exe') -KnownPaths @(
        "$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe",
        "${env:PROGRAMFILES(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:PROGRAMFILES\Microsoft\Edge\Application\msedge.exe"
    )
    if (-not $chrome) { throw 'Chrome or Edge with WebGL2 is required for Butterchurn.' }
    $env:FRAMERUDIMENTS_BROWSER = $chrome
    $env:FRAMERUDIMENTS_FFMPEG = $ffmpeg
    Invoke-Checked $uv @('pip','install','--python',$renderPython,'-e',"${repo}[dev,projectm,gpu]")

    $wheels = Join-Path $models 'release-wheels'
    $prism = Download-PinnedWheel -Directory $wheels -Name 'prismmoves-0.1.0-py3-none-any.whl' `
        -Url 'https://github.com/kieransimkin/PrismMoves/releases/download/v0.1.0/prismmoves-0.1.0-py3-none-any.whl' `
        -Sha256 '16b1ed8b8574809b36f7a1e60e5bd4fa2d024720b583106b73a5c3bf9e32f865'
    $pixel = Download-PinnedWheel -Directory $wheels -Name 'pixelcue-0.9.0-py3-none-any.whl' `
        -Url 'https://github.com/kieransimkin/PixelCue/releases/download/v0.9.0/pixelcue-0.9.0-py3-none-any.whl' `
        -Sha256 '9b89169331f002e93bcced0dcc62be340c6aa6d8bb87d35dc878fdfa755320d4'

    # The two exact wheel hashes are the same pinned wheels exercised by FrameRudiments CI.
    # Install only PixelCue's visual-analysis imports; its GUI/faces are unrelated.
    Invoke-Checked $uv @('pip','install','--python',$analysisPython,
        'numpy>=1.26,<3','Pillow>=10.4','torch>=2.14.1','transformers>=4.53',
        'accelerate>=1.6','huggingface-hub>=0.30','platformdirs>=4.2',
        'pytesseract>=0.3.13','python-magic-bin>=0.4.14',
        'safetensors>=0.4','sentencepiece>=0.2','protobuf>=5','av>=12',
        'py7zr>=0.22','rarfile>=4.2')
    if ($AllVLMs) { Invoke-Checked $uv @('pip','install','--python',$analysisPython,'bitsandbytes>=0.45') }
    Invoke-Checked $uv @('pip','install','--python',$analysisPython,'--no-deps',$pixel,$prism)

    # All three default neural adapters have pinned, project-local checkpoints.
    $modelProfiles = @('clip','dinov2','blip')
    $missingProfiles = @($modelProfiles | Where-Object {
        -not ((Test-Path (Join-Path $models "$_/pixelcue-model.json")) -and
              (Test-Path (Join-Path $models "$_/config.json")))
    })
    if ($missingProfiles.Count -gt 0) {
        Invoke-Checked $analysisPython (@('-m','pixelcue.visual_models') + $missingProfiles + @('--models-dir',$models))
    }

    # Pre-download and verify PixelCue tagging weights before an expensive
    # catalogue sweep. The helper rejects any model directory outside models/.
    if ($taggers.Count -gt 0) {
        Write-Host 'Preparing project-local PixelCue tagging checkpoints...' -ForegroundColor Cyan
        $prepare = Join-Path $PSScriptRoot 'prepare_pixelcue_taggers.py'
        Invoke-Checked $analysisPython (@($prepare,'--models-dir',$models,'--taggers') + $taggers)
    }

    $tesseractPaths = @(Get-TesseractKnownPaths)
    $tesseract = Find-Executable -Names @('tesseract.exe','tesseract') -KnownPaths $tesseractPaths
    if (-not $tesseract) {
        $tesseract = Require-WindowsTool -Name 'Tesseract OCR' -Binaries @('tesseract.exe') `
            -WingetId 'UB-Mannheim.TesseractOCR' `
            -KnownPaths $tesseractPaths
    }
    if ($tesseract -isnot [string] -or -not (Test-Path -LiteralPath $tesseract -PathType Leaf)) {
        throw 'Tesseract detection returned an invalid executable path; existing model downloads are safe.'
    }
    $env:PATH = (Split-Path -LiteralPath $tesseract) + [IO.Path]::PathSeparator + $env:PATH
    $trained = Join-Path $env:TESSDATA_PREFIX 'eng.traineddata'
    if (-not (Test-Path $trained)) {
        $source = Join-Path (Split-Path -LiteralPath $tesseract) 'tessdata/eng.traineddata'
        if (-not (Test-Path $source)) { throw "English OCR model missing next to Tesseract: $source" }
        Copy-Item $source $trained
    }
    Invoke-Checked $tesseract @('--list-langs')

    $butterchurn = Join-Path $models 'runtimes/butterchurn'
    if (-not (Test-Path (Join-Path $butterchurn 'node_modules'))) {
        Invoke-Checked $renderPython @('-m','framerudiments','effects-setup','butterchurn',$butterchurn)
    }
    $env:FRAMERUDIMENTS_BUTTERCHURN_RUNTIME = $butterchurn

    $projectm = Join-Path $models 'runtimes/projectm'
    $native = @(Get-ChildItem -Path (Join-Path $projectm 'install') -Include 'projectM-4.dll','libprojectM-4.dll' `
        -File -Recurse -ErrorAction SilentlyContinue | Where-Object { Test-Path (Join-Path $_.DirectoryName 'framerudiments-projectm.json') })
    if ($native.Count -eq 0) {
        Write-Host 'Building pinned projectM native runtime (may take several minutes)...' -ForegroundColor Yellow
        # Select by *installed VS major version* and CMake's supported generators.
        # Never assume that vswhere's presence means Visual Studio 2022.
        $selectGenerator = Join-Path $PSScriptRoot 'select_projectm_generator.py'
        $cmakeGenerator = & $renderPython $selectGenerator '--cmake' $cmake '--runtime' $projectm
        if ($LASTEXITCODE -ne 0 -or $cmakeGenerator -isnot [string] -or
            -not $cmakeGenerator.StartsWith('-G')) {
            throw 'Could not select a matching projectM CMake generator. Check the toolchain details above.'
        }
        Write-Host ("Selected projectM generator: " + $cmakeGenerator) -ForegroundColor Cyan
        Invoke-Checked $renderPython @('-m','framerudiments','effects-setup','projectm',$projectm,
            ('--cmake-arg=' + $cmakeGenerator))
        $native = @(Get-ChildItem -Path (Join-Path $projectm 'install') -Include 'projectM-4.dll','libprojectM-4.dll' -File -Recurse |
            Where-Object { Test-Path (Join-Path $_.DirectoryName 'framerudiments-projectm.json') })
    }
    if ($native.Count -lt 1) { throw 'Could not find the pinned projectM DLL and receipt under models/.' }
    $env:FRAMERUDIMENTS_PROJECTM_LIBRARY = @($native | Sort-Object FullName)[0].FullName

    Invoke-Checked $renderPython @($pythonScript,'install-library','--catalogue',$catalogue)
}
else {
    if (-not (Test-Path $renderPython) -or -not (Test-Path $analysisPython)) {
        throw '-SkipSetup needs prior successful installation of the project-local Python environments.'
    }
    if (-not (Test-Path $catalogue)) { throw 'Preset catalogue is missing. Run without -SkipSetup.' }
    $env:FRAMERUDIMENTS_BUTTERCHURN_RUNTIME = Join-Path $models 'runtimes/butterchurn'
    $native = @(Get-ChildItem -Path (Join-Path $models 'runtimes/projectm/install') `
        -Include 'projectM-4.dll','libprojectM-4.dll' -File -Recurse -ErrorAction Stop)
    if ($native.Count -lt 1) { throw 'The pinned projectM shared library is missing. Re-run without -SkipSetup.' }
    $env:FRAMERUDIMENTS_PROJECTM_LIBRARY = @($native | Sort-Object FullName)[0].FullName
    $browser = Find-Executable -Names @('chrome.exe','msedge.exe') -KnownPaths @(
        "$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe",
        "$env:PROGRAMFILES\Microsoft\Edge\Application\msedge.exe")
    if (-not $browser) { throw 'Chromium browser unavailable.' }
    $env:FRAMERUDIMENTS_BROWSER = $browser
    $ff = Find-Executable -Names @('ffmpeg.exe','ffmpeg')
    if (-not $ff) { throw 'FFmpeg unavailable.' }
    $env:FRAMERUDIMENTS_FFMPEG = $ff
    $ocr = Find-Executable -Names @('tesseract.exe','tesseract') -KnownPaths @(Get-TesseractKnownPaths)
    if (-not $ocr) { throw 'Tesseract is missing. Re-run without -SkipSetup.' }
    $env:PATH = (Split-Path -LiteralPath $ocr) + [IO.Path]::PathSeparator + $env:PATH
    if (-not (Test-Path (Join-Path $env:TESSDATA_PREFIX 'eng.traineddata'))) {
        throw 'Project-local OCR trained-data missing. Re-run without -SkipSetup.'
    }
}

if ($InstallOnly) { Write-Host 'Setup complete. Analysis not started.' -ForegroundColor Green; exit 0 }

$argsList = @($pythonScript,'run','--output',$output,'--catalogue',$catalogue,
    '--render-python',$renderPython,'--analysis-python',$analysisPython,
    '--device',$Device,'--semantic-edge',[string]$SemanticEdge,
    '--semantic-images',[string]$SemanticImages,
    '--checkpoint-every',[string]$CheckpointEvery,
    '--capture-timeout',[string]$CaptureTimeout,
    '--analysis-timeout',[string]$AnalysisTimeout,
    '--semantic-timeout',[string]$SemanticTimeout)
# Preserve exact output seconds rather than relying on a single thumbnail per preset.
$argsList += '--intervals'
$argsList += @($Intervals | ForEach-Object { $_.ToString([System.Globalization.CultureInfo]::InvariantCulture) })
if ($Suite) { $argsList += @('--suite',(Resolve-Path $Suite).Path) }
if ($ArtworkDirectory) { $argsList += @('--artwork-directory',(Resolve-Path $ArtworkDirectory).Path) }
if ($UpgradeCoverage) { $argsList += '--upgrade-coverage' }
if ($Font) { $argsList += @('--font',(Resolve-Path $Font).Path) }
if ($Limit -gt 0) { $argsList += @('--limit',[string]$Limit) }
if ($PSBoundParameters.ContainsKey('OrderSeed')) {
    $argsList += @('--order-seed', $OrderSeed.ToString([System.Globalization.CultureInfo]::InvariantCulture))
}
if ($RetryFailed) { $argsList += '--retry-failed' }
if ($CommitWhenComplete) { $argsList += '--commit' }
# All four image families are now the default. Keep -FullConditions compatible.
if ($FullConditions) { $argsList += @('--images','cover-lyrics','colour-bars','grid-rings','text-card') }
# Always pass the selected PixelCue taggers explicitly. Without an opt-out,
# the lightweight tagger is enabled, including on resumed runs. Keeping this
# argument stable preserves the existing experiment signature on resume.
$argsList += '--keyword-models'
$argsList += $taggers
Write-Host "FrameRudiments full preset analysis; resumable output: $output" -ForegroundColor Cyan
& $renderPython @argsList
$exitCode = $LASTEXITCODE
if ($exitCode -eq 2 -and $Limit -gt 0) {
    Write-Host 'Pilot limit reached. The remaining presets are pending; rerun without -Limit to continue.' -ForegroundColor Yellow
    exit 0
}
exit $exitCode
