param([string]$DataRoot)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
if (-not $DataRoot) {
    $pointer = Get-Content -Raw (Join-Path $repository '.voxweave.local.json') | ConvertFrom-Json
    $DataRoot = $pointer.data_root
}
$python = Join-Path $DataRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "VoxWeave environment is missing. Run scripts\bootstrap.ps1 first: $python"
}
$env:VOXWEAVE_HOME = [System.IO.Path]::GetFullPath($DataRoot)
& $python -m voxweave.gui
