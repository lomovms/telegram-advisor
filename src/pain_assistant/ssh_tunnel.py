from __future__ import annotations

import socket
import subprocess
import time
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .app_log import log_event
from .config import AppConfig


class SshTunnelError(RuntimeError):
    pass


def ensure_openclaw_tunnel(config: AppConfig) -> bool:
    if not config.openclaw_ssh_tunnel_enabled:
        return False
    if not config.openclaw_base_url:
        return False
    if not _base_url_uses_local_tunnel(config):
        return False
    requires_health_check = config.analysis_backend.lower().strip() == "codex"
    if _is_port_open("127.0.0.1", config.openclaw_ssh_tunnel_local_port):
        if not requires_health_check or _is_gateway_healthy(config.openclaw_base_url):
            return False
        log_event("ssh tunnel: local port is open but Codex gateway health failed; restarting tunnel")
        _stop_ssh_listener_on_port(config.openclaw_ssh_tunnel_local_port)
    if not config.openclaw_ssh_tunnel_target:
        raise SshTunnelError("OPENCLAW_SSH_TUNNEL_TARGET пустой, туннель запустить нельзя.")

    forward = (
        f"{config.openclaw_ssh_tunnel_local_port}:"
        f"{config.openclaw_ssh_tunnel_remote_host}:"
        f"{config.openclaw_ssh_tunnel_remote_port}"
    )
    command = [
        "ssh",
        "-N",
        "-L",
        forward,
        config.openclaw_ssh_tunnel_target,
    ]
    log_event(f"ssh tunnel: starting {' '.join(command)}")
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
    except OSError as exc:
        raise SshTunnelError(f"Не удалось запустить ssh tunnel: {exc}") from exc

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        port_open = _is_port_open("127.0.0.1", config.openclaw_ssh_tunnel_local_port)
        healthy = not requires_health_check or _is_gateway_healthy(config.openclaw_base_url)
        if port_open and healthy:
            log_event("ssh tunnel: ready")
            return True
        time.sleep(0.25)

    raise SshTunnelError(
        "Не удалось поднять SSH tunnel до AI gateway. Проверь SSH alias "
        f"{config.openclaw_ssh_tunnel_target!r} и доступность сервера."
    )


def _base_url_uses_local_tunnel(config: AppConfig) -> bool:
    parsed = urlparse(config.openclaw_base_url or "")
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host in {"127.0.0.1", "localhost"} and port == config.openclaw_ssh_tunnel_local_port


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _is_gateway_healthy(base_url: str | None) -> bool:
    if not base_url:
        return False
    url = f"{base_url.rstrip('/')}/health"
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=1.5) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _stop_ssh_listener_on_port(port: int) -> None:
    pid = _listening_pid(port)
    if pid is None:
        return
    if _process_name(pid).lower() != "ssh":
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_creation_flags(),
        check=False,
    )


def _listening_pid(port: int) -> int | None:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
            "Select-Object -First 1 -ExpandProperty OwningProcess"
        ),
    ]
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=_creation_flags(),
        check=False,
    )
    value = result.stdout.strip()
    if not value:
        return None
    try:
        return int(value.splitlines()[0].strip())
    except ValueError:
        return None


def _process_name(pid: int) -> str:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName",
    ]
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=_creation_flags(),
        check=False,
    )
    return result.stdout.strip()


def _creation_flags() -> int:
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW
    return 0
