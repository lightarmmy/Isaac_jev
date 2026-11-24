@echo off
cd /d "%~dp0"
echo Start the SSH tunnel first. Shadow mode evaluates combat without controlling the character.
"%~dp0.venv\Scripts\python.exe" "%~dp0run.py" remote-infer --mode shadow
pause
