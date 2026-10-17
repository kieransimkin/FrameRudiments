#Requires -Version 5.1
<#
.SYNOPSIS
Single-command, resumable PrismMoves/PixelCue analysis of EVERY FrameRudiments preset.
.EXAMPLE
.\scripts\Run-FullPresetAnalysis.ps1
.EXAMPLE
.\scripts\Run-FullPresetAnalysis.ps1 -Limit 5
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
    [string]$Font,
    [ValidateRange(0,2147483647)][int]$Limit = 0,
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
    [ValidateRange(1,8)][int]$SemanticImages = 1,
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
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    foreach ($path in $KnownPaths) {
        if ($path -and (Test-Path $path)) {
            $env:PATH = (Split-Path $path -Parent) + [IO.Path]::PathSeparator + $env:PATH
            return $path
        }
    }
    return $null
}
function Require-WindowsTool {
    param([string]$Name, [string[]]$Binaries, [string]$WingetId,
          [string[]]$KnownPaths = @())
    $value = Find-Executable -Names $Binaries -KnownPaths $KnownPaths
    if ($value) { return $value }
    $winget = Find-Executable -Names @('winget.exe','winget')
    if (-not $winget) { throw "$Name is not installed. Install $WingetId and rerun this script." }
    Write-Host "Installing required $Name using winget ($WingetId)..." -ForegroundColor Yellow
    & $winget install --id $WingetId --exact --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) { throw "winget could not install $Name ($WingetId)." }
    $value = Find-Executable -Names $Binaries -KnownPaths $KnownPaths
    if (-not $value) { throw "$Name was installed but is not on PATH yet. Restart PowerShell and rerun." }
    return $value
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

    $tesseract = Find-Executable -Names @('tesseract.exe','tesseract') -KnownPaths @(
        "$env:PROGRAMFILES\Tesseract-OCR\tesseract.exe"
    )
    if (-not $tesseract) {
        $tesseract = Require-WindowsTool -Name 'Tesseract OCR' -Binaries @('tesseract.exe') `
            -WingetId 'UB-Mannheim.TesseractOCR' `
            -KnownPaths @((Join-Path $env:PROGRAMFILES 'Tesseract-OCR/tesseract.exe'))
    }
    $env:PATH = (Split-Path $tesseract -Parent) + [IO.Path]::PathSeparator + $env:PATH
    $trained = Join-Path $env:TESSDATA_PREFIX 'eng.traineddata'
    if (-not (Test-Path $trained)) {
        $source = Join-Path (Split-Path $tesseract -Parent) 'tessdata/eng.traineddata'
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
        $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
        $cmakeGenerator = '-GNinja'
        if (Test-Path $vswhere) {
            $vsInstall = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
                -property installationPath 2>$null
            if ($vsInstall) { $cmakeGenerator = '-GVisual Studio 17 2022' }
        }
        elseif (-not (Find-Executable -Names @('cl.exe','clang++.exe','g++.exe'))) {
            throw 'The pinned projectM runtime needs Visual Studio 2022 C++ Build Tools or a working C++ compiler.'
        }
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
    $ocr = Find-Executable -Names @('tesseract.exe','tesseract') -KnownPaths @(
        (Join-Path $env:PROGRAMFILES 'Tesseract-OCR/tesseract.exe'))
    if (-not $ocr) { throw 'Tesseract is missing. Re-run without -SkipSetup.' }
    $env:PATH = (Split-Path $ocr -Parent) + [IO.Path]::PathSeparator + $env:PATH
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
if ($Suite) { $argsList += @('--suite',(Resolve-Path $Suite).Path) }
if ($Font) { $argsList += @('--font',(Resolve-Path $Font).Path) }
if ($Limit -gt 0) { $argsList += @('--limit',[string]$Limit) }
if ($RetryFailed) { $argsList += '--retry-failed' }
if ($CommitWhenComplete) { $argsList += '--commit' }
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
