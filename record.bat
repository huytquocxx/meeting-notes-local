@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" notes.py record
echo.
echo Done. Open the meetings folder to see your transcript.
pause
