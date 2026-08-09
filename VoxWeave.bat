@echo off
setlocal

set "VOXWEAVE_REPOSITORY=%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%VOXWEAVE_REPOSITORY%scripts\run.ps1" %*
set "VOXWEAVE_EXIT_CODE=%ERRORLEVEL%"

if not "%VOXWEAVE_EXIT_CODE%"=="0" (
    echo.
    echo VoxWeave failed to start. Exit code: %VOXWEAVE_EXIT_CODE%
    if not defined VOXWEAVE_NO_PAUSE pause
)

exit /b %VOXWEAVE_EXIT_CODE%
