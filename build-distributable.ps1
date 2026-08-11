$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TauriRoot = Join-Path $ProjectRoot "web-ui\src-tauri"
$ResourcesRoot = Join-Path $TauriRoot "resources"
$BackendResources = Join-Path $ResourcesRoot "backend"
$CodexResources = Join-Path $ResourcesRoot "codex\vendor"
$CodexPackage = Get-ChildItem (Join-Path $ProjectRoot "codex-gateway\node_modules\.pnpm") -Directory |
    Where-Object { $_.Name -match "^@openai\+codex@.+-win32-x64$" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$CodexVendorSource = if ($CodexPackage) {
    Join-Path $CodexPackage.FullName "node_modules\@openai\codex\vendor"
} else {
    ""
}
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Pnpm = "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"
$NodeBin = "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}
if (-not (Test-Path -LiteralPath $CodexVendorSource)) {
    throw "Bundled Codex runtime not found. Run codex-gateway\install-gateway.ps1 first."
}

New-Item -ItemType Directory -Force -Path $BackendResources | Out-Null
New-Item -ItemType Directory -Force -Path $CodexResources | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --name telegram-advisor-backend `
    --paths (Join-Path $ProjectRoot "src") `
    --distpath $BackendResources `
    --workpath (Join-Path $ProjectRoot "build\pyinstaller") `
    --specpath (Join-Path $ProjectRoot "build") `
    (Join-Path $ProjectRoot "packaging\backend_entry.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

Copy-Item -LiteralPath (Join-Path $CodexVendorSource "x86_64-pc-windows-msvc") -Destination $CodexResources -Recurse -Force

$env:PATH = "$NodeBin;$env:PATH"
& $Pnpm --dir (Join-Path $ProjectRoot "web-ui") exec tauri build --bundles msi
if ($LASTEXITCODE -ne 0) {
    throw "Tauri MSI build failed."
}

Write-Host "Installer created under web-ui\src-tauri\target\release\bundle\msi"
