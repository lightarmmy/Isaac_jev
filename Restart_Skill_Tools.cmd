@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0skills\isaac-play\scripts\isaac_tools.py" restart
if errorlevel 1 goto finish
"%~dp0.venv\Scripts\python.exe" "%~dp0skills\isaac-play\scripts\isaac_tools.py" call doctor
:finish
pause
