param(
    [string]$DataRoot,
    [switch]$Windowless
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
if (-not $DataRoot) {
    $pointer = Get-Content -Raw (Join-Path $repository '.voxweave.local.json') | ConvertFrom-Json
    $DataRoot = $pointer.data_root
}
$pythonName = if ($Windowless) { 'pythonw.exe' } else { 'python.exe' }
$python = Join-Path $DataRoot ".venv\Scripts\$pythonName"
if (-not (Test-Path -LiteralPath $python)) {
    throw "VoxWeave environment is missing. Run scripts\bootstrap.ps1 first: $python"
}
$env:VOXWEAVE_HOME = [System.IO.Path]::GetFullPath($DataRoot)
if ($Windowless) {
    Start-Process `
        -FilePath $python `
        -ArgumentList @('-m', 'voxweave.gui') `
        -WorkingDirectory $repository `
        -WindowStyle Hidden `
        -ErrorAction Stop | Out-Null
    exit 0
}
& $python -m voxweave.gui
