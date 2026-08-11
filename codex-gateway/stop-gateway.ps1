$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = if ($env:CODEX_GATEWAY_PORT) { $env:CODEX_GATEWAY_PORT } else { "18790" }
$PidPath = Join-Path $Root "gateway.pid"

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

$Stopped = $false

if (Test-Path $PidPath) {
    $PidValue = Get-Content $PidPath
    if ($PidValue -and (Test-GatewayProcess -TargetPid ([int]$PidValue))) {
        Stop-Process -Id $PidValue -Force
        Write-Host "codex-gateway stopped: PID $PidValue"
        $Stopped = $true
    }
    Remove-Item $PidPath -Force
}

$ListeningPid = Get-ListeningPid -TargetPort ([int]$Port)
if ($ListeningPid -and (Test-GatewayProcess -TargetPid $ListeningPid)) {
    Stop-Process -Id $ListeningPid -Force
    Write-Host "codex-gateway stopped: PID $ListeningPid"
    $Stopped = $true
}

if ($ListeningPid -and -not (Test-GatewayProcess -TargetPid $ListeningPid)) {
    throw "Port $Port is used by another process (PID $ListeningPid); it was not stopped."
}

if (-not $Stopped) {
    Write-Host "codex-gateway is not running"
}
