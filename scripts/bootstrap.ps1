param(
    [string]$DataRoot,
    [string]$Python,
    [string]$RvcRoot,
    [string]$RvcPython,
    [string]$Ffmpeg,
    [string]$Ffprobe,
    [string]$WeSpeakerModel
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
if (-not $DataRoot) {
    $DataRoot = Read-Host 'VoxWeave data/runtime directory (do not choose the source folder)'
}
if (-not [System.IO.Path]::IsPathRooted($DataRoot)) {
    throw 'DataRoot must be an absolute path.'
}
$resolvedDataRoot = [System.IO.Path]::GetFullPath($DataRoot)
$resolvedRepository = [System.IO.Path]::GetFullPath($repository)
if ($resolvedDataRoot -eq $resolvedRepository -or $resolvedDataRoot.StartsWith($resolvedRepository + [System.IO.Path]::DirectorySeparatorChar)) {
    throw 'DataRoot must be outside the source checkout.'
}
if (-not $Python) {
    $Python = (Get-Command python -ErrorAction Stop).Source
}

New-Item -ItemType Directory -Force -Path $resolvedDataRoot | Out-Null
$venv = Join-Path $resolvedDataRoot '.venv'
$pipCache = Join-Path $resolvedDataRoot 'pip-cache'
$tempRoot = Join-Path $resolvedDataRoot 'temp'
New-Item -ItemType Directory -Force -Path $pipCache,$tempRoot | Out-Null
$env:PIP_CACHE_DIR = $pipCache
$env:TMP = $tempRoot
$env:TEMP = $tempRoot

if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
    & $Python -m venv $venv
}
$venvPython = Join-Path $venv 'Scripts\python.exe'
$lockFile = Join-Path $repository 'requirements.lock'
$env:PIP_CONSTRAINT = $lockFile
& $venvPython -m pip install 'pip==26.2.1'
& $venvPython -m pip install -c $lockFile -e "${repository}[dev]"

$configure = @('-m','voxweave.bootstrap','--data-root',$resolvedDataRoot)
if ($RvcRoot) { $configure += @('--rvc-root',$RvcRoot) }
if ($RvcPython) { $configure += @('--rvc-python',$RvcPython) }
if ($Ffmpeg) { $configure += @('--ffmpeg',$Ffmpeg) }
if ($Ffprobe) { $configure += @('--ffprobe',$Ffprobe) }
if ($WeSpeakerModel) { $configure += @('--wespeaker-model',$WeSpeakerModel) }
& $venvPython @configure
Write-Host "VoxWeave source environment is ready: $venv"
