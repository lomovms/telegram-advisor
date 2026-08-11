from __future__ import annotations

import json
import mimetypes
import os
import re
import ssl
import threading
import time
import traceback
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import truststore

from .analysis_client import AnalysisClient
from .app_log import log_event
from .calendar_context import (
    DEFAULT_AFTER_HOURS_POLICY,
    DEFAULT_WEEKEND_POLICY,
    business_calendar_context,
)
from .config import AppConfig, load_config
from .db import Database
from .gmail_client import (
    GmailError,
    authorize_gmail,
    fetch_labeled_contacts,
    gmail_account_email,
    label_messages_from_contact_groups,
    list_google_contact_groups,
    protect_secret,
    send_gmail_reply,
    unprotect_secret,
)
from .models import AdvisorResult, ClientProfile
from .telegram_api import (
    download_telegram_avatar,
    import_all_messages_from_telegram,
    import_messages_from_telegram,
    import_new_messages_from_telegram,
    import_recent_contacts_from_telegram,
    listen_account_messages_from_telegram,
    send_telegram_message,
)
from .telegram_auth import TelegramQrAuth, check_telegram_authorized


class WebApiError(RuntimeError):
    pass


ANALYSIS_MODELS = {
    "gpt-5.6-luna": "Luna · экономная",
    "gpt-5.6-terra": "Terra · сбалансированная",
    "gpt-5.6-sol": "Sol · максимальное качество",
}

ASSISTANT_ROLES = {
    "general": {"label": "Универсальный", "context": "Работай как универсальный деловой помощник: уточняй цель, сохраняй факты и предлагай практичный следующий шаг."},
    "marketer": {"label": "Маркетолог", "context": "Работай как маркетолог: учитывай позиционирование, ценность для клиента, оффер, конверсию и ясный призыв к следующему действию. Не обещай результаты без оснований."},
    "developer": {"label": "Разработчик", "context": "Работай как опытный разработчик: уточняй требования, ограничения, стек, интеграции, оценку сроков и критерии готовности. Не выдумывай технические детали."},
    "project_manager": {"label": "Проджект", "context": "Работай как проектный менеджер: фиксируй договорённости, объём работ, сроки, ответственных, риски и следующий шаг. Не подтверждай то, что не было согласовано."},
}

REWRITE_MODES = {"my_style", "formal", "soft", "hard", "short", "correct"}
DEFAULT_CHAT_TOPICS = ["Авто", "Работа", "Флуд"]
DEFAULT_TOPIC_RULES = {
    "Работа": "Проекты, задачи, сроки, стоимость, оплата, правки, файлы, макеты и рабочие договорённости.",
    "Флуд": "Личные разговоры, шутки, мемы и сообщения без рабочей задачи или договорённости.",
}
DEFAULT_GMAIL_REPLY_BRIEF = "Ответить по сути последнего письма, кратко и по-деловому; при необходимости предложить следующий шаг."


class TelegramAccountLiveSession:
    def __init__(self, config: AppConfig, account: str, peers: dict[int, str], db: Database) -> None:
        safe_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"web-live-{account}").strip("._")
        live_session = config.telegram_session_path.with_name(
            f"{config.telegram_session_path.stem}.{safe_suffix}{config.telegram_session_path.suffix}"
        )
        if config.telegram_session_path.exists() and not live_session.exists():
            live_session.write_bytes(config.telegram_session_path.read_bytes())
        self.config = replace(config, telegram_session_path=live_session)
        self.account = account
        self.peers = peers
        self.db = db
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"web-telegram-live-{account}", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                listen_account_messages_from_telegram(
                    self.config,
                    self.peers,
                    self._save_message,
                    on_read=self._mark_read,
                    progress=lambda message: log_event(f"[{self.account}] {message}"),
                    stop_event=self.stop_event,
                    download_media=True,
                )
            except Exception as exc:
                log_event(f"web live Telegram failed for account {self.account}: {exc}")
            if self.stop_event.wait(5):
                break

    def _save_message(self, profile_id: int, message: dict) -> None:
        self.db.save_messages(profile_id, [message])

    def _mark_read(self, profile_id: int, max_message_id: int) -> None:
        self.db.mark_outgoing_messages_read(profile_id, max_message_id)


class AdvisorWebService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.db = Database(config.db_path)
        self.ai = AnalysisClient(config)
        self.ai.set_model(self.analysis_model())
        self._live_sessions: dict[str, TelegramAccountLiveSession] = {}
        self._live_lock = threading.Lock()
        self._analysis_lock = threading.Lock()
        self._project_scan_lock = threading.Lock()
        self._project_scan_status: dict[int, dict[str, object]] = {}
        self._gmail_label_lock = threading.Lock()
        self._gmail_label_status: dict[str, dict[str, object]] = {}
        self._telegram_locks = {"work": threading.Lock(), "personal": threading.Lock()}
        self._telegram_auth: dict[str, TelegramQrAuth] = {}
        self._telegram_auth_lock = threading.Lock()
        self._autopilot_stop = threading.Event()
        self._autopilot_lock = threading.Lock()
        self._autopilot_pending: dict[int, tuple[int, float]] = {}
        self._autopilot_status: dict[int, str] = {}
        self.start_live_sessions()
        self._autopilot_thread = threading.Thread(
            target=self._autopilot_loop,
            name="telegram-autopilot",
            daemon=True,
        )
        self._autopilot_thread.start()

    def close(self) -> None:
        self._autopilot_stop.set()
        with self._live_lock:
            sessions = list(self._live_sessions.values())
            self._live_sessions.clear()
        for session in sessions:
            session.stop()

    def list_profiles(self, account: str) -> list[dict]:
        selected_account = _normalize_account(account)
        return [
            self._profile_payload(profile)
            for profile in self.db.list_profiles()
            if self.profile_account(profile) == selected_account
        ]

    def projects(self) -> dict:
        projects = self.db.list_projects()
        leads = self.db.list_crm_leads()
        price = sum(int(item["price"] or 0) for item in projects)
        paid = sum(int(item["paid_amount"] or 0) for item in projects)
        payable = sum(int(item["payable"] or 0) for item in projects)
        paid_out = sum(int(item["paid_out"] or 0) for item in projects)
        receivable = max(0, price - paid)
        remaining_payable = max(0, payable - paid_out)
        return {
            "projects": projects,
            "leads": leads,
            "profiles": [{"id": profile.id, "chat_name": profile.chat_name} for profile in self.db.list_profiles()],
            "stats": {"total": len(projects), "price": price, "paid": paid, "receivable": receivable, "payable": payable, "paid_out": paid_out, "remaining_payable": remaining_payable, "balance": receivable - remaining_payable},
        }

    def _gmail_lead_source(self, account: dict[str, Any]) -> str:
        return f"gmail:{account.get('id', '')}"

    def save_gmail_lead(self, contact_id: str, raw: dict) -> dict:
        account = self._active_gmail_account()
        if not account:
            raise WebApiError("Сначала подключите Gmail.")
        contact = self._gmail_contact(contact_id)
        status = str(raw.get("status", "reply"))
        if status not in {"interested", "reply", "not_relevant"}:
            raise WebApiError("Некорректный статус контакта.")
        latest = contact.get("messages", [])[-1] if contact.get("messages") else {}
        return self.db.save_crm_lead({
            "source": self._gmail_lead_source(account),
            "external_id": str(contact["id"]),
            "contact_name": str(contact.get("name", "Контакт"))[:200],
            "email": str(contact.get("email", ""))[:320],
            "status": status,
            "category": str(raw.get("category", "")).strip()[:100],
            "summary": str(raw.get("summary", "")).strip()[:2000],
            "next_action": str(raw.get("next_action", "")).strip()[:500],
            "last_message_date": str(latest.get("date", "")),
        })

    def save_project(self, raw: dict) -> dict:
        title = str(raw.get("title", "")).strip()
        if not title:
            raise WebApiError("Укажите название задачи.")
        try:
            profile_id = int(raw["profile_id"]) if raw.get("profile_id") else None
        except (TypeError, ValueError) as exc:
            raise WebApiError("Некорректный контакт.") from exc
        if profile_id and not self.db.get_profile(profile_id):
            raise WebApiError("Контакт больше не существует.")
        try:
            project_id = int(raw["id"]) if raw.get("id") else None
            price = max(0, int(raw.get("price", 0) or 0))
            paid_amount = max(0, int(raw.get("paid_amount", 0) or 0))
            payable = max(0, int(raw.get("payable", 0) or 0))
            paid_out = max(0, int(raw.get("paid_out", 0) or 0))
        except (TypeError, ValueError) as exc:
            raise WebApiError("Суммы нужно указывать целыми рублями.") from exc
        status = str(raw.get("status", "planned"))
        if status not in {"planned", "progress", "waiting_payment", "done", "completed_by_me"}:
            raise WebApiError("Некорректный статус проекта.")
        return self.db.save_project({
            "id": project_id, "profile_id": profile_id, "title": title[:200], "price": price,
            "paid_amount": paid_amount, "payable": payable, "paid_out": paid_out, "status": status, "deadline": _project_date(raw.get("deadline")),
            "paid_at": _project_date(raw.get("paid_at")), "paid_out_at": _project_date(raw.get("paid_out_at")), "notes": str(raw.get("notes", "")).strip()[:2000],
        })

    def create_project_from_profile(self, profile_id: int) -> dict:
        profile = self._require_profile(profile_id)
        self._set_project_scan_status(profile_id, "running", "Отбираю сообщения за последние 31 день…", 0, 0)
        try:
            if self.db.get_setting(f"full_history_imported:{profile_id}") != "1":
                self._set_project_scan_status(profile_id, "history", "Загружаю всю историю из Telegram…", 0, 0)
                self.sync_history(profile_id)
            history = _recent_project_messages(self.db.all_messages(profile_id))
            chunks = _message_chunks(history, 180)
            saved, found = [], 0
            for index, chunk in enumerate(chunks, start=1):
                self._set_project_scan_status(profile_id, "analysis", f"Анализирую блок {index} из {len(chunks)}…", index, len(chunks))
                candidates = self.ai.extract_projects(profile, chunk)
                found += len(candidates)
                for candidate in candidates:
                    project = _project_candidate(profile_id, candidate)
                    if not project or self.db.project_by_profile_and_title(profile_id, project["title"]):
                        continue
                    saved.append(self.db.save_project(project))
            result = {"projects": saved, "found": found, "messages": len(history)}
            self._set_project_scan_status(profile_id, "done", f"Готово: за последние 31 день проверено сообщений {len(history)}, добавлено задач {len(saved)}.", len(chunks), len(chunks))
            return result
        except Exception as exc:
            self._set_project_scan_status(profile_id, "error", f"Ошибка: {exc}", 0, 0)
            raise

    def project_scan_status(self, profile_id: int) -> dict:
        self._require_profile(profile_id)
        with self._project_scan_lock:
            return dict(self._project_scan_status.get(profile_id, {"state": "idle", "message": "Полный анализ ещё не запускался.", "current": 0, "total": 0}))

    def _set_project_scan_status(self, profile_id: int, state: str, message: str, current: int, total: int) -> None:
        with self._project_scan_lock:
            self._project_scan_status[profile_id] = {"state": state, "message": message, "current": current, "total": total}

    def scan_projects(self, limit: int = 20) -> dict:
        profiles = [profile for profile in self.db.list_profiles() if self.contact_kind(profile.id) == "business"][:limit]
        created, reviewed = [], 0
        for profile in profiles:
            reviewed += 1
            result = self.create_project_from_profile(profile.id)
            created.extend(result["projects"])
        return {"reviewed": reviewed, "projects": created}

    def delete_project(self, project_id: int) -> None:
        self.db.delete_project(project_id)

    def profile_projects(self, profile_id: int) -> dict:
        self._require_profile(profile_id)
        return {"projects": self.db.list_projects_for_profile(profile_id)}

    def mark_project_completed_by_me(self, project_id: int) -> dict:
        project = self.db.mark_project_completed_by_me(project_id)
        if not project:
            raise WebApiError("Задача не найдена.")
        return project

    def onboarding_status(self) -> dict:
        accounts = {
            account: {
                "credentials_saved": self._telegram_credentials_saved(account),
                "authorized": self._telegram_account_authorized(account),
            }
            for account in ("work", "personal")
        }
        codex_installed = self.ai.codex_cli.available()
        codex_authenticated, codex_message = self.ai.codex_cli.login_status() if codex_installed else (False, "")
        return {
            "required": os.getenv("PAIN_ONBOARDING_REQUIRED", "").strip().lower() in {"1", "true", "yes"},
            "complete": bool(accounts["work"]["authorized"] and codex_authenticated),
            "accounts": accounts,
            "codex": {
                "installed": codex_installed,
                "authenticated": codex_authenticated,
                "message": codex_message,
            },
            "data_dir": str(self.config.db_path.resolve().parent),
        }

    def save_telegram_credentials(self, account: str, api_id: object, api_hash: str, phone: str) -> dict:
        selected = _normalize_account(account)
        try:
            numeric_api_id = int(str(api_id).strip())
        except ValueError as exc:
            raise WebApiError("Telegram API ID должен быть числом.") from exc
        clean_hash = api_hash.strip()
        if numeric_api_id <= 0:
            raise WebApiError("Telegram API ID должен быть положительным числом.")
        if len(clean_hash) < 16:
            raise WebApiError("Telegram API Hash выглядит слишком коротким.")
        self.db.set_setting(f"telegram_api_id:{selected}", str(numeric_api_id))
        self.db.set_setting(f"telegram_api_hash:{selected}", clean_hash)
        self.db.set_setting(f"telegram_phone:{selected}", phone.strip())
        self.db.set_setting(f"telegram_authorized:{selected}", "0")
        with self._telegram_auth_lock:
            self._telegram_auth.pop(selected, None)
        return self.onboarding_status()

    def start_telegram_auth(self, account: str) -> dict:
        selected = _normalize_account(account)
        if not self._telegram_credentials_saved(selected):
            raise WebApiError("Сначала сохраните Telegram API ID и API Hash.")
        with self._telegram_auth_lock:
            auth = self._telegram_auth.get(selected)
            if auth is None:
                auth = TelegramQrAuth(
                    self.telegram_config_for_account(selected),
                    lambda: self.db.set_setting(f"telegram_authorized:{selected}", "1"),
                )
                self._telegram_auth[selected] = auth
        return auth.start()

    def telegram_auth_status(self, account: str) -> dict:
        selected = _normalize_account(account)
        with self._telegram_auth_lock:
            auth = self._telegram_auth.get(selected)
        if auth is not None:
            return auth.snapshot()
        if self._telegram_account_authorized(selected):
            return {"status": "authorized", "qr_data_url": "", "message": "Telegram подключён."}
        return {"status": "idle", "qr_data_url": "", "message": ""}

    def submit_telegram_password(self, account: str, password: str) -> dict:
        selected = _normalize_account(account)
        with self._telegram_auth_lock:
            auth = self._telegram_auth.get(selected)
        if auth is None:
            raise WebApiError("Сессия авторизации Telegram не запущена.")
        return auth.submit_password(password)

    def start_codex_login(self) -> dict:
        try:
            self.ai.codex_cli.start_login()
        except Exception as exc:
            raise WebApiError(str(exc)) from exc
        return {"ok": True}

    def _telegram_credentials_saved(self, account: str) -> bool:
        config = self.telegram_config_for_account(account)
        return bool(config.telegram_api_id and config.telegram_api_hash)

    def _telegram_account_authorized(self, account: str) -> bool:
        if self.db.get_setting(f"telegram_authorized:{account}") == "1":
            return True
        config = self.telegram_config_for_account(account)
        if not self._telegram_credentials_saved(account) or not config.telegram_session_path.exists():
            return False
        authorized = check_telegram_authorized(config)
        if authorized:
            self.db.set_setting(f"telegram_authorized:{account}", "1")
        return authorized

    def profile_details(self, profile_id: int) -> dict:
        profile = self._require_profile(profile_id)
        payload = self._profile_payload(profile)
        payload["profile"] = asdict(profile)
        payload["messages"] = self.messages(profile_id, _analysis_message_limit(self.db))
        first_id = payload["messages"][0].get("telegram_message_id") if payload["messages"] else None
        payload["history_has_more"] = self.db.has_messages_before(profile_id, first_id)
        payload["last_analysis"] = self.last_analysis(profile_id)
        payload["conversation_goal"] = self.conversation_goal(profile_id)
        payload.update(self.topic_config(profile_id))
        payload.update(self.autopilot_config(profile_id))
        return payload

    def mark_profile_viewed(self, profile_id: int) -> dict:
        profile = self._require_profile(profile_id)
        latest_incoming_id = _max_incoming_message_id(self.db.recent_messages(profile_id, limit=500))
        self.db.set_setting(f"ui_last_seen_message_id:{profile_id}", str(latest_incoming_id))
        return self._profile_payload(profile)

    def messages(self, profile_id: int, limit: int) -> list[dict]:
        self._require_profile(profile_id)
        return self._decorate_messages(profile_id, self.db.recent_messages(profile_id, limit=max(20, min(500, limit))))

    def older_messages(self, profile_id: int, before_message_id: int, limit: int = 100) -> dict:
        self._require_profile(profile_id)
        messages, has_more = self.db.older_messages(profile_id, before_message_id, max(20, min(180, limit)))
        return {"messages": self._decorate_messages(profile_id, messages), "history_has_more": has_more}

    def _decorate_messages(self, profile_id: int, messages: list[dict]) -> list[dict]:
        for message in messages:
            message["out"] = bool(message.get("out"))
            media_path = str(message.pop("media_path", "") or "")
            message_id = message.get("telegram_message_id")
            message["media_url"] = (
                f"/api/profiles/{profile_id}/messages/{message_id}/media"
                if media_path and message_id is not None
                else ""
            )
        return messages

    def avatar_path(self, profile_id: int) -> Path | None:
        self._require_profile(profile_id)
        value = self.db.get_setting(f"telegram_avatar:{profile_id}").strip()
        path = Path(value) if value else None
        return path if path and path.exists() and path.is_file() else None

    def message_media_path(self, profile_id: int, telegram_message_id: int) -> Path | None:
        self._require_profile(profile_id)
        stored_path = self.db.message_media_path(profile_id, telegram_message_id).strip()
        if not stored_path:
            return None
        data_root = self.config.db_path.resolve().parent
        media_root = (data_root / "telegram-media").resolve()
        candidate = Path(stored_path)
        if not candidate.is_absolute():
            candidate = data_root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(media_root)
        except ValueError:
            return None
        return candidate if candidate.exists() and candidate.is_file() else None

    def send_message(self, profile_id: int, text: str, reply_to: int | None = None) -> dict:
        profile = self._require_profile(profile_id)
        text = text.strip()
        if not text:
            raise WebApiError("Нельзя отправить пустое сообщение.")
        peer = self.db.get_setting(f"telegram_peer:{profile_id}").strip()
        if not peer:
            raise WebApiError("У профиля нет Telegram-чата. Сначала импортируйте его через Telegram.")
        if reply_to is not None and reply_to <= 0:
            raise WebApiError("Некорректное сообщение для ответа.")
        with self._telegram_locks[self.profile_account(profile)]:
            sent = send_telegram_message(self.telegram_config(profile), peer, text, reply_to=reply_to)
        self.db.save_messages(profile_id, [sent])
        return sent

    def refresh_profile(self, profile_id: int) -> dict:
        profile = self._require_profile(profile_id)
        peer = self.db.get_setting(f"telegram_peer:{profile_id}").strip()
        after_id = self.db.max_telegram_message_id(profile_id)
        if not peer:
            raise WebApiError("Для обновления сначала выполните импорт Telegram-чата.")
        with self._telegram_locks[self.profile_account(profile)]:
            if after_id:
                _chat_name, messages = import_new_messages_from_telegram(
                    self.telegram_config(profile), peer, after_id, download_media=True
                )
            else:
                _chat_name, messages = import_messages_from_telegram(self.telegram_config(profile), peer)
        if messages:
            self.db.save_messages(profile_id, messages)
        if _profile_needs_ai_refresh(profile):
            refreshed = self.ai.build_profile(
                _chat_name or profile.chat_name,
                self.messages(profile_id, 500),
                profile,
            )
            refreshed.id = profile_id
            self.db.save_profile(refreshed)
        return {"added": len(messages), "profile": self._profile_payload(self._require_profile(profile_id))}

    def sync_history(self, profile_id: int) -> dict:
        profile = self._require_profile(profile_id)
        peer = self.db.get_setting(f"telegram_peer:{profile_id}").strip()
        if not peer:
            raise WebApiError("Для загрузки истории сначала выполните импорт Telegram-чата.")
        after_id = self.db.max_telegram_message_id(profile_id)
        with self._telegram_locks[self.profile_account(profile)]:
            chat_name, messages = import_all_messages_from_telegram(
                self.telegram_config(profile),
                peer,
                download_media=True,
            )
        if messages:
            self.db.save_messages(profile_id, messages)
        self.db.set_setting(f"full_history_imported:{profile_id}", "1")
        added = sum(
            1 for message in messages
            if int(message.get("telegram_message_id") or 0) > after_id
        )
        if _profile_needs_ai_refresh(profile):
            refreshed = self.ai.build_profile(chat_name or profile.chat_name, self.messages(profile_id, 500), profile)
            refreshed.id = profile_id
            self.db.save_profile(refreshed)
        return {"added": added, "profile": self._profile_payload(self._require_profile(profile_id))}

    def import_html_history(self, profile_id: int, files: object) -> dict:
        self._require_profile(profile_id)
        if not isinstance(files, list) or not files:
            raise WebApiError("Выберите HTML-файлы из экспорта Telegram.")
        messages: list[dict] = []
        for item in sorted(files, key=_telegram_html_file_order):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", ""))
            content = item.get("content")
            if not name.lower().endswith((".html", ".htm")) or not isinstance(content, str):
                continue
            if len(content) > 12_000_000:
                raise WebApiError(f"Файл {name} слишком большой для импорта.")
            messages.extend(_parse_telegram_html_history(content))
        messages = [message for message in messages if message.get("telegram_message_id")]
        if not messages:
            raise WebApiError("В выбранных файлах не найдены сообщения Telegram. Выберите files messages.html, messages2.html и далее.")
        messages.sort(key=lambda message: (str(message.get("message_date", "")), int(message["telegram_message_id"])))
        self.db.save_messages(profile_id, messages)
        self.db.set_setting(f"full_history_imported:{profile_id}", "1")
        return {"messages": len(messages), "files": len(files), "profile": self._profile_payload(self._require_profile(profile_id))}

    def import_telegram_chat(self, peer: str, account: str) -> dict:
        peer = peer.strip()
        if not peer:
            raise WebApiError("Укажите @username, id или точное название чата.")
        account = _normalize_account(account)
        telegram_config = self.telegram_config_for_account(account)
        with self._telegram_locks[account]:
            chat_name, messages = import_messages_from_telegram(telegram_config, peer)
        if not messages:
            raise WebApiError("В выбранном чате не найдено текстовых сообщений.")
        existing = self._profile_by_peer(peer, account) or self._profile_by_name(chat_name, account)
        profile = self.ai.build_profile(chat_name, messages, existing)
        profile.id = existing.id if existing else None
        profile_id = self.db.save_profile(profile)
        self.db.replace_messages(profile_id, messages)
        self.db.set_setting(f"telegram_peer:{profile_id}", peer)
        self.db.set_setting(f"telegram_account:{profile_id}", account)
        try:
            avatar = download_telegram_avatar(telegram_config, peer, self.config.db_path.parent / "avatars")
            if avatar:
                self.db.set_setting(f"telegram_avatar:{profile_id}", str(avatar))
        except Exception as exc:
            log_event(f"web Telegram avatar skipped for {profile_id}: {exc}")
        self.start_live_profile(profile_id)
        return self._profile_payload(self._require_profile(profile_id))

    def import_recent_contacts(self, account: str, limit: int = 10) -> dict:
        selected_account = _normalize_account(account)
        telegram_config = self.telegram_config_for_account(selected_account)
        with self._telegram_locks[selected_account]:
            contacts = import_recent_contacts_from_telegram(telegram_config, limit=limit)
        added = 0
        updated = 0
        avatars = 0
        for contact in contacts:
            peer = str(contact.get("peer", "")).strip()
            chat_name = str(contact.get("chat_name", peer)).strip() or peer
            if not peer:
                continue
            existing = self._profile_by_peer(peer, selected_account) or self._profile_by_name(
                chat_name, selected_account
            )
            if existing:
                profile_id = existing.id
                updated += 1
            else:
                profile_id = self.db.save_profile(
                    ClientProfile(
                        id=None,
                        chat_name=chat_name,
                        communication_style="Автоматически подключён из последних Telegram-диалогов.",
                        tone_recommendations="Профиль ещё не проходил глубокий AI-анализ.",
                    )
                )
                added += 1
            self.db.save_messages(profile_id, list(contact.get("messages", [])))
            self.db.set_setting(f"telegram_peer:{profile_id}", peer)
            self.db.set_setting(f"telegram_account:{profile_id}", selected_account)
            if not self.db.get_setting(f"telegram_avatar:{profile_id}").strip():
                try:
                    avatar = download_telegram_avatar(
                        telegram_config,
                        peer,
                        self.config.db_path.parent / "avatars",
                    )
                    if avatar:
                        self.db.set_setting(f"telegram_avatar:{profile_id}", str(avatar))
                        avatars += 1
                except Exception as exc:
                    log_event(f"web recent Telegram avatar skipped for {profile_id}: {exc}")
        self._restart_live_account(selected_account)
        return {
            "added": added,
            "updated": updated,
            "avatars": avatars,
            "total": len(contacts),
            "account": selected_account,
        }

    def rebuild_profile(self, profile_id: int) -> dict:
        profile = self._require_profile(profile_id)
        messages = self.messages(profile_id, 500)
        if not messages:
            raise WebApiError("В этом чате ещё нет сообщений для построения профиля.")
        refreshed = self.ai.build_profile(profile.chat_name, messages, profile)
        refreshed.id = profile_id
        self.db.save_profile(refreshed)
        return self._profile_payload(self._require_profile(profile_id))

    def analyze(
        self,
        profile_id: int,
        tone: str,
        user_comment: str,
        message_limit: int | None = None,
        topic_override: str | None = None,
    ) -> dict:
        profile = self._require_profile(profile_id)
        with self._analysis_lock:
            topic = topic_override or self.active_topic(profile_id)
            topic_key = topic.casefold()
            cursor_key = f"analysis_context_cursor:{profile_id}:topic:{topic_key}"
            summary_key = f"analysis_context_summary:{profile_id}:topic:{topic_key}"
            try:
                context_cursor = int(self.db.get_setting(cursor_key, "0"))
            except ValueError:
                context_cursor = 0
            history, next_cursor = self.db.analysis_context_messages(
                profile_id,
                context_cursor,
                initial_limit=message_limit or _analysis_message_limit(self.db),
            )
            history = _prepare_analysis_messages(history)
            style = self.db.get_setting("user_style_profile")
            memory = self.db.get_setting(summary_key)
            result = self.ai.analyze_screenshot(
                b"",
                profile,
                history,
                tone or "мой стиль",
                previous_analysis_context=memory,
                user_comment=self._role_context(profile_id, user_comment, topic),
                user_style_profile=style,
                business_calendar_context=business_calendar_context(
                    weekend_policy=self.weekend_policy(),
                    after_hours_policy=self.after_hours_policy(),
                    workday_start_hour=self.workday_start_hour(),
                    workday_end_hour=self.workday_end_hour(),
                ),
            )
            payload = asdict(result)
            payload["context"] = {
                "message_count": len(history),
                "contact": profile.chat_name,
                "relationship": "рабочие" if self.contact_kind(profile_id) == "business" else "личные",
                "tone": tone or "мой стиль",
                "goal": user_comment.strip() or "ответить по текущей ситуации",
                "topic": topic,
                "role": ASSISTANT_ROLES[self.assistant_role()]["label"],
                "topic_memory": bool(memory),
            }
            self.db.set_setting(
                f"last_analysis:{profile_id}:topic:{topic_key}",
                json.dumps(payload, ensure_ascii=False),
            )
            self.db.set_setting(f"ai_read_message_id:{profile_id}", str(self.db.max_telegram_message_id(profile_id)))
            self.db.set_setting(summary_key, _analysis_context_summary(result))
            self.db.set_setting(cursor_key, str(next_cursor))
            return payload

    def rewrite_message(self, profile_id: int, text: str, mode: str) -> dict:
        self._require_profile(profile_id)
        source_text = text.strip()
        if not source_text:
            raise WebApiError("Нет текста для переработки.")
        if len(source_text) > 4000:
            raise WebApiError("Для переработки выберите не более 4000 символов.")
        selected_mode = mode.strip().lower()
        if selected_mode not in REWRITE_MODES:
            raise WebApiError("Неизвестный режим переработки текста.")
        with self._analysis_lock:
            topic = self.active_topic(profile_id)
            result = self.ai.rewrite_message(
                source_text,
                selected_mode,
                _prepare_analysis_messages(self.db.recent_messages(profile_id, limit=12)),
                conversation_summary=(
                    f"Роль помощника: {self.assistant_role_context()}\n\n"
                    f"Тема: {topic}\n\n"
                    f"Описание темы: {self.topic_rule(profile_id, topic) or 'не задано'}\n\n"
                    f"{self.db.get_setting(f'analysis_context_summary:{profile_id}:topic:{topic.casefold()}') }"
                ),
                user_style_profile=self.user_style(),
            ).strip()
            if not result:
                raise WebApiError("ИИ не вернул переработанный текст.")
            self.db.set_setting(
                f"ai_read_message_id:{profile_id}",
                str(self.db.max_telegram_message_id(profile_id)),
            )
            return {"text": result, "mode": selected_mode}

    def last_analysis(self, profile_id: int, topic: str | None = None) -> dict | None:
        selected_topic = topic or self.active_topic(profile_id)
        raw = self.db.get_setting(f"last_analysis:{profile_id}:topic:{selected_topic.casefold()}")
        try:
            result = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return None
        return result if isinstance(result, dict) else None

    def delete_profile(self, profile_id: int) -> None:
        profile = self._require_profile(profile_id)
        account = self.profile_account(profile)
        self.db.delete_profile(profile_id)
        self._restart_live_account(account)

    def set_user_style(self, style: str) -> None:
        self.db.set_setting("user_style_profile", style.strip())

    def user_style(self) -> str:
        return self.db.get_setting("user_style_profile")

    def assistant_role(self) -> str:
        selected = self.db.get_setting("assistant_role", "general")
        return selected if selected in ASSISTANT_ROLES else "general"

    def assistant_role_context(self) -> str:
        return ASSISTANT_ROLES[self.assistant_role()]["context"]

    def set_assistant_role(self, role: str) -> str:
        selected = role.strip().lower()
        if selected not in ASSISTANT_ROLES:
            raise WebApiError("Неизвестная роль помощника.")
        self.db.set_setting("assistant_role", selected)
        return selected

    def topic_config(self, profile_id: int) -> dict:
        self._require_profile(profile_id)
        try:
            stored = json.loads(self.db.get_setting(f"chat_topics:{profile_id}", "[]"))
        except json.JSONDecodeError:
            stored = []
        topics = list(dict.fromkeys(DEFAULT_CHAT_TOPICS + [str(item).strip() for item in stored if str(item).strip()]))
        active = self.db.get_setting(f"active_chat_topic:{profile_id}", "Авто")
        if active not in topics:
            active = "Авто"
        try:
            stored_rules = json.loads(self.db.get_setting(f"chat_topic_rules:{profile_id}", "{}"))
        except json.JSONDecodeError:
            stored_rules = {}
        rules = dict(DEFAULT_TOPIC_RULES)
        if isinstance(stored_rules, dict):
            rules.update({str(key): str(value)[:1000] for key, value in stored_rules.items()})
        return {"topics": topics[:20], "active_topic": active, "topic_rules": rules}

    def active_topic(self, profile_id: int) -> str:
        return str(self.topic_config(profile_id)["active_topic"])

    def topic_rule(self, profile_id: int, topic: str) -> str:
        return str(self.topic_config(profile_id)["topic_rules"].get(topic, "")).strip()

    def set_active_topic(self, profile_id: int, topic: str, rule: str | None = None) -> dict:
        selected = " ".join(topic.strip().split())[:60]
        if not selected:
            raise WebApiError("Название темы пустое.")
        config = self.topic_config(profile_id)
        topics = list(config["topics"])
        if selected not in topics:
            topics.append(selected)
            self.db.set_setting(f"chat_topics:{profile_id}", json.dumps(topics, ensure_ascii=False))
        if rule is not None:
            rules = dict(config["topic_rules"])
            rules[selected] = rule.strip()[:1000]
            self.db.set_setting(f"chat_topic_rules:{profile_id}", json.dumps(rules, ensure_ascii=False))
        self.db.set_setting(f"active_chat_topic:{profile_id}", selected)
        result = self.topic_config(profile_id)
        result["last_analysis"] = self.last_analysis(profile_id, selected)
        return result

    def _role_context(self, profile_id: int, user_comment: str, topic: str) -> str:
        if topic == "Авто":
            rules = self.topic_config(profile_id)["topic_rules"]
            definitions = "\n".join(
                f"- {name}: {description}"
                for name, description in rules.items()
                if name != "Авто" and description
            )
            topic_context = (
                "Сам определи тему последних входящих сообщений по тексту и reply-связям. "
                "Не смешивай её с флудом, старыми проектами и несвязанными договорённостями.\n"
                f"Определения тем пользователя:\n{definitions or 'не заданы'}"
            )
        elif topic == "Флуд":
            topic_context = "Текущая тема — неформальный флуд. Не подтягивай рабочие договорённости без прямой необходимости."
        else:
            rule = self.topic_rule(profile_id, topic)
            topic_context = (
                f"Текущая тема — «{topic}». Отвечай только в рамках этой темы; "
                "игнорируй флуд и сообщения о других проектах, кроме явно необходимых связей. "
                f"Определение пользователя: {rule or 'не задано'}."
            )
        return (
            f"Рабочая роль помощника: {self.assistant_role_context()}\n\n"
            f"Контекст темы: {topic_context}\n\n{user_comment.strip()}"
        ).strip()

    def analysis_model(self) -> str:
        configured = self.db.get_setting("analysis_model", self.config.openclaw_model)
        return _normalize_analysis_model(configured)

    def set_analysis_model(self, model: str) -> str:
        selected = _normalize_analysis_model(model, strict=True)
        self.db.set_setting("analysis_model", selected)
        self.ai.set_model(selected)
        return selected

    def set_weekend_policy(self, policy: str) -> None:
        self.db.set_setting("weekend_policy", policy.strip() or DEFAULT_WEEKEND_POLICY)

    def weekend_policy(self) -> str:
        return self.db.get_setting("weekend_policy", DEFAULT_WEEKEND_POLICY)

    def set_after_hours_policy(self, policy: str) -> None:
        self.db.set_setting("after_hours_policy", policy.strip() or DEFAULT_AFTER_HOURS_POLICY)

    def after_hours_policy(self) -> str:
        return self.db.get_setting("after_hours_policy", DEFAULT_AFTER_HOURS_POLICY)

    def set_workday_end_hour(self, hour: object) -> None:
        try:
            normalized = max(0, min(23, int(hour)))
        except (TypeError, ValueError):
            normalized = 19
        self.db.set_setting("workday_end_hour", str(normalized))

    def workday_end_hour(self) -> int:
        try:
            return max(0, min(23, int(self.db.get_setting("workday_end_hour", "19"))))
        except ValueError:
            return 19

    def set_workday_start_hour(self, hour: object) -> None:
        try:
            normalized = max(0, min(23, int(hour)))
        except (TypeError, ValueError):
            normalized = 9
        self.db.set_setting("workday_start_hour", str(normalized))

    def workday_start_hour(self) -> int:
        try:
            return max(0, min(23, int(self.db.get_setting("workday_start_hour", "9"))))
        except ValueError:
            return 9

    def contact_kind(self, profile_id: int) -> str:
        profile = self._require_profile(profile_id)
        fallback = "business" if self.profile_account(profile) == "work" else "personal"
        value = self.db.get_setting(f"contact_kind:{profile_id}", fallback)
        return "business" if value == "business" else "personal"

    def conversation_goal(self, profile_id: int) -> str:
        self._require_profile(profile_id)
        return self.db.get_setting(f"conversation_goal:{profile_id}")

    def set_conversation_goal(self, profile_id: int, goal: str) -> dict:
        profile = self._require_profile(profile_id)
        self.db.set_setting(f"conversation_goal:{profile_id}", goal.strip())
        return {"conversation_goal": self.conversation_goal(profile.id or profile_id)}

    def set_contact_kind(self, profile_id: int, kind: str) -> dict:
        profile = self._require_profile(profile_id)
        normalized = "business" if kind == "business" else "personal"
        self.db.set_setting(f"contact_kind:{profile_id}", normalized)
        return self._profile_payload(profile)

    def notification_settings(self) -> dict:
        return {
            "notifications_enabled": self.db.get_setting("notifications_enabled") == "1",
            "notification_chat": self.db.get_setting("notification_chat"),
        }

    def max_status(self) -> dict:
        return {
            "max_connected": bool(self.db.get_setting("max_bot_token")),
            "max_bot_name": self.db.get_setting("max_bot_name"),
        }

    def connect_max(self, token: str) -> dict:
        token = token.strip()
        if not token:
            raise WebApiError("Вставьте токен бота MAX.")
        try:
            ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            response = httpx.get(
                "https://platform-api2.max.ru/me",
                headers={"Authorization": token},
                timeout=15,
                verify=ssl_context,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.ConnectError as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                raise WebApiError(
                    "Windows не доверяет сертификату MAX. Установите корневой сертификат Минцифры в «Доверенные корневые центры сертификации», затем повторите подключение."
                ) from exc
            raise WebApiError("Не удалось подключиться к API MAX. Проверьте интернет и повторите попытку.") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise WebApiError("MAX не принял токен. Проверьте его в расширенных настройках бота.") from exc
        if not payload.get("is_bot"):
            raise WebApiError("Этот токен не принадлежит боту MAX.")
        self.db.set_setting("max_bot_token", protect_secret(token))
        self.db.set_setting("max_bot_name", str(payload.get("name") or payload.get("username") or "MAX-бот"))
        self.db.set_setting("max_bot_user_id", str(payload.get("user_id", "")))
        return self.max_status()

    def _max_token(self) -> str:
        protected = self.db.get_setting("max_bot_token")
        if not protected:
            raise WebApiError("Сначала подключите бота MAX в разделе «Подключения».")
        return unprotect_secret(protected)

    def _max_contacts_cache(self) -> list[dict]:
        try:
            cached = json.loads(self.db.get_setting("max_contacts_cache", "[]"))
        except json.JSONDecodeError:
            cached = []
        return cached if isinstance(cached, list) else []

    def _max_message_item(self, message: dict, contact: dict) -> dict:
        sender = message.get("sender") or {}
        body = message.get("body") or {}
        timestamp = int(message.get("timestamp") or 0)
        date = datetime.fromtimestamp(timestamp / 1000 if timestamp > 10_000_000_000 else timestamp, tz=timezone.utc).isoformat() if timestamp else datetime.now(timezone.utc).isoformat()
        sender_name = " ".join(str(sender.get(key, "")).strip() for key in ("first_name", "last_name")).strip() or str(sender.get("username") or contact.get("name", "MAX"))
        return {
            "id": str(message.get("message_id") or message.get("id") or f"{contact['id']}:{timestamp}:{len(contact.get('messages', []))}"),
            "thread_id": "",
            "sender": sender_name,
            "subject": "MAX",
            "date": date,
            "timestamp": timestamp,
            "text": str(body.get("text") or "[Вложение]"),
            "technical_text": "",
            "out": str(sender.get("user_id", "")) == self.db.get_setting("max_bot_user_id"),
        }

    def _max_add_message(self, contact: dict, item: dict) -> None:
        if not any(
            existing.get("id") == item["id"]
            or (
                existing.get("timestamp") == item.get("timestamp")
                and existing.get("sender") == item.get("sender")
                and existing.get("text") == item.get("text")
                and existing.get("out") == item.get("out")
            )
            for existing in contact["messages"]
        ):
            contact["messages"].append(item)
            contact["messages"].sort(key=lambda entry: str(entry.get("date", "")))
        latest = contact["messages"][-1] if contact["messages"] else item
        contact.update({"date": latest.get("date", ""), "preview": latest.get("text", ""), "message_count": len(contact["messages"])})

    def _max_load_chat_history(self, contact: dict) -> None:
        if contact.get("max_target_type") != "chat":
            return
        try:
            response = httpx.get(
                "https://platform-api2.max.ru/messages",
                headers={"Authorization": self._max_token()},
                params={"chat_id": contact.get("max_target_id"), "count": 100},
                timeout=15,
                verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            )
            response.raise_for_status()
            for message in response.json().get("messages", []):
                self._max_add_message(contact, self._max_message_item(message, contact))
        except (httpx.HTTPError, ValueError) as exc:
            log_event(f"MAX history fetch failed for {contact.get('id')}: {exc}")

    def _max_dedupe_messages(self, contact: dict) -> None:
        unique: list[dict] = []
        seen: set[tuple[object, object, object, object]] = set()
        for message in sorted(contact.get("messages", []), key=lambda entry: str(entry.get("date", ""))):
            key = (message.get("timestamp"), message.get("sender"), message.get("text"), message.get("out"))
            if key not in seen:
                seen.add(key)
                unique.append(message)
        contact["messages"] = unique
        if unique:
            contact.update({"date": unique[-1].get("date", ""), "preview": unique[-1].get("text", ""), "message_count": len(unique)})

    def max_messages(self, force_refresh: bool = False) -> dict:
        contacts = self._max_contacts_cache()
        if not force_refresh:
            return {"contacts": contacts, **self.max_status()}
        if not self.db.get_setting("max_bot_user_id"):
            try:
                me = httpx.get("https://platform-api2.max.ru/me", headers={"Authorization": self._max_token()}, timeout=15, verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)).json()
                self.db.set_setting("max_bot_user_id", str(me.get("user_id", "")))
            except (httpx.HTTPError, ValueError):
                pass
        marker = self.db.get_setting("max_updates_marker").strip()
        try:
            response = httpx.get(
                "https://platform-api2.max.ru/updates",
                headers={"Authorization": self._max_token()},
                params={"marker": marker or None, "timeout": 0, "types": "message_created"},
                timeout=15,
                verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WebApiError("Не удалось получить сообщения MAX.") from exc
        if payload.get("marker") is not None:
            self.db.set_setting("max_updates_marker", str(payload["marker"]))
        by_id = {str(contact.get("id")): contact for contact in contacts}
        for update in payload.get("updates", []):
            message = update.get("message") or {}
            sender = message.get("sender") or update.get("user") or {}
            recipient = message.get("recipient") or {}
            user_id = sender.get("user_id")
            chat_id = update.get("chat_id") or recipient.get("chat_id")
            if not user_id and not chat_id:
                continue
            target_type, target_id = ("chat", chat_id) if chat_id else ("user", user_id)
            contact_id = f"{target_type}:{target_id}"
            name = " ".join(str(sender.get(key, "")).strip() for key in ("first_name", "last_name")).strip()
            name = name or str(sender.get("username") or f"MAX {target_type} {target_id}")
            username = str(sender.get("username") or "")
            timestamp = int(message.get("timestamp") or update.get("timestamp") or 0)
            date = datetime.fromtimestamp(timestamp / 1000 if timestamp > 10_000_000_000 else timestamp, tz=timezone.utc).isoformat() if timestamp else datetime.now(timezone.utc).isoformat()
            body = message.get("body") or {}
            text = str(body.get("text") or "[Вложение]")
            contact = by_id.setdefault(contact_id, {"id": contact_id, "name": name, "email": f"@{username}" if username else f"MAX {target_type} {target_id}", "date": date, "preview": text, "message_count": 0, "thread_ids": [], "messages": [], "max_target_type": target_type, "max_target_id": target_id})
            contact.update({"name": name, "email": f"@{username}" if username else contact.get("email", f"MAX {target_type} {target_id}")})
            self._max_add_message(contact, self._max_message_item(message, contact))
        for contact in by_id.values():
            self._max_load_chat_history(contact)
            self._max_dedupe_messages(contact)
        contacts = sorted(by_id.values(), key=lambda contact: str(contact.get("date", "")), reverse=True)
        self.db.set_setting("max_contacts_cache", json.dumps(contacts, ensure_ascii=False))
        return {"contacts": contacts, **self.max_status()}

    def send_max(self, contact_id: str, text: str) -> dict:
        text = text.strip()
        contacts = self._max_contacts_cache()
        contact = next((item for item in contacts if str(item.get("id")) == contact_id), None)
        if not contact or not text:
            raise WebApiError("Выберите контакт MAX и введите сообщение.")
        target_type = str(contact.get("max_target_type", "user"))
        target_id = contact.get("max_target_id")
        try:
            response = httpx.post(
                "https://platform-api2.max.ru/messages",
                headers={"Authorization": self._max_token()},
                params={f"{target_type}_id": target_id},
                json={"text": text},
                timeout=15,
                verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise WebApiError("Не удалось отправить сообщение через MAX.") from exc
        sent = payload.get("message") or {}
        item = self._max_message_item(sent, contact) if sent else {
            "id": f"local:{time.time_ns()}", "thread_id": "", "sender": self.max_status()["max_bot_name"], "subject": "MAX",
            "date": datetime.now(timezone.utc).isoformat(), "timestamp": int(time.time() * 1000), "text": text, "technical_text": "", "out": True,
        }
        self._max_add_message(contact, item)
        self.db.set_setting("max_contacts_cache", json.dumps(contacts, ensure_ascii=False))
        return {"sent": True}

    def analyze_max(self, contact_id: str, tone: str, brief: str) -> dict:
        contact = next((item for item in self._max_contacts_cache() if str(item.get("id")) == contact_id), None)
        if not contact:
            raise WebApiError("Контакт MAX не найден. Обновите сообщения.")
        task = brief.strip() or "Ответить на последнее сообщение кратко и по делу."
        self.db.set_setting("max_reply_brief", task)
        history = [{"message_date": message.get("date", ""), "sender": "Вы" if message.get("out") else message.get("sender", contact["name"]), "out": bool(message.get("out")), "text": str(message.get("text", ""))[:6000]} for message in contact.get("messages", [])[-40:]]
        profile = ClientProfile(id=None, chat_name=f"{contact['name']} ({contact['email']})", communication_style="Переписка в MAX.", tone_recommendations="Ответ без служебных заголовков.")
        with self._analysis_lock:
            result = self.ai.analyze_screenshot(b"", profile, history, tone or "деловой", user_comment=f"Подготовь ответ на последнее входящее сообщение MAX. ТЗ пользователя: {task}. Верни только текст сообщения в вариантах ответа.", user_style_profile=self.user_style(), business_calendar_context=business_calendar_context(weekend_policy=self.weekend_policy(), after_hours_policy=self.after_hours_policy(), workday_start_hour=self.workday_start_hour(), workday_end_hour=self.workday_end_hour()))
        payload = asdict(result)
        payload["context"] = {"message_count": len(history), "contact": profile.chat_name, "relationship": "MAX", "tone": tone or "деловой", "goal": task, "topic": "MAX", "role": ASSISTANT_ROLES[self.assistant_role()]["label"], "topic_memory": False}
        return payload

    def set_notification_settings(self, enabled: bool, chat: str) -> None:
        self.db.set_setting("notifications_enabled", "1" if enabled else "0")
        self.db.set_setting("notification_chat", chat.strip())

    def test_notifications(self) -> dict:
        chat = self.db.get_setting("notification_chat").strip()
        if not chat:
            raise WebApiError("Укажите ссылку или название группы уведомлений.")
        results: dict[str, str] = {}
        for account in ("work", "personal"):
            try:
                with self._telegram_locks[account]:
                    send_telegram_message(
                        self.telegram_config_for_account(account),
                        chat,
                        f"Telegram Advisor: тест уведомлений. Аккаунт: {_account_label(account)}.",
                    )
                results[account] = "sent"
            except Exception as exc:
                results[account] = str(exc)
                log_event(f"notification test failed for {account}: {exc}")
        if not any(value == "sent" for value in results.values()):
            raise WebApiError("Не удалось отправить тестовое уведомление ни с одного аккаунта.")
        return {"results": results}

    def _gmail_accounts(self) -> list[dict[str, Any]]:
        raw = self.db.get_setting("gmail_accounts")
        try:
            accounts = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            accounts = []
        if isinstance(accounts, list) and accounts:
            return [account for account in accounts if isinstance(account, dict) and account.get("id")]

        # Migrate the original single Gmail connection without making the user connect again.
        refresh_token = self.db.get_setting("gmail_refresh_token")
        cache = self.db.get_setting("gmail_contacts_cache")
        has_legacy_gmail_data = bool(
            refresh_token
            or cache
            or self.db.get_setting("gmail_contacts_cache_label")
            or self.db.get_setting("gmail_label")
        )
        if not has_legacy_gmail_data:
            return []
        legacy = {
            "id": "legacy",
            "email": self.db.get_setting("gmail_email") or "Подключённая почта",
            "client_id": self.db.get_setting("gmail_client_id"),
            "client_secret": self.db.get_setting("gmail_client_secret"),
            "refresh_token": refresh_token,
            "can_send": self.db.get_setting("gmail_can_send") == "1",
            "label": self.db.get_setting("gmail_label", "Advisor"),
            "contacts_cache": cache,
            "contacts_cache_label": self.db.get_setting("gmail_contacts_cache_label"),
            "contacts_cache_at": self.db.get_setting("gmail_contacts_cache_at"),
        }
        self._save_gmail_accounts([legacy], "legacy")
        return [legacy]

    def _save_gmail_accounts(self, accounts: list[dict[str, Any]], active_id: str | None = None) -> None:
        self.db.set_setting("gmail_accounts", json.dumps(accounts, ensure_ascii=False))
        if active_id:
            self.db.set_setting("gmail_active_account_id", active_id)
        active = next((item for item in accounts if item.get("id") == (active_id or self.db.get_setting("gmail_active_account_id"))), accounts[0] if accounts else None)
        if not active:
            return
        # Keep legacy keys synchronized for installations upgraded from the one-account version.
        self.db.set_setting("gmail_client_id", str(active.get("client_id", "")))
        self.db.set_setting("gmail_client_secret", str(active.get("client_secret", "")))
        self.db.set_setting("gmail_refresh_token", str(active.get("refresh_token", "")))
        self.db.set_setting("gmail_can_send", "1" if active.get("can_send") else "0")
        self.db.set_setting("gmail_label", str(active.get("label", "Advisor")))
        self.db.set_setting("gmail_email", str(active.get("email", "")))
        self.db.set_setting("gmail_contacts_cache", str(active.get("contacts_cache", "")))
        self.db.set_setting("gmail_contacts_cache_label", str(active.get("contacts_cache_label", "")))
        self.db.set_setting("gmail_contacts_cache_at", str(active.get("contacts_cache_at", "")))

    def _active_gmail_account(self) -> dict[str, Any] | None:
        accounts = self._gmail_accounts()
        active_id = self.db.get_setting("gmail_active_account_id")
        account = next((item for item in accounts if item.get("id") == active_id), None)
        if account:
            return account
        if accounts:
            self._save_gmail_accounts(accounts, str(accounts[0]["id"]))
            return accounts[0]
        return None

    def gmail_status(self) -> dict:
        accounts = self._gmail_accounts()
        active = self._active_gmail_account()
        if active and active.get("email") in {"", "Подключённая почта"} and active.get("client_id") and active.get("client_secret") and active.get("refresh_token"):
            try:
                active["email"] = gmail_account_email(
                    str(active["client_id"]),
                    unprotect_secret(str(active["client_secret"])),
                    unprotect_secret(str(active["refresh_token"])),
                ) or "Подключённая почта"
                self._save_gmail_accounts([active if item.get("id") == active.get("id") else item for item in accounts], str(active["id"]))
                accounts = self._gmail_accounts()
                active = self._active_gmail_account()
            except GmailError as exc:
                log_event(f"gmail account email lookup failed: {exc}")
        return {
            "gmail_connected": bool(active and active.get("refresh_token")),
            "gmail_can_send": bool(active and active.get("can_send")),
            "gmail_client_id": str(active.get("client_id", "")) if active else "",
            "gmail_label": str(active.get("label", "Advisor")) if active else self.db.get_setting("gmail_label", "Advisor"),
            "gmail_email": str(active.get("email", "")) if active else "",
            "gmail_active_account_id": str(active.get("id", "")) if active else "",
            "gmail_accounts": [
                {"id": str(item.get("id", "")), "email": str(item.get("email", "")), "label": str(item.get("label", "Advisor")), "connected": bool(item.get("refresh_token")), "can_send": bool(item.get("can_send"))}
                for item in accounts
            ],
            "gmail_reply_brief": self.db.get_setting("gmail_reply_brief", DEFAULT_GMAIL_REPLY_BRIEF),
        }

    def connect_gmail(self, client_id: str, client_secret: str, label: str) -> dict:
        normalized_id = client_id.strip()
        normalized_secret = client_secret.strip()
        if not normalized_id or not normalized_secret:
            raise WebApiError("Укажите OAuth Client ID и Client Secret.")
        log_event("gmail OAuth authorization started")
        try:
            tokens = authorize_gmail(normalized_id, normalized_secret)
        except GmailError as exc:
            log_event(f"gmail OAuth authorization failed: {exc}")
            if "invalid_client" in str(exc):
                raise WebApiError(
                    "Google отклонил OAuth Client ID/Secret. Скопируйте оба значения из одного Desktop OAuth-клиента."
                ) from exc
            raise WebApiError(str(exc)) from exc
        except Exception as exc:
            log_event(f"gmail OAuth authorization crashed: {exc}")
            raise WebApiError("Не удалось завершить авторизацию Gmail. Повторите подключение.") from exc
        log_event("gmail OAuth authorization received tokens")
        try:
            email = gmail_account_email(normalized_id, normalized_secret, str(tokens["refresh_token"]))
        except Exception as exc:
            log_event(f"gmail account email lookup after OAuth failed: {exc}")
            email = "Подключённая почта"
        accounts = self._gmail_accounts()
        account_id = next((str(item["id"]) for item in accounts if str(item.get("email", "")).casefold() == email.casefold()), f"gmail-{int(time.time() * 1000)}")
        account = {
            "id": account_id,
            "email": email or "Подключённая почта",
            "client_id": normalized_id,
            "client_secret": protect_secret(normalized_secret),
            "refresh_token": protect_secret(str(tokens["refresh_token"])),
            "can_send": True,
            "label": label.strip() or "Advisor",
            "contacts_cache": "",
            "contacts_cache_label": "",
            "contacts_cache_at": "",
        }
        accounts = [item for item in accounts if item.get("id") != account_id] + [account]
        self._save_gmail_accounts(accounts, account_id)
        log_event(f"gmail OAuth account saved: {account_id} ({account['email']})")
        return self.gmail_status()

    def reconnect_gmail(self) -> dict:
        account = self._active_gmail_account()
        client_id = str(account.get("client_id", "")).strip() if account else ""
        protected_secret = str(account.get("client_secret", "")) if account else ""
        if not client_id or not protected_secret:
            raise WebApiError("Сначала укажите OAuth Client ID и Client Secret.")
        try:
            tokens = authorize_gmail(client_id, unprotect_secret(protected_secret))
        except GmailError as exc:
            raise WebApiError(str(exc)) from exc
        account["refresh_token"] = protect_secret(str(tokens["refresh_token"]))
        account["can_send"] = True
        accounts = self._gmail_accounts()
        self._save_gmail_accounts([account if item.get("id") == account.get("id") else item for item in accounts], str(account["id"]))
        return self.gmail_status()

    def set_gmail_label(self, label: str) -> dict:
        account = self._active_gmail_account()
        if not account:
            self.db.set_setting("gmail_label", label.strip() or "Advisor")
            return self.gmail_status()
        account["label"] = label.strip() or "Advisor"
        account["contacts_cache"] = ""
        accounts = self._gmail_accounts()
        self._save_gmail_accounts([account if item.get("id") == account.get("id") else item for item in accounts], str(account["id"]))
        return self.gmail_status()

    def set_active_gmail_account(self, account_id: str) -> dict:
        accounts = self._gmail_accounts()
        if not any(str(account.get("id")) == account_id for account in accounts):
            raise WebApiError("Почтовый аккаунт не найден.")
        self._save_gmail_accounts(accounts, account_id)
        return self.gmail_status()

    def gmail_messages(self, force_refresh: bool = False) -> dict:
        status = self.gmail_status()
        if not status["gmail_connected"]:
            raise WebApiError("Сначала подключите Gmail в разделе «Стиль».")
        account = self._active_gmail_account()
        if not account:
            raise WebApiError("Сначала подключите Gmail.")
        cache_label = str(account.get("contacts_cache_label", ""))
        cache_raw = str(account.get("contacts_cache", ""))
        if not force_refresh and cache_raw and cache_label == status["gmail_label"]:
            try:
                contacts = json.loads(cache_raw)
                if isinstance(contacts, list):
                    return {
                        "contacts": contacts,
                        "gmail_cached": True,
                        "gmail_cached_at": str(account.get("contacts_cache_at", "")),
                        **status,
                    }
            except json.JSONDecodeError:
                pass
        try:
            contacts = fetch_labeled_contacts(
                str(account.get("client_id", "")),
                unprotect_secret(str(account.get("client_secret", ""))),
                unprotect_secret(str(account.get("refresh_token", ""))),
                str(status["gmail_label"]),
            )
        except GmailError as exc:
            raise WebApiError(str(exc)) from exc
        source = self._gmail_lead_source(account)
        for contact in contacts:
            contact["crm_lead"] = self.db.crm_lead(source, str(contact.get("id", "")))
        cached_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        account["contacts_cache"] = json.dumps(contacts, ensure_ascii=False)
        account["contacts_cache_label"] = str(status["gmail_label"])
        account["contacts_cache_at"] = cached_at
        if account.get("email") in {"", "Подключённая почта"}:
            try:
                account["email"] = gmail_account_email(str(account.get("client_id", "")), unprotect_secret(str(account.get("client_secret", ""))), unprotect_secret(str(account.get("refresh_token", ""))))
            except GmailError:
                pass
        accounts = self._gmail_accounts()
        self._save_gmail_accounts([account if item.get("id") == account.get("id") else item for item in accounts], str(account["id"]))
        return {"contacts": contacts, "gmail_cached": False, "gmail_cached_at": cached_at, **status}

    def analyze_gmail(self, contact_id: str, tone: str, brief: str) -> dict:
        contact = self._gmail_contact(contact_id)
        contact_messages = contact.get("messages", [])
        task = brief.strip() or DEFAULT_GMAIL_REPLY_BRIEF
        self.db.set_setting("gmail_reply_brief", task)
        history = [
            {
                "message_date": message.get("date", ""),
                "sender": "Вы" if message.get("out") else message.get("sender", contact["name"]),
                "out": bool(message.get("out")),
                "text": str(message.get("text", ""))[:6000],
            }
            for message in contact_messages[-40:]
        ]
        profile = ClientProfile(
            id=None,
            chat_name=f"{contact['name']} <{contact['email']}>",
            communication_style="Деловая переписка по электронной почте.",
            tone_recommendations="Ответ должен быть самодостаточным, без служебных заголовков и цитирования всей цепочки.",
        )
        with self._analysis_lock:
            result = self.ai.analyze_screenshot(
                b"",
                profile,
                history,
                tone or "деловой",
                user_comment=(
                    f"Подготовь ответ на последнее входящее письмо. ТЗ пользователя: {task}. "
                    "Верни только текст письма в вариантах ответа; не добавляй тему, To/From и технические заголовки."
                ),
                user_style_profile=self.user_style(),
                business_calendar_context=business_calendar_context(
                    weekend_policy=self.weekend_policy(),
                    after_hours_policy=self.after_hours_policy(),
                    workday_start_hour=self.workday_start_hour(),
                    workday_end_hour=self.workday_end_hour(),
                ),
            )
        payload = asdict(result)
        payload["context"] = {
            "message_count": len(history),
            "contact": profile.chat_name,
            "relationship": "деловая почта",
            "tone": tone or "деловой",
            "goal": task,
            "topic": contact_messages[-1].get("subject", "Письмо") if contact_messages else "Письмо",
            "role": ASSISTANT_ROLES[self.assistant_role()]["label"],
            "topic_memory": False,
        }
        return payload

    def send_gmail(self, contact_id: str, text: str) -> dict:
        account = self._active_gmail_account()
        if not account or not account.get("can_send"):
            raise WebApiError("Переподключите Gmail и разрешите отправку писем.")
        contact = self._gmail_contact(contact_id)
        clean_text = text.strip()
        if not clean_text:
            raise WebApiError("Нельзя отправить пустое письмо.")
        latest = contact.get("messages", [])[-1] if contact.get("messages") else {}
        thread_id = str(latest.get("thread_id", ""))
        if not thread_id:
            raise WebApiError("У выбранного контакта нет Gmail-цепочки для ответа.")
        try:
            sent = send_gmail_reply(
                str(account.get("client_id", "")),
                unprotect_secret(str(account.get("client_secret", ""))),
                unprotect_secret(str(account.get("refresh_token", ""))),
                str(contact["email"]),
                str(latest.get("subject", "Без темы")),
                clean_text,
                thread_id,
                str(latest.get("rfc_message_id", "")),
            )
        except GmailError as exc:
            raise WebApiError(str(exc)) from exc
        account["contacts_cache"] = ""
        accounts = self._gmail_accounts()
        self._save_gmail_accounts([account if item.get("id") == account.get("id") else item for item in accounts], str(account["id"]))
        return {"sent": True, "message_id": str(sent.get("id", "")), "thread_id": thread_id}

    def _gmail_contact(self, contact_id: str) -> dict:
        try:
            account = self._active_gmail_account()
            contacts = json.loads(str(account.get("contacts_cache", "")) if account else "[]")
        except json.JSONDecodeError as exc:
            raise WebApiError("Кеш Gmail повреждён. Обновите почту.") from exc
        contact = next((item for item in contacts if str(item.get("id", "")) == contact_id), None)
        if not contact:
            raise WebApiError("Контакт не найден в сохранённой почте. Нажмите «Обновить».")
        return contact

    def gmail_contact_groups(self) -> dict:
        account = self._active_gmail_account()
        if not account:
            raise WebApiError("Сначала подключите Gmail.")
        try:
            groups = list_google_contact_groups(
                str(account.get("client_id", "")),
                unprotect_secret(str(account.get("client_secret", ""))),
                unprotect_secret(str(account.get("refresh_token", ""))),
            )
        except GmailError as exc:
            raise WebApiError(f"Не удалось получить Google Contacts. Включите People API и переподключите Gmail: {exc}") from exc
        return {"groups": groups, "selected_ids": account.get("advisor_contact_group_ids", [])}

    def gmail_contact_label_status(self) -> dict:
        account = self._active_gmail_account()
        account_id = str(account.get("id", "")) if account else ""
        with self._gmail_label_lock:
            return dict(self._gmail_label_status.get(account_id, {"state": "idle", "message": "Контакты ещё не обрабатывались.", "current": 0, "total": 0}))

    def start_gmail_contact_labeling(self, group_ids: list[str]) -> dict:
        account = self._active_gmail_account()
        if not account:
            raise WebApiError("Сначала подключите Gmail.")
        clean_ids = list(dict.fromkeys(str(group_id) for group_id in group_ids if str(group_id).startswith("contactGroups/")))
        if not clean_ids:
            raise WebApiError("Выберите хотя бы одну группу Google Contacts.")
        account_id = str(account["id"])
        with self._gmail_label_lock:
            current = self._gmail_label_status.get(account_id, {})
            if current.get("state") == "running":
                return dict(current)
            self._gmail_label_status[account_id] = {"state": "running", "message": "Получаю контакты из Google…", "current": 0, "total": 0}
        snapshot = dict(account)
        threading.Thread(target=self._label_gmail_contact_messages, args=(account_id, snapshot, clean_ids), daemon=True, name=f"gmail-contact-label-{account_id}").start()
        return self.gmail_contact_label_status()

    def _label_gmail_contact_messages(self, account_id: str, account: dict[str, Any], group_ids: list[str]) -> None:
        def update(message: str, current: int, total: int) -> None:
            with self._gmail_label_lock:
                self._gmail_label_status[account_id] = {"state": "running", "message": message, "current": current, "total": total}

        try:
            result = label_messages_from_contact_groups(
                str(account.get("client_id", "")),
                unprotect_secret(str(account.get("client_secret", ""))),
                unprotect_secret(str(account.get("refresh_token", ""))),
                group_ids,
                str(account.get("label", "Advisor")),
                update,
            )
            accounts = self._gmail_accounts()
            for item in accounts:
                if item.get("id") == account_id:
                    item["advisor_contact_group_ids"] = group_ids
                    item["contacts_cache"] = ""
            self._save_gmail_accounts(accounts, account_id)
            with self._gmail_label_lock:
                self._gmail_label_status[account_id] = {"state": "done", "message": f"Готово: контактов {result['contacts']}, помечено писем {result['messages']}.", "current": result["contacts"], "total": result["contacts"]}
        except Exception as exc:
            log_event(f"gmail contact labeling failed: {exc}")
            with self._gmail_label_lock:
                self._gmail_label_status[account_id] = {"state": "error", "message": f"Ошибка: {exc}", "current": 0, "total": 0}

    def _send_profile_notification(self, profile_id: int, text: str) -> None:
        if self.db.get_setting("notifications_enabled") != "1":
            return
        chat = self.db.get_setting("notification_chat").strip()
        if not chat:
            return
        profile = self._require_profile(profile_id)
        account = self.profile_account(profile)
        with self._telegram_locks[account]:
            send_telegram_message(
                self.telegram_config(profile),
                chat,
                _truncate_telegram_text(text),
            )

    def _notify_important_incoming(self, profile_id: int, messages: list[dict]) -> None:
        try:
            last_seen = int(self.db.get_setting(f"notification_last_seen:{profile_id}", "0"))
        except ValueError:
            last_seen = 0
        incoming = [
            message
            for message in messages
            if not message.get("out") and int(message.get("telegram_message_id") or 0) > last_seen
        ]
        if not incoming:
            return
        latest_id = max(int(message.get("telegram_message_id") or 0) for message in incoming)
        important = [message for message in incoming if _looks_like_design_handoff(str(message.get("text", "")))]
        if important:
            profile = self._require_profile(profile_id)
            details = "\n".join(str(message.get("text", "")).strip() for message in important if message.get("text"))
            try:
                self._send_profile_notification(
                    profile_id,
                    "Важное входящее: макет / Figma\n"
                    f"Аккаунт: {_account_label(self.profile_account(profile))}\n"
                    f"Контакт: {profile.chat_name}\n\n"
                    f"{details}",
                )
            except Exception as exc:
                log_event(f"important notification failed for profile {profile_id}: {exc}")
                return
        self.db.set_setting(f"notification_last_seen:{profile_id}", str(latest_id))

    def _notify_autopilot_reply(
        self,
        profile_id: int,
        incoming_messages: list[dict],
        reply: str,
        analysis: dict,
    ) -> None:
        profile = self._require_profile(profile_id)
        incoming_text = "\n".join(
            str(message.get("text", "")).strip()
            for message in incoming_messages
            if not message.get("out") and message.get("text")
        )
        message = (
            "Автопилот ответил клиенту\n"
            f"Аккаунт: {_account_label(self.profile_account(profile))}\n"
            f"Контакт: {profile.chat_name}\n\n"
            f"Входящее:\n{incoming_text or '(без текста)'}\n\n"
            f"Ответ бота:\n{reply}\n\n"
            f"Стратегия:\n{str(analysis.get('recommended_strategy', '') or 'не указана')}"
        )
        try:
            self._send_profile_notification(profile_id, message)
        except Exception as exc:
            log_event(f"autopilot notification failed for profile {profile_id}: {exc}")

    def autopilot_config(self, profile_id: int) -> dict:
        enabled = self.db.get_setting(f"autopilot_enabled:{profile_id}") == "1"
        try:
            delay = int(self.db.get_setting(f"autopilot_delay_seconds:{profile_id}", "45"))
        except ValueError:
            delay = 45
        with self._autopilot_lock:
            status = self._autopilot_status.get(profile_id, "watching" if enabled else "off")
        return {
            "autopilot_enabled": enabled,
            "autopilot_delay_seconds": max(1, min(1800, delay)),
            "autopilot_status": status,
        }

    def set_autopilot(self, profile_id: int, enabled: bool, delay_seconds: int) -> dict:
        self._require_profile(profile_id)
        was_enabled = self.db.get_setting(f"autopilot_enabled:{profile_id}") == "1"
        delay = max(1, min(1800, delay_seconds))
        if enabled and not was_enabled:
            self.refresh_profile(profile_id)
        self.db.set_setting(f"autopilot_delay_seconds:{profile_id}", str(delay))
        self.db.set_setting(f"autopilot_enabled:{profile_id}", "1" if enabled else "0")
        if enabled and not was_enabled:
            with self._autopilot_lock:
                self._autopilot_pending.pop(profile_id, None)
                self._autopilot_status[profile_id] = "watching"
            current_incoming = _max_incoming_message_id(self.db.recent_messages(profile_id, limit=500))
            self.db.set_setting(f"autopilot_last_handled:{profile_id}", str(current_incoming))
            self.db.set_setting(f"notification_last_seen:{profile_id}", str(current_incoming))
            log_event(f"autopilot enabled for profile {profile_id}, delay={delay}s")
        elif not enabled:
            with self._autopilot_lock:
                self._autopilot_pending.pop(profile_id, None)
                self._autopilot_status[profile_id] = "off"
            log_event(f"autopilot disabled for profile {profile_id}")
        else:
            log_event(f"autopilot delay changed for profile {profile_id}, delay={delay}s")
        return self.autopilot_config(profile_id)

    def _autopilot_loop(self) -> None:
        while not self._autopilot_stop.wait(1):
            for profile in self.db.list_profiles():
                if self._autopilot_stop.is_set() or profile.id is None:
                    return
                profile_id = profile.id
                if self.db.get_setting(f"autopilot_enabled:{profile_id}") != "1":
                    continue
                try:
                    self.refresh_profile(profile_id)
                    self._autopilot_tick(profile_id)
                except Exception as exc:
                    with self._autopilot_lock:
                        self._autopilot_status[profile_id] = "error"
                    log_event(f"autopilot failed for profile {profile_id}: {exc}")

    def _autopilot_tick(self, profile_id: int) -> None:
        messages = self.db.recent_messages(profile_id, limit=80)
        if not messages:
            return
        latest = messages[-1]
        latest_incoming_id = _max_incoming_message_id(messages)
        try:
            handled_id = int(self.db.get_setting(f"autopilot_last_handled:{profile_id}", "0"))
        except ValueError:
            handled_id = 0
        self._notify_important_incoming(profile_id, messages)

        if latest.get("out"):
            if latest_incoming_id > handled_id:
                self.db.set_setting(f"autopilot_last_handled:{profile_id}", str(latest_incoming_id))
            with self._autopilot_lock:
                self._autopilot_pending.pop(profile_id, None)
                self._autopilot_status[profile_id] = "watching"
            return
        if latest_incoming_id <= handled_id:
            with self._autopilot_lock:
                self._autopilot_status[profile_id] = "watching"
            return

        delay = self.autopilot_config(profile_id)["autopilot_delay_seconds"]
        now = time.monotonic()
        with self._autopilot_lock:
            pending = self._autopilot_pending.get(profile_id)
            if not pending or pending[0] != latest_incoming_id:
                self._autopilot_pending[profile_id] = (latest_incoming_id, now)
                self._autopilot_status[profile_id] = "waiting"
                return
            if now - pending[1] < delay:
                self._autopilot_status[profile_id] = "waiting"
                return
            self._autopilot_status[profile_id] = "thinking"

        try:
            result = self.analyze(
                profile_id,
                "мой стиль",
                "Автопилот: ответь самостоятельно на последние входящие сообщения одним сообщением.",
                message_limit=60,
                topic_override="Авто",
            )
            reply = str(result.get("best_reply", "") or "").strip()
            if not reply:
                raise WebApiError("AI не вернул текст ответа.")
            current = self.db.recent_messages(profile_id, limit=80)
            current_incoming_id = _max_incoming_message_id(current)
            if not current or current[-1].get("out") or current_incoming_id != latest_incoming_id:
                with self._autopilot_lock:
                    self._autopilot_pending.pop(profile_id, None)
                    self._autopilot_status[profile_id] = "waiting"
                return
            with self._autopilot_lock:
                self._autopilot_status[profile_id] = "sending"
            self.send_message(profile_id, reply)
            relevant_incoming = [
                message
                for message in current
                if not message.get("out")
                and handled_id < int(message.get("telegram_message_id") or 0) <= latest_incoming_id
            ]
            self._notify_autopilot_reply(profile_id, relevant_incoming, reply, result)
            self.db.set_setting(f"autopilot_last_handled:{profile_id}", str(latest_incoming_id))
            with self._autopilot_lock:
                self._autopilot_pending.pop(profile_id, None)
                self._autopilot_status[profile_id] = "watching"
            log_event(f"autopilot sent reply for profile {profile_id}, message={latest_incoming_id}")
        except Exception:
            with self._autopilot_lock:
                self._autopilot_pending[profile_id] = (latest_incoming_id, time.monotonic())
                self._autopilot_status[profile_id] = "error"
            raise

    def start_live_sessions(self) -> None:
        for account in ("work", "personal"):
            self._restart_live_account(account)

    def start_live_profile(self, profile_id: int) -> None:
        profile = self.db.get_profile(profile_id)
        if not profile:
            return
        self._restart_live_account(self.profile_account(profile))

    def _restart_live_account(self, account: str) -> None:
        selected = _normalize_account(account)
        telegram_config = self.telegram_config_for_account(selected)
        peers = {
            profile.id: self.db.get_setting(f"telegram_peer:{profile.id}").strip()
            for profile in self.db.list_profiles()
            if profile.id is not None and self.profile_account(profile) == selected
        }
        peers = {profile_id: peer for profile_id, peer in peers.items() if peer}

        with self._live_lock:
            existing = self._live_sessions.pop(selected, None)
        if existing:
            existing.stop()
            existing.thread.join(timeout=3)
        if not peers or not telegram_config.telegram_api_id or not telegram_config.telegram_api_hash:
            return

        session = TelegramAccountLiveSession(telegram_config, selected, peers, self.db)
        with self._live_lock:
            self._live_sessions[selected] = session
        session.start()

    def profile_account(self, profile: ClientProfile) -> str:
        if profile.id is None:
            return "work"
        return _normalize_account(self.db.get_setting(f"telegram_account:{profile.id}", "work"))

    def telegram_config(self, profile: ClientProfile) -> AppConfig:
        return self.telegram_config_for_account(self.profile_account(profile))

    def telegram_config_for_account(self, account: str) -> AppConfig:
        selected = _normalize_account(account)
        if selected == "personal":
            base_id = self.config.telegram_personal_api_id
            base_hash = self.config.telegram_personal_api_hash
            base_phone = self.config.telegram_personal_phone
            session_path = self.config.telegram_personal_session_path
        else:
            base_id = self.config.telegram_api_id
            base_hash = self.config.telegram_api_hash
            base_phone = self.config.telegram_phone
            session_path = self.config.telegram_session_path
        stored_id = self.db.get_setting(f"telegram_api_id:{selected}").strip()
        if selected == "personal" and not stored_id:
            stored_id = self.db.get_setting("telegram_api_id:work").strip()
        try:
            api_id = int(stored_id) if stored_id else base_id
        except ValueError:
            api_id = base_id
        stored_hash = self.db.get_setting(f"telegram_api_hash:{selected}").strip()
        if selected == "personal" and not stored_hash:
            stored_hash = self.db.get_setting("telegram_api_hash:work").strip()
        return replace(
            self.config,
            telegram_api_id=api_id,
            telegram_api_hash=stored_hash or base_hash,
            telegram_phone=self.db.get_setting(f"telegram_phone:{selected}", base_phone or "").strip() or base_phone,
            telegram_session_path=session_path,
        )

    def _profile_payload(self, profile: ClientProfile) -> dict:
        if profile.id is None:
            raise WebApiError("Профиль не сохранен.")
        recent = self.db.recent_messages(profile.id, limit=30)
        last_message = recent[-1] if recent else None
        max_message_id = self.db.max_telegram_message_id(profile.id)
        try:
            ai_read_message_id = int(self.db.get_setting(f"ai_read_message_id:{profile.id}", "0"))
        except ValueError:
            ai_read_message_id = 0
        latest_incoming_id = _max_incoming_message_id(recent)
        seen_key = f"ui_last_seen_message_id:{profile.id}"
        seen_raw = self.db.get_setting(seen_key)
        if not seen_raw:
            ui_last_seen_message_id = latest_incoming_id
            self.db.set_setting(seen_key, str(ui_last_seen_message_id))
        else:
            try:
                ui_last_seen_message_id = int(seen_raw)
            except ValueError:
                ui_last_seen_message_id = latest_incoming_id
                self.db.set_setting(seen_key, str(ui_last_seen_message_id))
        return {
            "id": profile.id,
            "chat_name": profile.chat_name,
            "account": self.profile_account(profile),
            "preview": _message_preview(last_message),
            "last_message_date": last_message.get("message_date", "") if last_message else "",
            "avatar_url": f"/api/profiles/{profile.id}/avatar",
            "ai_unread": bool(max_message_id and ai_read_message_id < max_message_id),
            "unread": latest_incoming_id > ui_last_seen_message_id,
            "ui_last_seen_message_id": ui_last_seen_message_id,
            "contact_kind": self.contact_kind(profile.id),
            "autopilot_enabled": self.db.get_setting(f"autopilot_enabled:{profile.id}") == "1",
        }

    def _require_profile(self, profile_id: int) -> ClientProfile:
        profile = self.db.get_profile(profile_id)
        if not profile:
            raise WebApiError("Профиль не найден.")
        return profile

    def _profile_by_name(self, chat_name: str, account: str) -> ClientProfile | None:
        for profile in self.db.list_profiles():
            if profile.chat_name == chat_name and self.profile_account(profile) == account:
                return profile
        return None

    def _profile_by_peer(self, peer: str, account: str) -> ClientProfile | None:
        normalized_peer = _normalize_peer(peer)
        for profile in self.db.list_profiles():
            if profile.id is None or self.profile_account(profile) != account:
                continue
            if _normalize_peer(self.db.get_setting(f"telegram_peer:{profile.id}")) == normalized_peer:
                return profile
        return None


class AdvisorRequestHandler(BaseHTTPRequestHandler):
    service: AdvisorWebService

    server_version = "PainAdvisorWebApi/1.0"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)
            if not parts:
                self._web_app("index.html")
            elif parts and parts[0] == "assets":
                self._web_app("/".join(parts))
            elif parts == ["api", "health"]:
                self._json({"ok": True, "backend": self.service.config.analysis_backend})
            elif parts == ["projects"]:
                self._projects_page()
            elif parts == ["api", "projects"]:
                self._json(self.service.projects())
            elif parts == ["api", "onboarding"]:
                self._json(self.service.onboarding_status())
            elif len(parts) == 4 and parts[:2] == ["api", "telegram"] and parts[2] == "auth":
                self._json(self.service.telegram_auth_status(parts[3]))
            elif parts == ["api", "profiles"]:
                self._json({"profiles": self.service.list_profiles(query.get("account", ["work"])[0])})
            elif parts == ["api", "settings"]:
                self._json(
                    {
                        "user_style": self.service.user_style(),
                        "analysis_model": self.service.analysis_model(),
                        "analysis_models": [
                            {"id": model, "label": label}
                            for model, label in ANALYSIS_MODELS.items()
                        ],
                        "assistant_role": self.service.assistant_role(),
                        "assistant_roles": [
                            {"id": role, "label": payload["label"]}
                            for role, payload in ASSISTANT_ROLES.items()
                        ],
                        "weekend_policy": self.service.weekend_policy(),
                        "after_hours_policy": self.service.after_hours_policy(),
                        "workday_start_hour": self.service.workday_start_hour(),
                        "workday_end_hour": self.service.workday_end_hour(),
                        **self.service.notification_settings(),
                        "calendar_context": business_calendar_context(
                            weekend_policy=self.service.weekend_policy(),
                            after_hours_policy=self.service.after_hours_policy(),
                            workday_start_hour=self.service.workday_start_hour(),
                            workday_end_hour=self.service.workday_end_hour(),
                        ),
                        **self.service.gmail_status(),
                        **self.service.max_status(),
                    }
                )
            elif parts == ["api", "gmail", "messages"]:
                self._json(self.service.gmail_messages())
            elif parts == ["api", "gmail", "contact-groups"]:
                self._json(self.service.gmail_contact_groups())
            elif parts == ["api", "gmail", "contact-label-status"]:
                self._json(self.service.gmail_contact_label_status())
            elif parts == ["api", "max", "messages"]:
                self._json(self.service.max_messages())
            elif len(parts) == 3 and parts[:2] == ["api", "profiles"]:
                self._json(self.service.profile_details(_profile_id(parts[2])))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "messages":
                before = query.get("before", [""])[0]
                if before:
                    self._json(self.service.older_messages(_profile_id(parts[2]), _message_id(before), _limit(query.get("limit", ["100"])[0])))
                else:
                    self._json({"messages": self.service.messages(_profile_id(parts[2]), _limit(query.get("limit", ["100"])[0]))})
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "project-status":
                self._json(self.service.project_scan_status(_profile_id(parts[2])))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "projects":
                self._json(self.service.profile_projects(_profile_id(parts[2])))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "avatar":
                self._avatar(_profile_id(parts[2]))
            elif (
                len(parts) == 6
                and parts[:2] == ["api", "profiles"]
                and parts[3] == "messages"
                and parts[5] == "media"
            ):
                self._media(_profile_id(parts[2]), _message_id(parts[4]))
            else:
                self._error(HTTPStatus.NOT_FOUND, "Маршрут не найден.")
        except WebApiError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            log_event(f"web api GET failed: {traceback.format_exc()[-1200:]}")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Внутренняя ошибка локального API.")

    def do_POST(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            body = self._body()
            if parts == ["api", "telegram", "import"]:
                self._json(self.service.import_telegram_chat(str(body.get("peer", "")), str(body.get("account", "work"))))
            elif parts == ["api", "projects"]:
                self._json(self.service.save_project(body))
            elif parts == ["api", "projects", "scan"]:
                self._json(self.service.scan_projects(max(1, min(100, int(body.get("limit", 20))))))
            elif len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "completed-by-me":
                self._json(self.service.mark_project_completed_by_me(_project_id(parts[2])))
            elif parts == ["api", "telegram", "import-recent"]:
                self._json(
                    self.service.import_recent_contacts(
                        str(body.get("account", "work")),
                        max(1, min(20, int(body.get("limit", 10)))),
                    )
                )
            elif parts == ["api", "telegram", "credentials"]:
                self._json(
                    self.service.save_telegram_credentials(
                        str(body.get("account", "work")),
                        body.get("api_id", ""),
                        str(body.get("api_hash", "")),
                        str(body.get("phone", "")),
                    )
                )
            elif len(parts) == 5 and parts[:3] == ["api", "telegram", "auth"] and parts[4] == "start":
                self._json(self.service.start_telegram_auth(parts[3]))
            elif len(parts) == 5 and parts[:3] == ["api", "telegram", "auth"] and parts[4] == "password":
                self._json(self.service.submit_telegram_password(parts[3], str(body.get("password", ""))))
            elif parts == ["api", "codex", "login"]:
                self._json(self.service.start_codex_login())
            elif parts == ["api", "settings"]:
                self.service.set_user_style(str(body.get("user_style", "")))
                self.service.set_weekend_policy(str(body.get("weekend_policy", "")))
                self.service.set_after_hours_policy(str(body.get("after_hours_policy", "")))
                self.service.set_workday_start_hour(body.get("workday_start_hour", 9))
                self.service.set_workday_end_hour(body.get("workday_end_hour", 19))
                self.service.set_notification_settings(
                    bool(body.get("notifications_enabled", False)),
                    str(body.get("notification_chat", "")),
                )
                self._json({"ok": True})
            elif parts == ["api", "settings", "model"]:
                self._json({"analysis_model": self.service.set_analysis_model(str(body.get("model", "")))})
            elif parts == ["api", "settings", "role"]:
                self._json({"assistant_role": self.service.set_assistant_role(str(body.get("role", "")))})
            elif parts == ["api", "settings", "notifications", "test"]:
                self._json(self.service.test_notifications())
            elif parts == ["api", "gmail", "connect"]:
                self._json(
                    self.service.connect_gmail(
                        str(body.get("client_id", "")),
                        str(body.get("client_secret", "")),
                        str(body.get("label", "Advisor")),
                    )
                )
            elif parts == ["api", "max", "connect"]:
                self._json(self.service.connect_max(str(body.get("token", ""))))
            elif parts == ["api", "max", "refresh"]:
                self._json(self.service.max_messages(force_refresh=True))
            elif parts == ["api", "max", "send"]:
                self._json(self.service.send_max(str(body.get("contact_id", "")), str(body.get("text", ""))))
            elif parts == ["api", "max", "analyze"]:
                self._json(self.service.analyze_max(str(body.get("contact_id", "")), str(body.get("tone", "деловой")), str(body.get("brief", ""))))
            elif parts == ["api", "gmail", "reconnect"]:
                self._json(self.service.reconnect_gmail())
            elif parts == ["api", "gmail", "active"]:
                self._json(self.service.set_active_gmail_account(str(body.get("account_id", ""))))
            elif parts == ["api", "gmail", "settings"]:
                self._json(self.service.set_gmail_label(str(body.get("label", "Advisor"))))
            elif parts == ["api", "gmail", "refresh"]:
                self._json(self.service.gmail_messages(force_refresh=True))
            elif parts == ["api", "gmail", "contact-label"]:
                self._json(self.service.start_gmail_contact_labeling(body.get("group_ids", [])))
            elif parts == ["api", "gmail", "lead"]:
                self._json(self.service.save_gmail_lead(str(body.get("contact_id", "")), body))
            elif parts == ["api", "gmail", "analyze"]:
                self._json(
                    self.service.analyze_gmail(
                        str(body.get("contact_id", "")),
                        str(body.get("tone", "деловой")),
                        str(body.get("brief", "")),
                    )
                )
            elif parts == ["api", "gmail", "send"]:
                self._json(
                    self.service.send_gmail(
                        str(body.get("contact_id", "")),
                        str(body.get("text", "")),
                    )
                )
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "send":
                reply_to_raw = body.get("reply_to")
                try:
                    reply_to = int(reply_to_raw) if reply_to_raw is not None else None
                except (TypeError, ValueError) as exc:
                    raise WebApiError("Некорректное сообщение для ответа.") from exc
                self._json(
                    self.service.send_message(
                        _profile_id(parts[2]),
                        str(body.get("text", "")),
                        reply_to=reply_to,
                    )
                )
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "refresh":
                self._json(self.service.refresh_profile(_profile_id(parts[2])))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "profile-refresh":
                self._json(self.service.rebuild_profile(_profile_id(parts[2])))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "history":
                self._json(self.service.sync_history(_profile_id(parts[2])))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "html-history":
                self._json(self.service.import_html_history(_profile_id(parts[2]), body.get("files")))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "autopilot":
                self._json(
                    self.service.set_autopilot(
                        _profile_id(parts[2]),
                        bool(body.get("enabled", False)),
                        _autopilot_delay(body.get("delay_seconds", 45)),
                    )
                )
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "viewed":
                self._json(self.service.mark_profile_viewed(_profile_id(parts[2])))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "topic":
                self._json(
                    self.service.set_active_topic(
                        _profile_id(parts[2]),
                        str(body.get("topic", "")),
                        str(body.get("rule", "")) if "rule" in body else None,
                    )
                )
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "goal":
                self._json(self.service.set_conversation_goal(_profile_id(parts[2]), str(body.get("goal", ""))))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "project":
                self._json(self.service.create_project_from_profile(_profile_id(parts[2])))
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "contact-kind":
                self._json(
                    self.service.set_contact_kind(
                        _profile_id(parts[2]),
                        str(body.get("kind", "personal")),
                    )
                )
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "analyze":
                self._json(
                    self.service.analyze(
                        _profile_id(parts[2]),
                        str(body.get("tone", "мой стиль")),
                        str(body.get("user_comment", "")),
                    )
                )
            elif len(parts) == 4 and parts[:2] == ["api", "profiles"] and parts[3] == "rewrite":
                self._json(
                    self.service.rewrite_message(
                        _profile_id(parts[2]),
                        str(body.get("text", "")),
                        str(body.get("mode", "my_style")),
                    )
                )
            else:
                self._error(HTTPStatus.NOT_FOUND, "Маршрут не найден.")
        except WebApiError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            log_event(f"web api POST failed: {traceback.format_exc()[-1200:]}")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            parts = [part for part in urlparse(self.path).path.split("/") if part]
            if len(parts) == 3 and parts[:2] == ["api", "profiles"]:
                self.service.delete_profile(_profile_id(parts[2]))
                self._json({"ok": True})
            elif len(parts) == 3 and parts[:2] == ["api", "projects"]:
                self.service.delete_project(_project_id(parts[2]))
                self._json({"ok": True})
            else:
                self._error(HTTPStatus.NOT_FOUND, "Маршрут не найден.")
        except WebApiError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:
            log_event(f"web api DELETE failed: {traceback.format_exc()[-1200:]}")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Внутренняя ошибка локального API.")

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebApiError("Неверный JSON в запросе.") from exc
        if not isinstance(value, dict):
            raise WebApiError("JSON-запрос должен быть объектом.")
        return value

    def _projects_page(self) -> None:
        content = (Path(__file__).with_name("projects_page.html")).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _web_app(self, relative_path: str) -> None:
        root = Path(__file__).resolve().parents[2] / "web-ui" / "dist"
        target = (root / relative_path).resolve()
        if root not in target.parents and target != root or not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "Веб-интерфейс не собран.")
            return
        content = target.read_bytes()
        mime, _encoding = mimetypes.guess_type(target.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store" if target.name == "index.html" else "private, max-age=86400")
        self.end_headers()
        self.wfile.write(content)

    def _avatar(self, profile_id: int) -> None:
        path = self.service.avatar_path(profile_id)
        if not path:
            self._error(HTTPStatus.NOT_FOUND, "Аватарка не найдена.")
            return
        mime, _encoding = mimetypes.guess_type(path.name)
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _media(self, profile_id: int, telegram_message_id: int) -> None:
        path = self.service.message_media_path(profile_id, telegram_message_id)
        if not path:
            self._error(HTTPStatus.NOT_FOUND, "Изображение не найдено.")
            return
        mime, _encoding = mimetypes.guess_type(path.name)
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in {
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5174",
            "http://tauri.localhost",
            "tauri://localhost",
            "https://tauri.localhost",
        }:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def run_web_backend(config: AppConfig | None = None) -> int:
    config = config or load_config()
    host = os.getenv("PAIN_WEB_API_HOST", "127.0.0.1")
    port = int(os.getenv("PAIN_WEB_API_PORT", "18791"))
    service = AdvisorWebService(config)
    handler = type("BoundAdvisorRequestHandler", (AdvisorRequestHandler,), {"service": service})
    server = ThreadingHTTPServer((host, port), handler)
    log_event(f"web api started on http://{host}:{port}")
    print(f"Pain Advisor API: http://{host}:{port}/api/health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        server.server_close()
    return 0


def _normalize_account(account: str) -> str:
    return "personal" if account.strip().lower() == "personal" else "work"


def _normalize_peer(peer: str) -> str:
    return peer.strip().casefold().removeprefix("@")


def _profile_id(value: str) -> int:
    try:
        profile_id = int(value)
    except ValueError as exc:
        raise WebApiError("Некорректный идентификатор профиля.") from exc
    if profile_id <= 0:
        raise WebApiError("Некорректный идентификатор профиля.")
    return profile_id


def _project_id(value: str) -> int:
    try:
        project_id = int(value)
    except ValueError as exc:
        raise WebApiError("Некорректный проект.") from exc
    if project_id <= 0:
        raise WebApiError("Некорректный проект.")
    return project_id


def _project_date(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise WebApiError("Дата должна быть в формате ГГГГ-ММ-ДД.") from exc


def _project_candidate(profile_id: int, raw: dict) -> dict | None:
    title = str(raw.get("title", "")).strip()[:200]
    if not title:
        return None
    try:
        price = max(0, int(raw.get("price", 0) or 0))
        paid_amount = max(0, int(raw.get("paid_amount", 0) or 0))
        payable = max(0, int(raw.get("payable", 0) or 0))
        paid_out = max(0, int(raw.get("paid_out", 0) or 0))
        deadline = _project_date(raw.get("deadline"))
        paid_at = _project_date(raw.get("paid_at"))
        paid_out_at = _project_date(raw.get("paid_out_at"))
    except (TypeError, ValueError, WebApiError):
        return None
    status = str(raw.get("status", "planned"))
    if status not in {"planned", "progress", "waiting_payment", "done"}:
        return None
    notes = str(raw.get("notes", "")).strip()[:2000]
    if not notes:
        return None
    return {
        "profile_id": profile_id,
        "title": title,
        "price": price,
        "paid_amount": paid_amount,
        "payable": payable,
        "paid_out": paid_out,
        "status": status,
        "deadline": deadline,
        "paid_at": paid_at,
        "paid_out_at": paid_out_at,
        "notes": notes,
    }


class _TelegramHtmlHistoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: list[dict] = []
        self.current: dict | None = None
        self.depth = 0
        self.field = ""
        self.field_depth = 0
        self.parts: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        classes = set(str(data.get("class", "")).split())
        if self.current is None:
            if tag == "div" and "message" in classes and "default" in classes:
                message_id = _telegram_html_message_id(str(data.get("id", "")))
                if message_id:
                    self.current = {"telegram_message_id": message_id, "out": "out" in classes}
                    self.depth = 1
                    self.parts = {"sender": [], "date": [], "text": []}
            return
        if tag == "div":
            self.depth += 1
            if "from_name" in classes:
                self.field, self.field_depth = "sender", self.depth
            elif "date" in classes and "details" in classes:
                if data.get("title"):
                    self.parts["date"].append(str(data["title"]))
                else:
                    self.field, self.field_depth = "date", self.depth
            elif "text" in classes:
                self.field, self.field_depth = "text", self.depth
        elif tag == "br" and self.field == "text":
            self.parts["text"].append("\n")
        elif tag == "img" and self.field == "text" and data.get("alt"):
            self.parts["text"].append(str(data["alt"]))

    def handle_data(self, value: str) -> None:
        if self.current is not None and self.field:
            self.parts[self.field].append(value)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None or tag != "div":
            return
        if self.field and self.field_depth == self.depth:
            self.field = ""
        self.depth -= 1
        if self.depth:
            return
        text = _telegram_html_text("".join(self.parts["text"]))
        if text:
            self.current.update({
                "message_date": _telegram_html_date("".join(self.parts["date"])),
                "sender": _telegram_html_text("".join(self.parts["sender"])) or ("Вы" if self.current["out"] else "Собеседник"),
                "text": text,
            })
            self.messages.append(self.current)
        self.current = None
        self.field = ""


def _parse_telegram_html_history(content: str) -> list[dict]:
    parser = _TelegramHtmlHistoryParser()
    parser.feed(content)
    parser.close()
    return parser.messages


def _recent_project_messages(messages: list[dict]) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=31)
    recent = []
    for message in messages:
        try:
            date = datetime.fromisoformat(str(message.get("message_date", "")).replace("Z", "+00:00"))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if date.astimezone(timezone.utc) >= cutoff:
            recent.append(message)
    return recent


def _telegram_html_file_order(item: object) -> tuple[int, str]:
    name = str(item.get("name", "")) if isinstance(item, dict) else ""
    match = re.fullmatch(r"messages(\d*)\.html?", name.lower())
    return (int(match.group(1) or 1) if match else 10**9, name.lower())


def _telegram_html_message_id(value: str) -> int:
    match = re.fullmatch(r"message(\d+)", value)
    return int(match.group(1)) if match else 0


def _telegram_html_text(value: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", value.replace("\xa0", " "))).strip()


def _telegram_html_date(value: str) -> str:
    value = " ".join(value.split())
    for pattern in ("%d.%m.%Y %H:%M:%S UTC%z", "%d.%m.%Y %H:%M UTC%z", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            parsed = datetime.strptime(value, pattern)
            return parsed.isoformat()
        except ValueError:
            continue
    return ""


def _message_id(value: str) -> int:
    try:
        message_id = int(value)
    except ValueError as exc:
        raise WebApiError("Некорректный идентификатор сообщения.") from exc
    if message_id <= 0:
        raise WebApiError("Некорректный идентификатор сообщения.")
    return message_id


def _limit(value: str) -> int:
    try:
        return max(20, min(500, int(value)))
    except ValueError:
        return 100


def _autopilot_delay(value: object) -> int:
    try:
        return max(1, min(1800, int(value)))
    except (TypeError, ValueError):
        return 45


def _analysis_message_limit(db: Database) -> int:
    try:
        return max(20, min(500, int(db.get_setting("analysis_message_limit", "180"))))
    except ValueError:
        return 180


def _message_chunks(messages: list[dict], size: int) -> list[list[dict]]:
    return [messages[index : index + size] for index in range(0, len(messages), size)]


def _normalize_analysis_model(model: str, strict: bool = False) -> str:
    selected = model.strip().lower()
    if selected in ANALYSIS_MODELS:
        return selected
    if strict:
        raise WebApiError("Выбрана неподдерживаемая модель AI.")
    return "gpt-5.6-luna"


def _prepare_analysis_messages(messages: list[dict]) -> list[dict]:
    prepared: list[dict] = []
    for message in messages:
        item = dict(message)
        item.pop("context_row_id", None)
        item["out"] = bool(item.get("out"))
        prepared.append(item)
    return prepared


def _analysis_context_summary(result: AdvisorResult, limit: int = 2400) -> str:
    sections = [
        ("Ситуация", result.situation_summary),
        ("Намерение клиента", result.client_intent),
        ("Риск", result.risk),
        ("Стратегия", result.recommended_strategy),
        ("Не делать", "; ".join(result.do_not_do)),
    ]
    summary = "\n".join(f"{label}: {value.strip()}" for label, value in sections if value.strip())
    return summary[:limit].rstrip()


def _message_preview(message: dict | None) -> str:
    if not message:
        return "Нет сообщений"
    text = " ".join(str(message.get("text", "") or "").split())
    if not text and message.get("media_path"):
        text = "Фото"
    if len(text) > 56:
        text = text[:53].rstrip() + "..."
    sender = "Вы" if message.get("out") else str(message.get("sender", "") or "")
    return f"{sender}: {text}" if sender else text


def _max_incoming_message_id(messages: list[dict]) -> int:
    return max(
        (
            int(message.get("telegram_message_id") or 0)
            for message in messages
            if not message.get("out")
        ),
        default=0,
    )


def _account_label(account: str) -> str:
    return "рабочий Telegram" if account == "work" else "личный Telegram"


def _looks_like_design_handoff(text: str) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in ["figma.com", "figma", "фигм", "макет", "прототип"])


def _truncate_telegram_text(text: str, limit: int = 4000) -> str:
    cleaned = text.strip()
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _profile_needs_ai_refresh(profile: ClientProfile) -> bool:
    agreements = profile.agreements.lower()
    communication_style = profile.communication_style.lower()
    return (
        "ai backend недоступен" in agreements
        or "профиль составлен эвристически" in agreements
        or "автоматически подключён из последних telegram-диалогов" in communication_style
    )


if __name__ == "__main__":
    raise SystemExit(run_web_backend())
