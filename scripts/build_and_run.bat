@echo off
set "ROOT=%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\build_windows.ps1"
if errorlevel 1 exit /b %ERRORLEVEL%
call "%ROOT%\scripts\run_chimera.bat"
