param(
    [string]$Python,
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$repository = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
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
        if (-not $OutputRoot) {
            $OutputRoot = Join-Path $pointer.data_root 'release-builds'
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
if (-not $OutputRoot -and $env:VOXWEAVE_BUILD_ROOT) {
    $OutputRoot = $env:VOXWEAVE_BUILD_ROOT
}
if (-not $OutputRoot) {
    throw 'A build output root is required. Pass -OutputRoot or set VOXWEAVE_BUILD_ROOT.'
}

$outputRootFull = [System.IO.Path]::GetFullPath($OutputRoot)
$repositoryPrefix = $repository.TrimEnd('\') + '\'
if (($outputRootFull.TrimEnd('\') + '\').StartsWith($repositoryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release builds must stay outside the repository: $outputRootFull"
}
if ([System.IO.Path]::GetPathRoot($outputRootFull).Equals('C:\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release builds must not consume the system drive: $outputRootFull"
}

$versionOutput = (& $Python @pythonArguments --version 2>&1).Trim()
if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch '^Python 3\.12(\.|$)') {
    throw "VoxWeave must be built with Python 3.12; found $versionOutput."
}
& $Python @pythonArguments -c 'import PyInstaller' 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is missing. Install requirements-build.lock into the build environment.'
}
if (-not (Test-Path -LiteralPath (Join-Path $repository 'assets\app\VoxWeave.ico'))) {
    throw 'The source-controlled Windows icon is missing.'
}
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    throw 'Git is required to establish release provenance.'
}

$dirty = @(& git.exe -C $repository status --porcelain=v1 --untracked-files=normal)
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect the Git worktree.'
}
if ($dirty.Count -ne 0) {
    throw 'Release builds require a clean worktree so the ZIP maps to one exact commit.'
}
$commit = (& git.exe -C $repository rev-parse HEAD).Trim()
$sourceDateEpoch = (& git.exe -C $repository show -s --format=%ct $commit).Trim()
$sourceUrl = (& git.exe -C $repository remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0 -or -not $sourceUrl) {
    throw 'The release source repository URL is unavailable.'
}
$projectMetadata = Get-Content -Raw -LiteralPath (Join-Path $repository 'pyproject.toml')
$projectVersionMatch = [System.Text.RegularExpressions.Regex]::Match(
    $projectMetadata,
    '(?m)^version\s*=\s*"([^"]+)"\s*$'
)
if (-not $projectVersionMatch.Success) {
    throw 'Unable to read the project version.'
}
$projectVersion = $projectVersionMatch.Groups[1].Value
if (-not [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().Equals('X64')) {
    throw 'The supported Windows release target is x64.'
}

$buildKey = "VoxWeave-$projectVersion-$($commit.Substring(0, 12))"
$buildRoot = Join-Path $outputRootFull $buildKey
if (Test-Path -LiteralPath $buildRoot) {
    throw "A build for this exact version and commit already exists: $buildRoot"
}
New-Item -ItemType Directory -Path $buildRoot | Out-Null

$pyinstallerWork = Join-Path $buildRoot 'pyinstaller-work'
$pyinstallerDist = Join-Path $buildRoot 'pyinstaller-dist'
$artifactsRoot = Join-Path $buildRoot 'artifacts'
$verificationRoot = Join-Path $buildRoot 'extracted-verification'

Push-Location $repository
try {
    & $Python @pythonArguments -m PyInstaller `
        --noconfirm `
        --distpath $pyinstallerDist `
        --workpath $pyinstallerWork `
        (Join-Path $repository 'packaging\VoxWeave.spec')
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited with code $LASTEXITCODE. The incomplete build remains at $buildRoot for diagnosis."
    }
}
finally {
    Pop-Location
}

$bundleRoot = Join-Path $pyinstallerDist 'VoxWeave'
if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot 'VoxWeave.exe'))) {
    throw "Build completed without the expected executable: $bundleRoot\VoxWeave.exe"
}

$releaseScript = Join-Path $repository 'scripts\release_artifacts.py'
$analysisToc = Join-Path $pyinstallerWork 'VoxWeave\Analysis-00.toc'
& $Python @pythonArguments $releaseScript `
    --repository $repository `
    --bundle-root $bundleRoot `
    --artifacts-root $artifactsRoot `
    --verification-root $verificationRoot `
    --version $projectVersion `
    --commit $commit `
    --source-url $sourceUrl `
    --source-date-epoch $sourceDateEpoch `
    --analysis-toc $analysisToc
if ($LASTEXITCODE -ne 0) {
    throw "Release assembly or extracted-tree verification failed. Evidence remains at $buildRoot."
}

$smokeExe = Join-Path $verificationRoot 'VoxWeave\VoxWeave.exe'
$smokeData = Join-Path $buildRoot 'smoke-data'
$smokeReport = Join-Path $buildRoot 'smoke-report.json'
New-Item -ItemType Directory -Path $smokeData | Out-Null
$previousVoxWeaveHome = $env:VOXWEAVE_HOME
$previousQtPlatform = $env:QT_QPA_PLATFORM
$previousRhiBackend = $env:QSG_RHI_BACKEND
try {
    $env:VOXWEAVE_HOME = $smokeData
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:QSG_RHI_BACKEND = 'software'
    $smokeProcess = Start-Process `
        -FilePath $smokeExe `
        -ArgumentList @('--voxweave-release-smoke', '--report', "`"$smokeReport`"") `
        -PassThru `
        -WindowStyle Hidden
    if (-not $smokeProcess.WaitForExit(30000)) {
        Stop-Process -Id $smokeProcess.Id -Force -ErrorAction SilentlyContinue
        throw "Extracted application smoke test timed out. Evidence remains at $buildRoot."
    }
    if ($smokeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $smokeReport)) {
        throw "Extracted application smoke test failed. Evidence remains at $buildRoot."
    }
    $smokeResult = Get-Content -Raw -LiteralPath $smokeReport | ConvertFrom-Json
    if (-not $smokeResult.ok -or $smokeResult.root_object_count -lt 1) {
        throw "Extracted application smoke report is invalid. Evidence remains at $buildRoot."
    }
}
finally {
    [System.Environment]::SetEnvironmentVariable('VOXWEAVE_HOME', $previousVoxWeaveHome, 'Process')
    [System.Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM', $previousQtPlatform, 'Process')
    [System.Environment]::SetEnvironmentVariable('QSG_RHI_BACKEND', $previousRhiBackend, 'Process')
}

Write-Output (Join-Path $artifactsRoot "VoxWeave-$projectVersion-windows-x64.zip")
Write-Output (Join-Path $artifactsRoot "VoxWeave-$projectVersion-windows-x64.zip.sha256")
Write-Output (Join-Path $artifactsRoot 'release-manifest.json')
Write-Output (Join-Path $artifactsRoot 'release-summary.json')
Write-Output $smokeReport
