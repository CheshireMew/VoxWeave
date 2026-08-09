param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$VoxWeaveArguments
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$pointer = Get-Content -Raw (Join-Path $repository '.voxweave.local.json') | ConvertFrom-Json
$python = Join-Path $pointer.data_root '.venv\Scripts\python.exe'
$env:VOXWEAVE_HOME = [System.IO.Path]::GetFullPath($pointer.data_root)
& $python -m voxweave.cli @VoxWeaveArguments
exit $LASTEXITCODE
