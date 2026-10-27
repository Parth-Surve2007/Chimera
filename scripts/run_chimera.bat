@echo off
set "ROOT=%~dp0.."
if exist "%ROOT%\dist\Chimera\Chimera.exe" (
  start "Chimera" "%ROOT%\dist\Chimera\Chimera.exe"
  exit /b 0
)
echo Packaged Chimera.exe not found. Run scripts\build_windows.ps1 first.
exit /b 2
