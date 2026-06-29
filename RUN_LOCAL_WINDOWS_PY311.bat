@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo FED Chair Simulator - Local Windows Launcher
echo ==================================================
echo This app must run with Python 3.11 because pandas/statsmodels
echo wheels may not exist for newer Python versions on Windows.
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python launcher "py" was not found.
  echo Install Python 3.11 from https://www.python.org/downloads/release/python-31111/
  echo During install, tick "Add python.exe to PATH".
  pause
  exit /b 1
)

py -3.11 -V >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python 3.11 is not installed.
  echo Your machine is probably using Python 3.14, which tries to compile pandas
  echo and fails without Visual Studio build tools.
  echo.
  echo Fix: install Python 3.11.11, then run this file again.
  echo Download: https://www.python.org/downloads/release/python-31111/
  pause
  exit /b 1
)

if exist .venv\Scripts\python.exe (
  for /f %%V in ('.venv\Scripts\python.exe -c "import sys; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor))"') do set VENV_PY=%%V
  if not "%VENV_PY%"=="3.11" (
    echo Existing .venv uses Python %VENV_PY%. Rebuilding it with Python 3.11...
    rmdir /s /q .venv
  )
)

if not exist .venv\Scripts\python.exe (
  echo Creating virtual environment with Python 3.11...
  py -3.11 -m venv .venv
  if errorlevel 1 (
    echo ERROR: Could not create virtual environment.
    pause
    exit /b 1
  )
)

call .venv\Scripts\activate.bat

echo Using Python:
python -V

echo.
echo Installing dependencies...
python -m pip install --upgrade pip setuptools wheel
python -m pip install --only-binary=:all: -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR: Dependency install failed.
  echo Most common cause: wrong Python version. Run: py -3.11 -V
  echo If Python 3.11 is missing, install Python 3.11.11 and run again.
  pause
  exit /b 1
)

set PORT=5000
echo.
echo Starting app at http://127.0.0.1:%PORT%
start "" http://127.0.0.1:%PORT%
python main.py
pause
