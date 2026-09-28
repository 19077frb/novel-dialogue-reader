@echo off
rem Double-click launcher for the Novel Dialogue Reader (single-port, same-origin mode).
rem ASCII-only on purpose: Chinese messages come from scripts\serve.ps1 (UTF-8 with BOM).
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8765"

echo ============================================================
echo   Novel Dialogue Reader - starting (port %PORT%)
echo ============================================================
echo.

if not exist "scripts\serve.ps1" (
  echo [ERROR] scripts\serve.ps1 not found. Put this file in the project root.
  pause
  exit /b 1
)

rem --- pick a PowerShell host: prefer PowerShell 7 (pwsh), fall back to Windows PowerShell 5.1 ---
set "PS="
for %%I in (pwsh.exe) do if not defined PS set "PS=%%~$PATH:I"
if not defined PS if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PS=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined PS if exist "%ProgramFiles(x86)%\PowerShell\7\pwsh.exe" set "PS=%ProgramFiles(x86)%\PowerShell\7\pwsh.exe"
if not defined PS if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe" set "PS=%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"
if not defined PS if exist "%ProgramFiles%\PowerShell\7-preview\pwsh.exe" set "PS=%ProgramFiles%\PowerShell\7-preview\pwsh.exe"
if not defined PS if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not defined PS (
  echo [ERROR] No PowerShell found. Install PowerShell 7, then run:
  echo         pwsh -File scripts\serve.ps1
  pause
  exit /b 1
)

if not exist "backend\.venv\Scripts\python.exe" (
  where uv >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Backend environment missing. First run:
    echo         uv sync --project backend --all-groups
    pause
    exit /b 1
  )
)

rem --- already running? just open the page ---
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 3 -UseBasicParsing; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 (
  echo Reader is ALREADY RUNNING on port %PORT%.
  echo Opening http://127.0.0.1:%PORT%/ ...
  start "" "http://127.0.0.1:%PORT%/"
  echo To stop it: double-click stop.bat
  timeout /t 4 /nobreak >nul 2>nul
  exit /b 0
)

set "SERVE_ARGS=-Port %PORT%"
if exist "frontend\dist\index.html" (
  set "SERVE_ARGS=%SERVE_ARGS% -SkipBuild"
) else (
  echo First run: building the frontend, this may take a minute...
)

if not defined NDR_NO_BROWSER (
  start "" /min "%PS%" -NoProfile -ExecutionPolicy Bypass -Command "for ($i=0; $i -lt 120; $i++) { try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/api/health' -TimeoutSec 2 -UseBasicParsing; if ($r.StatusCode -eq 200) { break } } catch {}; Start-Sleep -Milliseconds 750 }; Start-Process 'http://127.0.0.1:%PORT%/'"
)

echo Starting the server with: %PS%
echo The browser will open when the server is ready.
echo Keep this window open while reading.
echo Stop with Ctrl+C in this window, or double-click stop.bat.
echo.

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "scripts\serve.ps1" %SERVE_ARGS%
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo [ERROR] Server exited with code %RC%.
  echo   * Port %PORT% busy? Double-click stop.bat, or use another port:  start.bat 8800
  echo   * Backend logs: data\logs\
  echo   * Details: README.md
  pause
) else (
  echo Server stopped. You can close this window.
  timeout /t 5 /nobreak >nul 2>nul
)
exit /b %RC%
