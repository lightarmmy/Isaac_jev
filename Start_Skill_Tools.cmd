@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0skills\isaac-play\scripts\isaac_tools.py" setup
"%~dp0.venv\Scripts\python.exe" "%~dp0skills\isaac-play\scripts\isaac_tools.py" call doctor
pause
