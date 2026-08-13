param(
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$pythonArguments = @()

if (-not $Python -and $env:VOXWEAVE_BUILD_PYTHON) {
    $Python = $env:VOXWEAVE_BUILD_PYTHON
}
if (-not $Python) {
    $pointerPath = Join-Path $repository '.voxweave.local.json'
    if (Test-Path -LiteralPath $pointerPath) {
        $pointer = Get-Content -Raw -LiteralPath $pointerPath | ConvertFrom-Json
        $candidate = Join-Path $pointer.data_root '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $candidate) {
            $Python = $candidate
        }
    }
}
if (-not $Python -and (Get-Command py.exe -ErrorAction SilentlyContinue)) {
    $Python = (Get-Command py.exe).Source
    $pythonArguments = @('-3.12')
}
if (-not $Python -and (Get-Command python.exe -ErrorAction SilentlyContinue)) {
    $Python = (Get-Command python.exe).Source
}
if (-not $Python) {
    throw 'Python 3.12 was not found. Pass -Python or set VOXWEAVE_BUILD_PYTHON.'
}

$version = & $Python @pythonArguments -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE -ne 0 -or $version.Trim() -ne '3.12') {
    throw "VoxWeave must be built with Python 3.12; found $version."
}
& $Python @pythonArguments -c 'import PyInstaller' 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is missing. Install requirements-build.lock into the build environment.'
}

$iconBuilder = Join-Path $repository 'scripts\build-icon.py'
& $Python @pythonArguments $iconBuilder
if ($LASTEXITCODE -ne 0) {
    throw "Icon generation exited with code $LASTEXITCODE."
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$archiveRoot = Join-Path $repository ".archive\package-builds\$timestamp"
foreach ($relative in @('build\VoxWeave', 'dist\VoxWeave')) {
    $existing = Join-Path $repository $relative
    if (Test-Path -LiteralPath $existing) {
        New-Item -ItemType Directory -Force -Path $archiveRoot | Out-Null
        $label = if ($relative.StartsWith('build')) { 'build' } else { 'dist' }
        Move-Item -LiteralPath $existing -Destination (Join-Path $archiveRoot $label)
    }
}

Push-Location $repository
try {
    & $Python @pythonArguments -m PyInstaller `
        --distpath (Join-Path $repository 'dist') `
        --workpath (Join-Path $repository 'build') `
        (Join-Path $repository 'packaging\VoxWeave.spec')
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

$executable = Join-Path $repository 'dist\VoxWeave\VoxWeave.exe'
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Build completed without the expected executable: $executable"
}
Write-Output $executable
