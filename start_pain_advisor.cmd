@echo off
cd /d C:\work\pain
set PYTHONPATH=C:\work\pain\src
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\work\pain\codex-gateway\start-gateway.ps1"
if errorlevel 1 (
  echo Failed to start local Codex gateway.
  pause
  exit /b 1
)
start "" "C:\work\pain\.venv\Scripts\pythonw.exe" -m pain_assistant
