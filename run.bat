@echo off
REM Venue Demand Intelligence - start script (Windows)
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py -3
if exist ".venv\Scripts\python.exe" set PY=".venv\Scripts\python.exe"
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo Python 3.10 or newer is required. Install Python and try again.
  exit /b 1
)
%PY% -c "import pandas, numpy, dotenv" 2>nul
if errorlevel 1 (
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)
if "%VDI_PORT%"=="" (set PORTSHOW=8050) else (set PORTSHOW=%VDI_PORT%)
echo Open http://localhost:%PORTSHOW%
%PY% -m vdi.server %*
exit /b %errorlevel%
