from __future__ import annotations

import asyncio
import base64
import io
import threading
from dataclasses import dataclass
from typing import Callable

import qrcode

from .config import AppConfig
from .telegram_api import TelegramApiError, _connect, _session_password_needed_error, _validate_config


@dataclass
class TelegramAuthSnapshot:
    status: str = "idle"
    qr_data_url: str = ""
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "qr_data_url": self.qr_data_url,
            "message": self.message,
        }


class TelegramQrAuth:
    def __init__(self, config: AppConfig, on_authorized: Callable[[], None]) -> None:
        self.config = config
        self.on_authorized = on_authorized
        self._lock = threading.Lock()
        self._snapshot = TelegramAuthSnapshot()
        self._password = ""
        self._thread: threading.Thread | None = None

    def start(self) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._snapshot.as_dict()
            self._snapshot = TelegramAuthSnapshot(status="starting", message="Подключаюсь к Telegram...")
            self._password = ""
            self._thread = threading.Thread(target=self._thread_main, daemon=True, name="telegram-qr-auth")
            self._thread.start()
            return self._snapshot.as_dict()

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot.as_dict()

    def submit_password(self, password: str) -> dict:
        password = password.strip()
        if not password:
            raise TelegramApiError("Введите пароль двухэтапной аутентификации Telegram.")
        with self._lock:
            self._password = password
            self._snapshot.message = "Проверяю пароль..."
            return self._snapshot.as_dict()

    def _set(self, status: str, message: str, qr_data_url: str = "") -> None:
        with self._lock:
            self._snapshot = TelegramAuthSnapshot(status=status, message=message, qr_data_url=qr_data_url)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:
            self._set("error", str(exc))

    async def _run(self) -> None:
        _validate_config(self.config)
        client = await _connect(self.config)
        try:
            if await client.is_user_authorized():
                self.on_authorized()
                self._set("authorized", "Telegram уже авторизован.")
                return

            qr_login = await client.qr_login()
            while True:
                self._set(
                    "waiting_scan",
                    "Telegram на телефоне → Настройки → Устройства → Подключить устройство.",
                    _qr_data_url(qr_login.url),
                )
                try:
                    await qr_login.wait(timeout=30)
                    break
                except asyncio.TimeoutError:
                    await qr_login.recreate()
                except _session_password_needed_error():
                    self._set("password_required", "Введите пароль двухэтапной аутентификации Telegram.")
                    password = await self._wait_for_password()
                    await client.sign_in(password=password)
                    break

            self.on_authorized()
            self._set("authorized", "Telegram успешно подключён.")
        finally:
            await client.disconnect()

    async def _wait_for_password(self) -> str:
        while True:
            with self._lock:
                password = self._password
                self._password = ""
            if password:
                return password
            await asyncio.sleep(0.25)


def check_telegram_authorized(config: AppConfig) -> bool:
    async def _check() -> bool:
        _validate_config(config)
        client = await _connect(config)
        try:
            return bool(await client.is_user_authorized())
        finally:
            await client.disconnect()

    try:
        return bool(asyncio.run(_check()))
    except Exception:
        return False


def _qr_data_url(value: str) -> str:
    image = qrcode.make(value)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
