@echo off
setlocal
title AI Marketing Ops - Launcher
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem  Universal launcher entry point. Real logic lives in scripts\launcher.ps1,
rem  which auto-detects Python, rebuilds a broken .venv, installs missing
rem  packages (with China mirror fallback), picks a free port, and binds to
rem  0.0.0.0 so other computers on the same LAN can open the page.
rem
rem  Optional arguments (passed through to launcher.ps1):
rem    -Port 9000    use a specific port
rem    -Local        only allow this computer to connect
rem    -NoBrowser    do not open the browser automatically
rem ---------------------------------------------------------------------------

if not exist "scripts\launcher.ps1" (
  echo.
  echo  [ERROR] scripts\launcher.ps1 not found.
  echo          Please extract the WHOLE folder, keep the scripts subfolder,
  echo          then double-click this file again.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\launcher.ps1" %*
set "RC=%ERRORLEVEL%"

if /i "%NOPAUSE%"=="1" exit /b %RC%
echo.
pause
exit /b %RC%
