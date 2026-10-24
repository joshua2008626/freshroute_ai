@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Run setup_windows.bat first.
  exit /b 1
)
call .venv\Scripts\activate.bat
start "" http://127.0.0.1:5000
python run.py
