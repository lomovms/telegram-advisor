from __future__ import annotations

import secrets
import threading
import webbrowser
import base64
import ctypes
import re
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from email.message import EmailMessage
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from typing import Callable

import httpx


GMAIL_SCOPES = " ".join(
    (
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/contacts.readonly",
    )
)


class GmailError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def protect_secret(value: str) -> str:
    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
    ):
        raise GmailError("Windows не удалось защитить Gmail-токен.")
    try:
        encrypted = ctypes.string_at(target.pbData, target.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def unprotect_secret(value: str) -> str:
    if not value.startswith("dpapi:"):
        return value
    try:
        raw = base64.b64decode(value.removeprefix("dpapi:"))
    except ValueError as exc:
        raise GmailError("Сохранённый Gmail-токен повреждён.") from exc
    buffer = ctypes.create_string_buffer(raw)
    source = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)
    ):
        raise GmailError("Gmail был подключён под другой учётной записью Windows.")
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def authorize_gmail(client_id: str, client_secret: str, timeout: int = 180) -> dict:
    result: dict[str, str] = {}
    state = secrets.token_urlsafe(24)

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            result["code"] = query.get("code", [""])[0]
            result["state"] = query.get("state", [""])[0]
            result["error"] = query.get("error", [""])[0]
            body = (
                "<html><body style='font-family:system-ui;background:#0b1621;color:#e2e8f0;padding:40px'>"
                "<h2>Gmail подключён</h2><p>Можно закрыть эту вкладку и вернуться в Telegram Advisor.</p>"
                "</body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.timeout = timeout
    redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth2callback"
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GMAIL_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    threading.Thread(target=webbrowser.open, args=(auth_url,), daemon=True).start()
    server.handle_request()
    server.server_close()

    if result.get("error"):
        raise GmailError(f"Google отменил авторизацию: {result['error']}")
    if not result.get("code") or result.get("state") != state:
        raise GmailError("Авторизация Gmail не завершена или ответ Google некорректен.")

    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": result["code"],
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    data = _json_response(response, "Не удалось получить токен Gmail")
    if not data.get("refresh_token"):
        raise GmailError("Google не вернул refresh token. Отзовите доступ приложения и подключите Gmail заново.")
    return data


def fetch_labeled_contacts(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    label_name: str,
    limit: int = 100,
) -> list[dict]:
    access_token = _refresh_access_token(client_id, client_secret, refresh_token)
    headers = {"Authorization": f"Bearer {access_token}"}
    profile = _json_response(
        httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile", headers=headers, timeout=30),
        "Не удалось определить Gmail-аккаунт",
    )
    own_email = str(profile.get("emailAddress", "")).casefold()
    labels_response = httpx.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/labels",
        headers=headers,
        timeout=30,
    )
    labels = _json_response(labels_response, "Не удалось получить ярлыки Gmail").get("labels", [])
    label = next((item for item in labels if str(item.get("name", "")).casefold() == label_name.casefold()), None)
    if not label:
        raise GmailError(f"В Gmail не найден ярлык «{label_name}». Создайте его или измените название в настройках.")

    list_response = httpx.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/threads",
        headers=headers,
        params={"labelIds": label["id"], "maxResults": max(1, min(500, limit))},
        timeout=30,
    )
    threads = _json_response(list_response, "Не удалось получить письма Gmail").get("threads", [])
    contacts: dict[str, dict] = {}
    for thread in threads:
        detail_response = httpx.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread['id']}",
            headers=headers,
            params={"format": "full"},
            timeout=30,
        )
        detail = _json_response(detail_response, "Не удалось прочитать цепочку Gmail")
        raw_messages = detail.get("messages", [])
        thread_contacts = _thread_contacts(raw_messages, own_email)
        thread_id = str(detail.get("id", thread["id"]))
        for contact_email, contact_name in thread_contacts.items():
            contact = contacts.setdefault(
                contact_email,
                {
                    "id": contact_email,
                    "name": contact_name or contact_email,
                    "email": contact_email,
                    "date": "",
                    "preview": "",
                    "message_count": 0,
                    "thread_ids": [],
                    "messages": [],
                },
            )
            if contact_name and contact["name"] == contact["email"]:
                contact["name"] = contact_name
            _append_contact_thread(contact, detail, contact_email, own_email, len(thread_contacts))

    history_thread_ids: set[str] = set()
    contact_emails = list(contacts)
    # ponytail: latest 500 matching threads per 20 contacts; paginate only if a real mailbox exceeds this.
    for start in range(0, len(contact_emails), 20):
        batch = contact_emails[start : start + 20]
        query = "{" + " ".join(f"from:{email} to:{email}" for email in batch) + "}"
        history_response = httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/threads",
            headers=headers,
            params={"q": query, "maxResults": 500},
            timeout=30,
        )
        history_threads = _json_response(history_response, "Не удалось получить историю контактов").get("threads", [])
        history_thread_ids.update(str(thread["id"]) for thread in history_threads)

    known_thread_ids = {thread_id for contact in contacts.values() for thread_id in contact["thread_ids"]}

    def fetch_thread(thread_id: str) -> dict:
        response = httpx.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
            headers=headers,
            params={"format": "full"},
            timeout=30,
        )
        return _json_response(response, "Не удалось прочитать историю контакта")

    with ThreadPoolExecutor(max_workers=8) as executor:
        history_details = executor.map(fetch_thread, history_thread_ids - known_thread_ids)
        for detail in history_details:
            for contact_email, contact in contacts.items():
                _append_contact_thread(contact, detail, contact_email, own_email, len(contacts))

    result = []
    for contact in contacts.values():
        contact["messages"].sort(key=lambda item: item["timestamp"])
        if not contact["messages"]:
            continue
        latest = contact["messages"][-1]
        contact["date"] = latest["date"]
        contact["preview"] = latest["text"]
        contact["message_count"] = len(contact["messages"])
        result.append(contact)
    result.sort(key=lambda item: item["messages"][-1]["timestamp"], reverse=True)
    return result


def gmail_account_email(client_id: str, client_secret: str, refresh_token: str) -> str:
    access_token = _refresh_access_token(client_id, client_secret, refresh_token)
    profile = _json_response(
        httpx.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        ),
        "Не удалось определить Gmail-аккаунт",
    )
    return str(profile.get("emailAddress", "")).strip()


def list_google_contact_groups(client_id: str, client_secret: str, refresh_token: str) -> list[dict]:
    access_token = _refresh_access_token(client_id, client_secret, refresh_token)
    headers = {"Authorization": f"Bearer {access_token}"}
    groups: list[dict] = []
    page_token = ""
    while True:
        params = {"pageSize": 1000, "groupFields": "name,groupType,memberCount"}
        if page_token:
            params["pageToken"] = page_token
        payload = _json_response(
            httpx.get("https://people.googleapis.com/v1/contactGroups", headers=headers, params=params, timeout=30),
            "Не удалось прочитать группы Google Contacts",
        )
        groups.extend(
            {
                "id": str(group.get("resourceName", "")),
                "name": str(group.get("name") or group.get("formattedName") or "Без названия"),
                "member_count": int(group.get("memberCount", 0) or 0),
            }
            for group in payload.get("contactGroups", [])
            if group.get("resourceName") and group.get("groupType") == "USER_CONTACT_GROUP"
        )
        page_token = str(payload.get("nextPageToken", ""))
        if not page_token:
            break
    return sorted(groups, key=lambda item: item["name"].casefold())


def label_messages_from_contact_groups(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    group_ids: list[str],
    label_name: str,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    access_token = _refresh_access_token(client_id, client_secret, refresh_token)
    headers = {"Authorization": f"Bearer {access_token}"}
    label_response = _json_response(
        httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/labels", headers=headers, timeout=30),
        "Не удалось прочитать ярлыки Gmail",
    )
    label = next((item for item in label_response.get("labels", []) if str(item.get("name", "")).casefold() == label_name.casefold()), None)
    if not label:
        label = _json_response(
            httpx.post("https://gmail.googleapis.com/gmail/v1/users/me/labels", headers=headers, json={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}, timeout=30),
            "Не удалось создать ярлык Gmail",
        )
    label_id = str(label.get("id", ""))
    if not label_id:
        raise GmailError("Gmail не вернул идентификатор ярлыка.")

    people: list[str] = []
    for group_id in group_ids:
        group = _json_response(
            httpx.get(f"https://people.googleapis.com/v1/{group_id}", headers=headers, params={"maxMembers": 1000}, timeout=30),
            "Не удалось прочитать участников группы Google Contacts",
        )
        people.extend(str(item) for item in group.get("memberResourceNames", []) if item)
    people = list(dict.fromkeys(people))
    emails: set[str] = set()
    for start in range(0, len(people), 200):
        response = _json_response(
            httpx.get(
                "https://people.googleapis.com/v1/people:batchGet",
                headers=headers,
                params=[("personFields", "emailAddresses"), *[("resourceNames", item) for item in people[start : start + 200]]],
                timeout=30,
            ),
            "Не удалось прочитать email контактов",
        )
        for item in response.get("responses", []):
            for address in item.get("person", {}).get("emailAddresses", []):
                value = str(address.get("value", "")).strip().casefold()
                if "@" in value:
                    emails.add(value)

    ordered_emails = sorted(emails)
    labeled = 0
    for start in range(0, len(ordered_emails), 20):
        batch = ordered_emails[start : start + 20]
        if progress:
            progress("Ищу письма контактов…", start, len(ordered_emails))
        query = "{" + " ".join(f"from:{email}" for email in batch) + "}"
        page_token = ""
        while True:
            params = {"q": query, "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            payload = _json_response(
                httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages", headers=headers, params=params, timeout=30),
                "Не удалось найти письма контактов",
            )
            message_ids = [str(message.get("id", "")) for message in payload.get("messages", []) if message.get("id")]
            for message_start in range(0, len(message_ids), 1000):
                current = message_ids[message_start : message_start + 1000]
                _json_response(
                    httpx.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/batchModify", headers=headers, json={"ids": current, "addLabelIds": [label_id]}, timeout=30),
                    "Не удалось присвоить ярлык письмам",
                )
                labeled += len(current)
            page_token = str(payload.get("nextPageToken", ""))
            if not page_token:
                break
    if progress:
        progress("Готово", len(ordered_emails), len(ordered_emails))
    return {"contacts": len(ordered_emails), "messages": labeled, "label": label_name}


def send_gmail_reply(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    recipient: str,
    subject: str,
    text: str,
    thread_id: str,
    reply_to_message_id: str = "",
) -> dict:
    access_token = _refresh_access_token(client_id, client_secret, refresh_token)
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject if subject.casefold().startswith("re:") else f"Re: {subject}"
    if reply_to_message_id:
        message["In-Reply-To"] = reply_to_message_id
        message["References"] = reply_to_message_id
    message.set_content(text)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    response = httpx.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"raw": raw, "threadId": thread_id},
        timeout=30,
    )
    return _json_response(response, "Не удалось отправить письмо Gmail")


def _headers(message: dict) -> dict[str, str]:
    return {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in message.get("payload", {}).get("headers", [])
    }


def _append_contact_thread(contact: dict, detail: dict, contact_email: str, own_email: str, contact_count: int) -> None:
    thread_id = str(detail.get("id", ""))
    if not thread_id or thread_id in contact["thread_ids"]:
        return
    known_message_ids = {item["id"] for item in contact["messages"]}
    added = False
    for message in detail.get("messages", []):
        message_id = str(message.get("id", ""))
        metadata = _headers(message)
        if message_id in known_message_ids or _is_delivery_failure(metadata):
            continue
        if not _message_belongs_to_contact(metadata, contact_email, own_email, contact_count):
            continue
        sender_name, sender_email = parseaddr(metadata.get("from", ""))
        raw_text = _payload_text(message.get("payload", {}))
        if not raw_text:
            raw_text = str(message.get("snippet", ""))
        text, technical_text = _split_message_text(raw_text)
        if not text:
            continue
        contact["messages"].append(
            {
                "id": message_id,
                "thread_id": thread_id,
                "sender": _decode_header(sender_name) or sender_email,
                "subject": _decode_header(metadata.get("subject", "Без темы")),
                "rfc_message_id": metadata.get("message-id", ""),
                "date": metadata.get("date", ""),
                "timestamp": int(message.get("internalDate", 0) or 0),
                "text": text,
                "technical_text": technical_text[:6000],
                "out": sender_email.casefold() == own_email,
            }
        )
        added = True
    if added:
        contact["thread_ids"].append(thread_id)


def _thread_contacts(messages: list[dict], own_email: str) -> dict[str, str]:
    responders: dict[str, str] = {}
    for message in messages:
        metadata = _headers(message)
        if _is_delivery_failure(metadata):
            continue
        name, address = parseaddr(metadata.get("from", ""))
        normalized = address.casefold()
        if normalized and normalized != own_email:
            responders[normalized] = _decode_header(name) or address
    if responders:
        return responders

    recipients = {
        address.casefold(): _decode_header(name) or address
        for message in messages
        for name, address in getaddresses(
            [
                _headers(message).get("to", ""),
                _headers(message).get("cc", ""),
                _headers(message).get("bcc", ""),
            ]
        )
        if address and address.casefold() != own_email
    }
    return recipients if len(recipients) == 1 else {}


def _message_belongs_to_contact(metadata: dict[str, str], contact_email: str, own_email: str, contact_count: int) -> bool:
    sender_email = parseaddr(metadata.get("from", ""))[1].casefold()
    if sender_email == contact_email:
        return True
    if sender_email != own_email:
        return False
    recipients = {
        address.casefold()
        for _, address in getaddresses([metadata.get("to", ""), metadata.get("cc", ""), metadata.get("bcc", "")])
        if address and address.casefold() != own_email
    }
    return contact_email in recipients or not recipients or contact_count == 1


def _is_delivery_failure(metadata: dict[str, str]) -> bool:
    sender = metadata.get("from", "").casefold()
    subject = metadata.get("subject", "").casefold()
    return any(value in sender for value in ("mail delivery subsystem", "mailer-daemon", "postmaster")) or any(
        value in subject
        for value in ("delivery status notification", "delivery failure", "undeliverable", "недостав", "не удалось доставить")
    )


def _decode_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError):
        return value


def _payload_text(payload: dict) -> str:
    plain: list[str] = []
    rich: list[str] = []

    def collect(part: dict) -> None:
        mime_type = str(part.get("mimeType", ""))
        data = str(part.get("body", {}).get("data", ""))
        if data and mime_type in {"text/plain", "text/html"}:
            decoded = _decode_body(data)
            (plain if mime_type == "text/plain" else rich).append(decoded)
        for child in part.get("parts", []):
            collect(child)

    collect(payload)
    if plain:
        return "\n".join(plain)
    if rich:
        parser = _HtmlTextExtractor()
        parser.feed("\n".join(rich))
        return parser.text
    return ""


def _decode_body(value: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        return raw.decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _clean_message_text(value: str) -> str:
    return _split_message_text(value)[0]


def _split_message_text(value: str) -> tuple[str, str]:
    lines = value.replace("\r", "").splitlines()
    boundary = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        next_lines = "\n".join(lines[index + 1 : index + 5])
        is_header_block = re.match(r"^(кому|to)\s*:", stripped, re.IGNORECASE) and re.search(
            r"^(тема|subject)\s*:", next_lines, re.IGNORECASE | re.MULTILINE
        )
        if is_header_block or stripped.startswith(">") or re.match(
            r"^(on .+wrote:|от\s*:|from\s*:|--\s*$|с уважением[,.!]*$|best regards[,.!]*$|regards[,.!]*$|отправлено с |sent from |_{3,}|-{3,}\s*(original|forwarded)|.+\b\d{4}\s*г?\..*<[^>]+>:\s*)",
            stripped,
            re.IGNORECASE,
        ):
            boundary = index
            break
    main = re.sub(r"\n{3,}", "\n\n", "\n".join(lines[:boundary])).strip()
    technical = re.sub(r"\n{3,}", "\n\n", "\n".join(lines[boundary:])).strip()
    return main, technical


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip:
            self._skip += 1
            return
        classes = dict(attrs).get("class", "") or ""
        if tag in {"blockquote", "script", "style"} or "gmail_quote" in classes:
            self._skip = 1
        elif tag in {"br", "p", "div", "li"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip:
            self._skip -= 1
        elif tag in {"p", "div", "li"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def _refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    data = _json_response(response, "Не удалось обновить доступ Gmail")
    access_token = str(data.get("access_token", ""))
    if not access_token:
        raise GmailError("Google не вернул access token.")
    return access_token


def _json_response(response: httpx.Response, prefix: str) -> dict:
    if response.status_code == 204:
        return {}
    try:
        data = response.json()
    except ValueError as exc:
        raise GmailError(f"{prefix}: HTTP {response.status_code}") from exc
    if response.is_error:
        message = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
        raise GmailError(f"{prefix}: {message or response.status_code}")
    return data
