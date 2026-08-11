$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundledPnpm = if ($env:USERPROFILE) {
    Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"
} else {
    $null
}

$PnpmPath = if ($env:CODEX_GATEWAY_PNPM) {
    $env:CODEX_GATEWAY_PNPM
} elseif ($BundledPnpm -and (Test-Path $BundledPnpm)) {
    $BundledPnpm
} else {
    $PnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($PnpmCommand) { $PnpmCommand.Source } else { $null }
}

Push-Location $Root
try {
    if ($PnpmPath) {
        & $PnpmPath install
    } else {
        npm install
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Package installation failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$CodexEntry = Join-Path $Root "node_modules\@openai\codex\bin\codex.js"
if (-not (Test-Path $CodexEntry)) {
    throw "@openai/codex was not installed correctly."
}

Write-Host "codex-gateway dependencies installed."
