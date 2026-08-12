@echo off
setlocal

set "VOXWEAVE_REPOSITORY=%~dp0"
start "" wscript.exe //NoLogo "%VOXWEAVE_REPOSITORY%VoxWeave.vbs" %*
exit /b 0
