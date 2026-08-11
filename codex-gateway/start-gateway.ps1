$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = if ($env:CODEX_GATEWAY_PORT) { $env:CODEX_GATEWAY_PORT } else { "18790" }
$OutLogPath = Join-Path $Root "gateway.out.log"
$ErrLogPath = Join-Path $Root "gateway.err.log"
$PidPath = Join-Path $Root "gateway.pid"

$env:CODEX_GATEWAY_HOST = if ($env:CODEX_GATEWAY_HOST) { $env:CODEX_GATEWAY_HOST } else { "127.0.0.1" }
$env:CODEX_GATEWAY_PORT = $Port
$env:CODEX_GATEWAY_WORKDIR = $Root

function Test-NodeRuntime {
    param([string]$Executable)

    if (-not $Executable) {
        return $false
    }
    try {
        $Version = & $Executable -p "process.versions.node" 2>$null
        $Major = [int](($Version -split "\.")[0])
        return $Major -ge 20
    }
    catch {
        return $false
    }
}

function Resolve-NodeRuntime {
    $Candidates = @()
    if ($env:CODEX_GATEWAY_NODE) {
        $Candidates += $env:CODEX_GATEWAY_NODE
    }

    if ($env:USERPROFILE) {
        $Candidates += Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    }

    $PathNode = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($PathNode) {
        $Candidates += $PathNode.Source
    }

    foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
        if (Test-NodeRuntime -Executable $Candidate) {
            return $Candidate
        }
    }

    throw "Node.js 20+ not found. Set CODEX_GATEWAY_NODE or install a current Node.js runtime."
}

function Get-ListeningPid {
    param([int]$TargetPort)

    $Connection = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($Connection) {
        return [int]$Connection.OwningProcess
    }

    $NetstatLine = netstat -ano | Select-String -Pattern (":$TargetPort\s+.*LISTENING\s+(\d+)$") |
        Select-Object -First 1
    if ($NetstatLine -and $NetstatLine.Matches[0].Groups[1].Value) {
        return [int]$NetstatLine.Matches[0].Groups[1].Value
    }

    return $null
}

function Test-GatewayProcess {
    param([int]$TargetPid)

    $ProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $TargetPid" -ErrorAction SilentlyContinue
    return $ProcessInfo -and $ProcessInfo.Name -eq "node.exe" -and $ProcessInfo.CommandLine -like "*server.js*"
}

$NodePath = Resolve-NodeRuntime
$LocalCodexEntry = Join-Path $Root "node_modules\@openai\codex\bin\codex.js"
if (-not $env:CODEX_BIN -and -not (Test-Path $LocalCodexEntry)) {
    throw "Local Codex CLI is not installed. Run .\install-gateway.ps1 first."
}

if (Test-Path $PidPath) {
    $ExistingPid = Get-Content $PidPath -ErrorAction SilentlyContinue
    if ($ExistingPid -and (Test-GatewayProcess -TargetPid ([int]$ExistingPid))) {
        Write-Host "codex-gateway already running: PID $ExistingPid"
        exit 0
    }
}

$ListeningPid = Get-ListeningPid -TargetPort ([int]$Port)
if ($ListeningPid) {
    if (Test-GatewayProcess -TargetPid $ListeningPid) {
        Set-Content -Path $PidPath -Value $ListeningPid -Encoding ascii
        Write-Host "codex-gateway already running: PID $ListeningPid"
        exit 0
    }

    throw "Port $Port is already in use by PID $ListeningPid"
}

$Command = "cmd.exe /d /s /c `"cd /d `"$Root`" && `"$NodePath`" server.js 1>`"$OutLogPath`" 2>`"$ErrLogPath`"`""
$Result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $Command }
if ($Result.ReturnValue -ne 0) {
    throw "Failed to start codex-gateway via WMI, return value $($Result.ReturnValue)"
}

$Deadline = (Get-Date).AddSeconds(8)
do {
    Start-Sleep -Milliseconds 250
    $ListeningPid = Get-ListeningPid -TargetPort ([int]$Port)
} while (-not $ListeningPid -and (Get-Date) -lt $Deadline)

if (-not $ListeningPid) {
    throw "codex-gateway process was created as PID $($Result.ProcessId), but port $Port did not start listening"
}

Set-Content -Path $PidPath -Value $ListeningPid -Encoding ascii
Write-Host "codex-gateway started: PID $ListeningPid, http://127.0.0.1:$Port"
Write-Host "node: $NodePath"
