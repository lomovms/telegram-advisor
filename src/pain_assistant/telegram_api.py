from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
import sqlite3
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
from typing import Any

from .config import AppConfig


class TelegramApiError(RuntimeError):
    pass


def import_messages_from_telegram(
    config: AppConfig,
    peer: str,
    progress=None,
    download_media: bool = False,
) -> tuple[str, list[dict]]:
    return _run_telegram(
        _import_messages_from_telegram(
            config,
            peer,
            progress=progress,
            download_media=download_media,
        )
    )


def import_all_messages_from_telegram(
    config: AppConfig,
    peer: str,
    progress=None,
    download_media: bool = False,
) -> tuple[str, list[dict]]:
    return _run_telegram(
        _import_messages_from_telegram(
            config,
            peer,
            progress=progress,
            download_media=download_media,
            history_limit=None,
        )
    )


def import_new_messages_from_telegram(
    config: AppConfig,
    peer: str,
    after_message_id: int,
    progress=None,
    download_media: bool = False,
) -> tuple[str, list[dict]]:
    return _run_telegram(
        _import_messages_from_telegram(
            config,
            peer,
            progress=progress,
            min_id=max(0, after_message_id),
            allow_empty=True,
            download_media=download_media,
        )
    )


def listen_new_messages_from_telegram(
    config: AppConfig,
    peer: str,
    on_message,
    on_read=None,
    progress=None,
    stop_event=None,
    download_media: bool = False,
) -> None:
    _run_telegram(
        _listen_new_messages_from_telegram(
            config,
            peer,
            on_message,
            on_read=on_read,
            progress=progress,
            stop_event=stop_event,
            download_media=download_media,
        )
    )


def listen_account_messages_from_telegram(
    config: AppConfig,
    peers: dict[int, str],
    on_message,
    on_read=None,
    progress=None,
    stop_event=None,
    download_media: bool = False,
) -> None:
    _run_telegram(
        _listen_account_messages_from_telegram(
            config,
            peers,
            on_message,
            on_read=on_read,
            progress=progress,
            stop_event=stop_event,
            download_media=download_media,
        )
    )


def send_telegram_message(
    config: AppConfig,
    peer: str,
    text: str,
    reply_to: int | None = None,
    progress=None,
) -> dict:
    return _run_telegram(_send_telegram_message(config, peer, text, reply_to=reply_to, progress=progress))


def import_recent_contacts_from_telegram(
    config: AppConfig,
    limit: int = 10,
    messages_per_chat: int = 60,
    progress=None,
) -> list[dict]:
    return _run_telegram(
        _import_recent_contacts_from_telegram(
            config,
            limit=max(1, min(20, limit)),
            messages_per_chat=max(10, min(100, messages_per_chat)),
            progress=progress,
        )
    )


def download_telegram_avatar(config: AppConfig, peer: str, output_dir: Path, progress=None) -> Path | None:
    return _run_telegram(_download_telegram_avatar(config, peer, output_dir, progress=progress))


def login_telegram_console(config: AppConfig, force_sms: bool = False) -> None:
    _run_telegram(_login_telegram_console(config, force_sms=force_sms))


def send_telegram_code_console(config: AppConfig, force_sms: bool = False) -> None:
    _run_telegram(_send_telegram_code_console(config, force_sms=force_sms))


def login_telegram_qr_console(config: AppConfig) -> None:
    _run_telegram(_login_telegram_qr_console(config))


def _run_telegram(coro):
    try:
        return asyncio.run(coro)
    except sqlite3.OperationalError as exc:
        if "database is locked" in str(exc).lower():
            raise TelegramApiError(
                "Telegram session занята другой операцией. Остановите Live Telegram или повторите действие через несколько секунд."
            ) from exc
        raise


async def _import_messages_from_telegram(
    config: AppConfig,
    peer: str,
    progress=None,
    min_id: int = 0,
    allow_empty: bool = False,
    download_media: bool = False,
    history_limit: int | None = 0,
) -> tuple[str, list[dict]]:
    _validate_config(config)
    peer = peer.strip()
    if not peer:
        raise TelegramApiError("Укажите чат: @username, id или точное название диалога.")

    if progress:
        progress("Telegram: подключаюсь к MTProto...")
    client = await _connect(config)
    try:
        if not await client.is_user_authorized():
            raise TelegramApiError(_login_required_message(config))

        if progress:
            progress(f"Telegram: ищу чат {peer}...")
        entity = await _resolve_entity(client, peer)
        title = _entity_title(entity, peer)
        messages: list[dict] = []
        seen = 0
        if progress:
            if min_id:
                progress(f"Telegram: читаю новые сообщения после #{min_id}...")
            else:
                progress("Telegram: читаю всю историю..." if history_limit is None else f"Telegram: читаю последние {config.telegram_history_limit} сообщений...")
        limit = config.telegram_history_limit if history_limit == 0 else history_limit
        async for message in client.iter_messages(entity, limit=limit, min_id=min_id):
            seen += 1
            text = _message_text(message)
            media_path, media_type = (
                await _download_message_image(client, config, peer, message)
                if download_media
                else ("", "")
            )
            if not text and not media_path:
                continue
            sender = await message.get_sender()
            messages.append(
                {
                    "date": message.date.isoformat() if message.date else "",
                    "telegram_message_id": getattr(message, "id", None),
                    "out": bool(getattr(message, "out", False)),
                    "sender": _entity_title(sender, "unknown"),
                    "text": text,
                    "media_path": media_path,
                    "media_type": media_type,
                    "reply_to_telegram_message_id": getattr(message, "reply_to_msg_id", None),
                }
            )
            if progress and len(messages) % 100 == 0:
                progress(f"Telegram: прочитано сообщений: {len(messages)} из {seen} просмотренных...")
        messages.reverse()
        if not messages and not allow_empty:
            raise TelegramApiError("В выбранном чате не найдено текстовых сообщений.")
        if progress:
            progress(f"Telegram: найдено сообщений: {len(messages)}")
        return title, messages
    finally:
        await client.disconnect()


async def _import_recent_contacts_from_telegram(
    config: AppConfig,
    limit: int,
    messages_per_chat: int,
    progress=None,
) -> list[dict]:
    _validate_config(config)
    if progress:
        progress("Telegram: получаю последние личные диалоги...")
    client = await _connect(config)
    try:
        if not await client.is_user_authorized():
            raise TelegramApiError(_login_required_message(config))
        me = await client.get_me()
        dialogs = await client.get_dialogs(limit=max(30, limit * 3))
        selected: list[tuple[object, str, str]] = []
        for dialog in dialogs:
            entity = dialog.entity
            if not hasattr(entity, "first_name") or getattr(entity, "bot", False):
                continue
            if me is not None and getattr(entity, "id", None) == getattr(me, "id", None):
                continue
            peer = str(getattr(entity, "username", "") or getattr(entity, "id", "")).strip()
            title = _entity_title(entity, peer)
            if peer and title:
                selected.append((entity, peer, title))
            if len(selected) >= limit:
                break

        result: list[dict] = []
        for index, (entity, peer, title) in enumerate(selected, start=1):
            if progress:
                progress(f"Telegram: подключаю диалог {index} из {len(selected)}: {title}")
            messages: list[dict] = []
            async for message in client.iter_messages(entity, limit=messages_per_chat):
                text = _message_text(message)
                if not text:
                    continue
                messages.append(
                    {
                        "date": message.date.isoformat() if message.date else "",
                        "telegram_message_id": getattr(message, "id", None),
                        "out": bool(getattr(message, "out", False)),
                        "sender": "Вы" if getattr(message, "out", False) else title,
                        "text": text,
                        "media_path": "",
                        "media_type": "",
                        "reply_to_telegram_message_id": getattr(message, "reply_to_msg_id", None),
                    }
                )
            messages.reverse()
            result.append({"peer": peer, "chat_name": title, "messages": messages})
        return result
    finally:
        await client.disconnect()


async def _listen_new_messages_from_telegram(
    config: AppConfig,
    peer: str,
    on_message,
    on_read=None,
    progress=None,
    stop_event=None,
    download_media: bool = False,
) -> None:
    _validate_config(config)
    peer = peer.strip()
    if not peer:
        raise TelegramApiError("Укажите чат: @username, id или точное название диалога.")

    if progress:
        progress("Live Telegram: подключаюсь к MTProto...")
    client = await _connect(config)
    try:
        if not await client.is_user_authorized():
            raise TelegramApiError(_login_required_message(config))

        if progress:
            progress(f"Live Telegram: ищу чат {peer}...")
        entity = await _resolve_entity(client, peer)
        title = _entity_title(entity, peer)

        try:
            from telethon import events
        except ImportError as exc:
            raise TelegramApiError("Пакет telethon не установлен. Выполните: pip install -r requirements.txt") from exc

        @client.on(events.NewMessage(chats=entity))
        async def _handler(event) -> None:
            message = event.message
            text = _message_text(message)
            media_path, media_type = (
                await _download_message_image(client, config, peer, message)
                if download_media
                else ("", "")
            )
            if not text and not media_path:
                return
            sender = await message.get_sender()
            on_message(
                {
                    "date": message.date.isoformat() if message.date else "",
                    "telegram_message_id": getattr(message, "id", None),
                    "out": bool(getattr(message, "out", False)),
                    "sender": _entity_title(sender, "unknown"),
                    "text": text,
                    "media_path": media_path,
                    "media_type": media_type,
                    "reply_to_telegram_message_id": getattr(message, "reply_to_msg_id", None),
                }
            )

        @client.on(events.MessageRead(chats=entity))
        async def _read_handler(event) -> None:
            if on_read is None or not getattr(event, "outbox", False):
                return
            max_message_id = getattr(event, "max_id", None)
            if max_message_id is not None:
                on_read(int(max_message_id))

        if progress:
            progress(f"Live Telegram: слушаю новые сообщения в чате {title}...")
        while stop_event is None or not stop_event.is_set():
            await asyncio.sleep(0.5)
    finally:
        await client.disconnect()


async def _listen_account_messages_from_telegram(
    config: AppConfig,
    peers: dict[int, str],
    on_message,
    on_read=None,
    progress=None,
    stop_event=None,
    download_media: bool = False,
) -> None:
    _validate_config(config)
    routes = {profile_id: peer.strip() for profile_id, peer in peers.items() if peer.strip()}
    if not routes:
        return

    if progress:
        progress("Live Telegram: подключаюсь к MTProto...")
    client = await _connect(config)
    try:
        if not await client.is_user_authorized():
            raise TelegramApiError(_login_required_message(config))

        try:
            from telethon import events, utils
        except ImportError as exc:
            raise TelegramApiError("Пакет telethon не установлен. Выполните: pip install -r requirements.txt") from exc

        entities = []
        profiles_by_peer_id: dict[int, list[int]] = {}
        peer_by_profile: dict[int, str] = {}
        for profile_id, peer in routes.items():
            try:
                entity = await _resolve_entity(client, peer)
            except Exception as exc:
                if progress:
                    progress(f"Live Telegram: не удалось подключить {peer}: {exc}")
                continue
            peer_id = int(utils.get_peer_id(entity))
            profiles_by_peer_id.setdefault(peer_id, []).append(profile_id)
            peer_by_profile[profile_id] = peer
            entities.append(entity)

        if not entities:
            raise TelegramApiError("Не удалось подключить ни один Telegram-чат для live-обновлений.")

        @client.on(events.NewMessage(chats=entities))
        async def _handler(event) -> None:
            profile_ids = profiles_by_peer_id.get(int(event.chat_id or 0), [])
            if not profile_ids:
                return
            message = event.message
            text = _message_text(message)
            sender = await message.get_sender()
            for profile_id in profile_ids:
                media_path, media_type = (
                    await _download_message_image(client, config, peer_by_profile[profile_id], message)
                    if download_media
                    else ("", "")
                )
                if not text and not media_path:
                    continue
                on_message(
                    profile_id,
                    {
                        "date": message.date.isoformat() if message.date else "",
                        "telegram_message_id": getattr(message, "id", None),
                        "out": bool(getattr(message, "out", False)),
                        "sender": _entity_title(sender, "unknown"),
                        "text": text,
                        "media_path": media_path,
                        "media_type": media_type,
                        "reply_to_telegram_message_id": getattr(message, "reply_to_msg_id", None),
                    },
                )

        @client.on(events.MessageRead(chats=entities))
        async def _read_handler(event) -> None:
            if on_read is None or not getattr(event, "outbox", False):
                return
            max_message_id = getattr(event, "max_id", None)
            if max_message_id is None:
                return
            for profile_id in profiles_by_peer_id.get(int(event.chat_id or 0), []):
                on_read(profile_id, int(max_message_id))

        if progress:
            progress(f"Live Telegram: слушаю {len(entities)} чатов...")
        while stop_event is None or not stop_event.is_set():
            await asyncio.sleep(0.25)
    finally:
        await client.disconnect()


async def _send_telegram_message(
    config: AppConfig,
    peer: str,
    text: str,
    reply_to: int | None = None,
    progress=None,
) -> dict:
    _validate_config(config)
    peer = peer.strip()
    text = text.strip()
    if not peer:
        raise TelegramApiError("Укажите чат: @username, id или точное название диалога.")
    if not text:
        raise TelegramApiError("Нельзя отправить пустое сообщение.")

    if progress:
        progress("Telegram: подключаюсь для отправки...")
    client = await _connect(config)
    try:
        if not await client.is_user_authorized():
            raise TelegramApiError(_login_required_message(config))
        if progress:
            progress(f"Telegram: ищу чат {peer}...")
        entity = await _resolve_entity(client, peer)
        if progress:
            progress("Telegram: отправляю сообщение...")
        sent = await client.send_message(entity, text, reply_to=reply_to)
        sent_text = _message_text(sent) or text
        sent_date = getattr(sent, "date", None)
        return {
            "date": sent_date.isoformat() if sent_date else datetime.now(timezone.utc).isoformat(),
            "telegram_message_id": getattr(sent, "id", None),
            "out": True,
            "delivery_status": "sent",
            "sender": "Вы",
            "text": sent_text,
            "reply_to_telegram_message_id": reply_to,
        }
    finally:
        await client.disconnect()


async def _download_telegram_avatar(config: AppConfig, peer: str, output_dir: Path, progress=None) -> Path | None:
    _validate_config(config)
    peer = peer.strip()
    if not peer:
        return None

    if progress:
        progress("Telegram: загружаю аватарку...")
    client = await _connect(config)
    try:
        if not await client.is_user_authorized():
            raise TelegramApiError(_login_required_message(config))
        entity = await _resolve_entity(client, peer)
        output_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(peer.encode("utf-8")).hexdigest()[:16]
        target = output_dir / f"{digest}.jpg"
        downloaded = await client.download_profile_photo(entity, file=str(target))
        return Path(downloaded) if downloaded else None
    finally:
        await client.disconnect()


async def _login_telegram_console(config: AppConfig, force_sms: bool = False) -> None:
    _validate_config(config, require_phone=True)
    client = await _connect(config)
    try:
        if await client.is_user_authorized():
            print("Telegram session уже авторизована.")
            return

        sent = await client.send_code_request(config.telegram_phone, force_sms=force_sms)
        _print_sent_code_info(sent)
        code = input("Введите код из Telegram: ").strip()
        try:
            await client.sign_in(config.telegram_phone, code)
        except _session_password_needed_error():
            password = getpass("Введите 2FA пароль Telegram: ")
            await client.sign_in(password=password)
        print(f"Telegram session сохранена: {config.telegram_session_path}")
    finally:
        await client.disconnect()


async def _send_telegram_code_console(config: AppConfig, force_sms: bool = False) -> None:
    _validate_config(config, require_phone=True)
    client = await _connect(config)
    try:
        if await client.is_user_authorized():
            print("Telegram session уже авторизована.")
            return
        sent = await client.send_code_request(config.telegram_phone, force_sms=force_sms)
        _print_sent_code_info(sent)
    finally:
        await client.disconnect()


async def _login_telegram_qr_console(config: AppConfig) -> None:
    _validate_config(config)
    client = await _connect(config)
    try:
        if await client.is_user_authorized():
            print("Telegram session уже авторизована.")
            return

        qr_login = await client.qr_login()
        _print_qr(qr_login.url)
        print("Откройте Telegram на телефоне: Settings -> Devices -> Link Desktop Device.")
        print("Отсканируйте QR. Ожидаю подтверждение...")
        try:
            await qr_login.wait(timeout=120)
        except _session_password_needed_error():
            password = getpass("Введите 2FA пароль Telegram: ")
            await client.sign_in(password=password)
        print(f"Telegram session сохранена: {config.telegram_session_path}")
    finally:
        await client.disconnect()


async def _connect(config: AppConfig):
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise TelegramApiError("Пакет telethon не установлен. Выполните: pip install -r requirements.txt") from exc

    config.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(config.telegram_session_path),
        config.telegram_api_id,
        config.telegram_api_hash,
    )
    await client.connect()
    return client


async def _resolve_entity(client: Any, peer: str) -> Any:
    invite_hash = _telegram_invite_hash(peer)
    if invite_hash:
        try:
            from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

            checked = await client(CheckChatInviteRequest(invite_hash))
            joined_chat = getattr(checked, "chat", None)
            if joined_chat is not None:
                return joined_chat
            imported = await client(ImportChatInviteRequest(invite_hash))
            chats = list(getattr(imported, "chats", []) or [])
            if chats:
                return chats[0]
        except Exception as exc:
            raise TelegramApiError(f"Не удалось присоединиться к группе уведомлений: {exc}") from exc

    try:
        return await client.get_entity(int(peer))
    except ValueError:
        pass
    except Exception:
        pass

    try:
        return await client.get_entity(peer)
    except Exception:
        pass

    needle = peer.casefold()
    dialogs = await client.get_dialogs(limit=200)
    for dialog in dialogs:
        if str(dialog.name or "").casefold() == needle:
            return dialog.entity

    matches = [dialog for dialog in dialogs if needle in str(dialog.name or "").casefold()]
    if len(matches) == 1:
        return matches[0].entity

    if matches:
        names = ", ".join(str(dialog.name) for dialog in matches[:10])
        raise TelegramApiError(f"Нашел несколько похожих чатов. Уточните название или username: {names}")

    raise TelegramApiError(f"Не нашел чат: {peer}")


def _telegram_invite_hash(peer: str) -> str:
    match = re.search(r"(?:t\.me|telegram\.me)/(?:joinchat/|\+)([A-Za-z0-9_-]+)", peer.strip())
    return match.group(1) if match else ""


def _validate_config(config: AppConfig, require_phone: bool = False) -> None:
    if not config.telegram_api_id or not config.telegram_api_hash:
        raise TelegramApiError("Заполните TELEGRAM_API_ID и TELEGRAM_API_HASH в .env.")
    if require_phone and not config.telegram_phone:
        raise TelegramApiError("Заполните TELEGRAM_PHONE в .env для первого входа.")


def _login_required_message(config: AppConfig) -> str:
    session_name = config.telegram_session_path.name.lower()
    account_arg = " --telegram-account personal" if "personal" in session_name else ""
    return (
        f"Telegram session еще не авторизована: {config.telegram_session_path}\n"
        "Это нужно сделать один раз, session сохранится и при следующих запусках код не потребуется.\n"
        "Запустите в PowerShell:\n"
        f"$env:PYTHONPATH='C:\\work\\pain\\src'; .\\.venv\\Scripts\\python.exe -m pain_assistant --telegram-login-qr{account_arg}"
    )


def _message_text(message: Any) -> str:
    text = getattr(message, "message", "") or getattr(message, "text", "") or ""
    return " ".join(str(text).replace("\xa0", " ").split())


async def _download_message_image(client: Any, config: AppConfig, peer: str, message: Any) -> tuple[str, str]:
    photo = getattr(message, "photo", None)
    file_info = getattr(message, "file", None)
    media_type = str(getattr(file_info, "mime_type", "") or "")
    if photo is not None and not media_type:
        media_type = "image/jpeg"
    if not media_type.startswith("image/"):
        return "", ""

    message_id = getattr(message, "id", None)
    if message_id is None:
        return "", ""

    extension = str(getattr(file_info, "ext", "") or "").lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
        extension = mimetypes.guess_extension(media_type) or ".jpg"
    output_dir = _telegram_media_dir(config, peer)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"message-{int(message_id)}{extension}"
    if not target.exists():
        downloaded = await client.download_media(message, file=str(target))
        if not downloaded:
            return "", ""
        target = Path(downloaded)

    data_root = config.db_path.resolve().parent
    try:
        stored_path = target.resolve().relative_to(data_root).as_posix()
    except ValueError:
        stored_path = str(target.resolve())
    return stored_path, media_type


def _telegram_media_dir(config: AppConfig, peer: str) -> Path:
    account = "personal" if "personal" in config.telegram_session_path.name.lower() else "work"
    normalized = peer.strip().removeprefix("@").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:40] or "chat"
    digest = hashlib.sha256(peer.strip().casefold().encode("utf-8")).hexdigest()[:8]
    return config.db_path.resolve().parent / "telegram-media" / account / f"{slug}-{digest}"


def _print_sent_code_info(sent: Any) -> None:
    code_type = getattr(sent, "type", None)
    next_type = getattr(sent, "next_type", None)
    timeout = getattr(sent, "timeout", None)
    print("Telegram принял запрос кода.")
    if code_type:
        print(f"Способ доставки: {type(code_type).__name__}")
    if next_type:
        print(f"Следующий доступный способ: {type(next_type).__name__}")
    if timeout:
        print(f"Повторный запрос будет доступен через {timeout} сек.")


def _print_qr(url: str) -> None:
    try:
        import qrcode
    except ImportError as exc:
        raise TelegramApiError("Пакет qrcode не установлен. Выполните: pip install -r requirements.txt") from exc

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def _entity_title(entity: Any, fallback: str) -> str:
    title = getattr(entity, "title", None)
    first_name = getattr(entity, "first_name", "") or ""
    last_name = getattr(entity, "last_name", "") or ""
    name = " ".join(part for part in [first_name, last_name] if part).strip()
    base = str(title or name or fallback).strip() or fallback
    username = getattr(entity, "username", None)
    if username:
        handle = f"@{username}"
        return base if handle.casefold() in base.casefold() else f"{base} ({handle})"
    entity_id = getattr(entity, "id", None)
    if entity_id is not None and str(entity_id) not in base:
        return f"{base} (id:{entity_id})"
    return base


def _session_password_needed_error():
    try:
        from telethon.errors import SessionPasswordNeededError
    except ImportError:
        return RuntimeError
    return SessionPasswordNeededError
