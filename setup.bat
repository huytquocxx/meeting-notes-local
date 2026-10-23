@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10 or newer is required. Install it from python.org and tick "Add to PATH", then run this again. & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
".venv\Scripts\python.exe" setup.py
echo.
echo Done. Double-click record.bat to record a meeting.
pause
