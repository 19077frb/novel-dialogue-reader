@echo off
rem Stops the backend started by start.bat / scripts\serve.ps1 (ASCII-only on purpose).
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "scripts\serve.ps1" (
  echo [ERROR] scripts\serve.ps1 not found. Put this file in the project root.
  pause
  exit /b 1
)

set "PS="
for %%I in (pwsh.exe) do if not defined PS set "PS=%%~$PATH:I"
if not defined PS if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PS=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined PS if exist "%ProgramFiles(x86)%\PowerShell\7\pwsh.exe" set "PS=%ProgramFiles(x86)%\PowerShell\7\pwsh.exe"
if not defined PS if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe" set "PS=%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"
if not defined PS if exist "%ProgramFiles%\PowerShell\7-preview\pwsh.exe" set "PS=%ProgramFiles%\PowerShell\7-preview\pwsh.exe"
if not defined PS if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not defined PS (
  echo [ERROR] No PowerShell found.
  pause
  exit /b 1
)

echo Stopping the reader (backend recorded in data\run\serve-pids.json)...
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "scripts\serve.ps1" -Stop
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
  echo Done. If the start window is still open it will close by itself.
) else (
  echo [ERROR] Stop failed with code %RC%.
  pause
)
timeout /t 5 /nobreak >nul 2>nul
exit /b %RC%
