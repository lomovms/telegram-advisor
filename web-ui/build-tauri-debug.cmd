@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set "PATH=C:\Users\user\.cargo\bin;C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;%PATH%"
call "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" --dir C:\work\pain\web-ui exec tauri build --debug --no-bundle
