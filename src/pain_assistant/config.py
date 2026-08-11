from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    analysis_backend: str
    openclaw_base_url: str | None
    openclaw_token: str | None
    openclaw_model: str
    openclaw_session_key: str | None
    openclaw_send_image: bool
    codex_bin: Path | None
    stub_response_path: Path | None
    openai_api_key: str | None
    openai_model: str
    db_path: Path
    save_screenshots: bool
    screenshot_dir: Path
    telegram_window_title: str
    crop_telegram_chat: bool
    telegram_left_sidebar_width: int
    telegram_right_panel_width: int
    server_screenshot_upload: bool
    server_screenshot_ssh_target: str | None
    server_screenshot_remote_dir: str
    local_outgoing_screenshot_dir: Path
    telegram_api_id: int | None
    telegram_api_hash: str | None
    telegram_phone: str | None
    telegram_session_path: Path
    telegram_personal_api_id: int | None
    telegram_personal_api_hash: str | None
    telegram_personal_phone: str | None
    telegram_personal_session_path: Path
    telegram_history_limit: int
    openclaw_ssh_tunnel_enabled: bool
    openclaw_ssh_tunnel_target: str | None
    openclaw_ssh_tunnel_local_port: int
    openclaw_ssh_tunnel_remote_host: str
    openclaw_ssh_tunnel_remote_port: int


def load_config() -> AppConfig:
    if not _as_bool(os.getenv("PAIN_IGNORE_DOTENV", "false")):
        load_dotenv()
    db_path = Path(os.getenv("APP_DB_PATH", "data/advisor.sqlite3"))
    screenshot_dir = Path(os.getenv("SCREENSHOT_DIR", "data/screenshots"))
    return AppConfig(
        analysis_backend=os.getenv("ANALYSIS_BACKEND", "openclaw"),
        openclaw_base_url=_clean_optional(os.getenv("OPENCLAW_BASE_URL")),
        openclaw_token=_clean_optional(os.getenv("OPENCLAW_TOKEN")),
        openclaw_model=os.getenv("OPENCLAW_MODEL", ""),
        openclaw_session_key=_clean_optional(os.getenv("OPENCLAW_SESSION_KEY")),
        openclaw_send_image=_as_bool(os.getenv("OPENCLAW_SEND_IMAGE", "false")),
        codex_bin=_clean_optional_path(os.getenv("CODEX_BIN")),
        stub_response_path=_clean_optional_path(os.getenv("STUB_RESPONSE_PATH")),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        db_path=db_path,
        save_screenshots=_as_bool(os.getenv("SAVE_SCREENSHOTS", "false")),
        screenshot_dir=screenshot_dir,
        telegram_window_title=os.getenv("TELEGRAM_WINDOW_TITLE", "Telegram"),
        crop_telegram_chat=_as_bool(os.getenv("CROP_TELEGRAM_CHAT", "true")),
        telegram_left_sidebar_width=_as_int(os.getenv("TELEGRAM_LEFT_SIDEBAR_WIDTH"), 460),
        telegram_right_panel_width=_as_int(os.getenv("TELEGRAM_RIGHT_PANEL_WIDTH"), 370),
        server_screenshot_upload=_as_bool(os.getenv("SERVER_SCREENSHOT_UPLOAD", "true")),
        server_screenshot_ssh_target=_clean_optional(os.getenv("SERVER_SCREENSHOT_SSH_TARGET")),
        server_screenshot_remote_dir=os.getenv(
            "SERVER_SCREENSHOT_REMOTE_DIR",
            r"C:\Users\user\.openclaw\workspace\incoming-screenshots",
        ),
        local_outgoing_screenshot_dir=Path(os.getenv("LOCAL_OUTGOING_SCREENSHOT_DIR", "data/outgoing-screenshots")),
        telegram_api_id=_as_optional_int(os.getenv("TELEGRAM_API_ID")),
        telegram_api_hash=_clean_optional(os.getenv("TELEGRAM_API_HASH")),
        telegram_phone=_clean_optional(os.getenv("TELEGRAM_PHONE")),
        telegram_session_path=Path(os.getenv("TELEGRAM_SESSION_PATH", "data/telegram.session")),
        telegram_personal_api_id=_as_optional_int(os.getenv("TELEGRAM_PERSONAL_API_ID")) or _as_optional_int(os.getenv("TELEGRAM_API_ID")),
        telegram_personal_api_hash=_clean_optional(os.getenv("TELEGRAM_PERSONAL_API_HASH")) or _clean_optional(os.getenv("TELEGRAM_API_HASH")),
        telegram_personal_phone=_clean_optional(os.getenv("TELEGRAM_PERSONAL_PHONE")),
        telegram_personal_session_path=Path(os.getenv("TELEGRAM_PERSONAL_SESSION_PATH", "data/telegram.personal.session")),
        telegram_history_limit=_as_int(os.getenv("TELEGRAM_HISTORY_LIMIT"), 500),
        openclaw_ssh_tunnel_enabled=_as_bool(os.getenv("OPENCLAW_SSH_TUNNEL_ENABLED", "false")),
        openclaw_ssh_tunnel_target=_clean_optional(os.getenv("OPENCLAW_SSH_TUNNEL_TARGET", "openclaw-server")),
        openclaw_ssh_tunnel_local_port=_as_int(os.getenv("OPENCLAW_SSH_TUNNEL_LOCAL_PORT"), 18790),
        openclaw_ssh_tunnel_remote_host=os.getenv("OPENCLAW_SSH_TUNNEL_REMOTE_HOST", "127.0.0.1"),
        openclaw_ssh_tunnel_remote_port=_as_int(os.getenv("OPENCLAW_SSH_TUNNEL_REMOTE_PORT"), 18790),
    )


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_optional_path(value: str | None) -> Path | None:
    cleaned = _clean_optional(value)
    return Path(cleaned) if cleaned else None


def _as_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _as_optional_int(value: str | None) -> int | None:
    cleaned = _clean_optional(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None
