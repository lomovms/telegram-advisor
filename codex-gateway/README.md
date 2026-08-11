# Local Codex Gateway

Local HTTP service around `codex exec` for the Telegram desktop assistant.

It binds only to `127.0.0.1` by default. The Codex CLI is installed inside this
folder, so the service does not depend on the Microsoft Store executable.

## Endpoints

- `GET /health`
- `POST /v1/responses`

The response shape includes `output_text`, compatible with the current desktop client.

## Install

```powershell
cd C:\work\pain\codex-gateway
.\install-gateway.ps1
```

## Run

```powershell
.\start-gateway.ps1
Invoke-RestMethod http://127.0.0.1:18790/health
```

Starting `start_pain_advisor.cmd` also starts the gateway before the desktop app.

## Stop

```powershell
.\stop-gateway.ps1
```

## Configuration

Optional environment variables:

```powershell
$env:CODEX_GATEWAY_PORT="18790"
$env:CODEX_GATEWAY_MODEL=""
$env:CODEX_GATEWAY_TIMEOUT_MS="120000"
$env:CODEX_GATEWAY_NODE="C:\path\to\node.exe"
$env:CODEX_BIN="C:\path\to\codex.exe"
.\start-gateway.ps1
```

Defaults:

- host: `127.0.0.1`
- port: `18790`
- workdir: this gateway folder
- model: Codex CLI default
