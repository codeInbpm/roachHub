@echo off
rem roachHub Windows build entry (double-click friendly wrapper).
rem Real work lives in build_windows.ps1 -- this only bypasses the ExecutionPolicy.
rem   build_windows.cmd            -> portable folder build
rem   build_windows.cmd -OneFile   -> single exe build (settings will not persist)
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %RC%
