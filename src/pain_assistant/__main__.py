from __future__ import annotations

import sys
from dataclasses import replace

from .analysis_client import AnalysisClient
from .config import load_config
from .stub_client import StubAnalysisClient
from .telegram_api import login_telegram_console, login_telegram_qr_console, send_telegram_code_console
from .ui import run_app


def main() -> int:
    config = load_config()
    telegram_config = _telegram_config_from_args(config)
    if "--check" in sys.argv:
        AnalysisClient(config)
        print(f"pain_assistant ok; backend={config.analysis_backend}")
        return 0
    if "--stub-check" in sys.argv:
        stub_config = replace(config, analysis_backend="stub")
        client = AnalysisClient(stub_config)
        result = client.analyze_screenshot(b"stub-png-bytes", None, [], "деловой")
        print(StubAnalysisClient().raw_stub_json())
        print(f"stub result ok; best_reply={bool(result.best_reply)}")
        return 0
    if "--telegram-login" in sys.argv:
        login_telegram_console(telegram_config)
        return 0
    if "--telegram-login-sms" in sys.argv:
        login_telegram_console(telegram_config, force_sms=True)
        return 0
    if "--telegram-login-qr" in sys.argv:
        login_telegram_qr_console(telegram_config)
        return 0
    if "--telegram-send-code" in sys.argv:
        send_telegram_code_console(telegram_config)
        return 0
    if "--telegram-send-sms-code" in sys.argv:
        send_telegram_code_console(telegram_config, force_sms=True)
        return 0
    return run_app(config)


def _telegram_config_from_args(config):
    try:
        account = sys.argv[sys.argv.index("--telegram-account") + 1].strip().lower()
    except (ValueError, IndexError):
        account = "work"
    if account != "personal":
        return config
    return replace(
        config,
        telegram_api_id=config.telegram_personal_api_id,
        telegram_api_hash=config.telegram_personal_api_hash,
        telegram_phone=config.telegram_personal_phone,
        telegram_session_path=config.telegram_personal_session_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
