@echo off
setlocal EnableExtensions
title Chimera Windows Setup

cd /d "%~dp0.."

echo.
echo  CHIMERA - WINDOWS SETUP
echo  ======================
echo  This installs Chimera's Python dependencies into the active global
echo  Python user installation. It does not create or use a virtual environment.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    echo Install Python 3.11 or newer, enable "Add Python to PATH", then rerun.
    exit /b 1
)

python -c "import sys; print('Python', sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
    echo ERROR: Chimera requires Python 3.11 or newer.
    exit /b 1
)

echo.
echo [1/2] Installing Chimera, Windows UI automation, test and packaging dependencies...
python -m pip install --user --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install --user -e ".[test]" pyinstaller
if errorlevel 1 exit /b 1

echo.
echo Python dependencies installed.
echo.

where winget >nul 2>nul
if errorlevel 1 (
    echo WARNING: winget was not found. Chimera itself is installed, but companion
    echo applications must be installed manually:
    echo   Blender: https://www.blender.org/download/
    echo   Ghidra:  https://github.com/NationalSecurityAgency/ghidra/releases
    echo   Java:    https://adoptium.net/installation/
    goto :finish
)

echo [2/2] Optional companion applications
echo Blender is installed through Winget. Ghidra is downloaded from the official
echo NSA GitHub release because it is distributed as a portable ZIP, not a setup wizard.
echo Ghidra 12.2 requires a 64-bit JDK 25 or newer.
echo.
choice /C YN /M "Install Blender, Temurin JDK 25, and official Ghidra now"
if errorlevel 2 goto :finish

echo.
echo Installing Blender...
winget install --id BlenderFoundation.Blender --exact --accept-source-agreements --accept-package-agreements
if errorlevel 1 echo WARNING: Blender installation reported an error. Check the winget output.

echo.
echo Installing Eclipse Temurin JDK 25...
winget install --id EclipseAdoptium.Temurin.25.JDK --exact --accept-source-agreements --accept-package-agreements
if errorlevel 1 echo WARNING: JDK installation reported an error. Check the winget output.

echo.
echo Downloading and installing the latest official Ghidra release...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_ghidra.ps1"
if errorlevel 1 echo WARNING: Ghidra installation did not complete. Use the manual link below.

echo.
echo Companion installation commands finished.
echo Close and reopen Chimera so it can rediscover newly installed applications.

:finish
echo.
echo SETUP FINISHED
echo.
echo Launch from this repository with:
echo   python -m chimera
echo.
pause
endlocal
