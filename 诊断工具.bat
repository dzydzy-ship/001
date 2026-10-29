@echo off
setlocal
cd /d "%~dp0"

rem Environment diagnostics: Python, venv, dependencies, ports, LAN IP,
rem firewall state, and an app import self-test.
rem Output is printed here and also saved as a text file inside the logs folder.

if not exist "scripts\launcher.ps1" (
  echo.
  echo  [ERROR] scripts\launcher.ps1 not found.
  echo          Please extract the WHOLE folder, then double-click this file again.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\launcher.ps1" -Diagnose
set "RC=%ERRORLEVEL%"
if /i "%NOPAUSE%"=="1" exit /b %RC%
echo.
pause
exit /b %RC%