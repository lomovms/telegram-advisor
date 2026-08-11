from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .screen_capture import Screenshot


@dataclass(frozen=True)
class UploadedScreenshot:
    local_path: Path
    remote_path: str


class ScreenshotUploadError(RuntimeError):
    pass


def upload_screenshot_to_server(
    screenshot: Screenshot,
    local_dir: Path,
    ssh_target: str,
    remote_dir: str,
) -> UploadedScreenshot:
    local_dir.mkdir(parents=True, exist_ok=True)
    filename = f"telegram-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    local_path = local_dir / filename
    local_path.write_bytes(screenshot.png_bytes)

    remote_dir_posix = _remote_posix_path(remote_dir)
    remote_path = f"{remote_dir_posix}/{filename}"
    _ensure_remote_dir(ssh_target, remote_dir)
    _scp(local_path, ssh_target, remote_path)
    _verify_remote_file(ssh_target, remote_dir, filename)
    return UploadedScreenshot(local_path=local_path, remote_path=remote_path)


def _ensure_remote_dir(ssh_target: str, remote_dir: str) -> None:
    escaped = remote_dir.replace("'", "''")
    command = (
        "powershell -NoProfile -Command "
        f"\"New-Item -ItemType Directory -Force -Path '{escaped}' | Out-Null\""
    )
    _run(["ssh", ssh_target, command], "Не удалось создать папку скриншотов на сервере")


def _scp(local_path: Path, ssh_target: str, remote_path: str) -> None:
    destination = f"{ssh_target}:{remote_path}"
    _run(["scp", str(local_path), destination], "Не удалось загрузить скриншот на сервер через scp")


def _verify_remote_file(ssh_target: str, remote_dir: str, filename: str) -> None:
    normalized_dir = remote_dir.rstrip("\\/")
    remote_file = f"{normalized_dir}\\{filename}"
    escaped = remote_file.replace("'", "''")
    command = (
        "powershell -NoProfile -Command "
        f"\"if (-not (Test-Path -LiteralPath '{escaped}')) {{ exit 2 }}\""
    )
    _run(["ssh", ssh_target, command], "Скриншот загружен через scp, но не найден на сервере")


def _run(command: list[str], error_message: str) -> None:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise ScreenshotUploadError(f"{error_message}. {details}")


def _remote_posix_path(remote_dir: str) -> str:
    return remote_dir.replace("\\", "/").rstrip("/")
