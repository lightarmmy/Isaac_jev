@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_game_link.ps1"
if errorlevel 1 pause
