@echo off
setlocal
cd /d "%~dp0"

rem Add a Windows Firewall inbound rule for LAN access (run this once).
rem Rule: TCP 8000-8020, local subnet only, all network profiles.
rem Remove it later with:
rem   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\allow_firewall.ps1 -Remove

if not exist "scripts\allow_firewall.ps1" (
  echo.
  echo  [ERROR] scripts\allow_firewall.ps1 not found.
  echo          Please extract the WHOLE folder, then double-click this file again.
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\allow_firewall.ps1" %*
set "RC=%ERRORLEVEL%"
if /i "%NOPAUSE%"=="1" exit /b %RC%
echo.
pause
exit /b %RC%