@echo off
REM One-time setup for Windows 11: creates a virtual environment and installs dependencies.
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher "py" not found. Install Python 3.10+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  exit /b 1
)
if not exist .venv ( py -3 -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
echo.
echo Optional: download Chart.js for the Chart.js renderer (built-in SVG charts work without it)
python scripts\download_chartjs.py
echo.
echo Setup complete. Start the app with run_windows.bat
