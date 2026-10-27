@echo off
set "ROOT=%~dp0.."
pushd "%ROOT%"
python -m chimera
set "CODE=%ERRORLEVEL%"
popd
exit /b %CODE%
