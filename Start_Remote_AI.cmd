@echo off
cd /d "%~dp0"
echo Close the shadow client first. Keep the SSH tunnel open.
echo AI combat test: 30 seconds. F8 or Esc returns manual control.
"%~dp0.venv\Scripts\python.exe" "%~dp0run.py" remote-infer --mode ai --max-seconds 30
pause
