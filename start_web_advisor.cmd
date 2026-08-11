@echo off
cd /d C:\work\pain
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\work\pain\codex-gateway\start-gateway.ps1"
if errorlevel 1 (
  echo Failed to start local Codex gateway.
  pause
  exit /b 1
)
if not exist "C:\work\pain\web-ui\src-tauri\target\release\telegram-advisor.exe" (
  echo Release build is missing. Run C:\work\pain\web-ui\build-tauri-release.cmd first.
  pause
  exit /b 1
)
start "" "C:\work\pain\web-ui\src-tauri\target\release\telegram-advisor.exe"
