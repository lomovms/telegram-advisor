from __future__ import annotations

import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


class TelegramExportError(RuntimeError):
    pass


def load_export(path: Path) -> tuple[str, list[dict]]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_json(path)
    if suffix in {".html", ".htm"}:
        return _load_html_export(path)
    raise TelegramExportError(f"Unsupported export format: {path.suffix}")


def compact_messages(messages: list[dict], max_chars: int = 24_000) -> str:
    lines: list[str] = []
    total = 0
    for message in reversed(messages[-350:]):
        message_date = _local_message_date(message.get("message_date") or message.get("date", ""))
        line = f"{message_date} | {message.get('sender', '')}: {message.get('text', '')}".strip()
        if not line:
            continue
        if total + len(line) > max_chars and lines:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(reversed(lines))


def _local_message_date(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M")


def save_clean_text_export(
    messages: list[dict],
    source_path: Path,
    output_dir: Path | None = None,
    export_name: str | None = None,
) -> Path:
    target_dir = output_dir or Path("data/cleaned-exports")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_stem = _safe_filename(
        export_name
        or (source_path.parent.name if source_path.name.lower().startswith("messages") else source_path.stem)
    )
    target = target_dir / f"{target_stem}.txt"
    lines = [
        f"{message.get('date', '')} | {message.get('sender', '')}: {message.get('text', '')}".strip()
        for message in messages
        if message.get("text")
    ]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def _load_json(path: Path) -> tuple[str, list[dict]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TelegramExportError(f"Cannot read JSON export: {exc}") from exc

    chat_name = str(payload.get("name") or payload.get("title") or path.stem)
    raw_messages = payload.get("messages", [])
    if not isinstance(raw_messages, list):
        raise TelegramExportError("JSON export does not contain a messages list")

    messages = []
    for item in raw_messages:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        text = _clean_message_text(_telegram_text_to_plain(item.get("text", "")))
        if text:
            messages.append(
                {
                    "date": str(item.get("date", "")),
                    "sender": str(item.get("from") or item.get("actor") or ""),
                    "text": text,
                }
            )
    return chat_name, messages


def _telegram_text_to_plain(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text", "")))
        return "".join(parts).strip()
    return ""


def _load_html_export(path: Path) -> tuple[str, list[dict]]:
    parts = _html_export_parts(path)
    messages: list[dict] = []
    chat_name = ""
    for part in parts:
        parser = _parse_html(part)
        if not chat_name and parser.chat_name:
            chat_name = parser.chat_name
        messages.extend(parser.messages)
    if not messages:
        raise TelegramExportError("No messages found in HTML export")
    return _preferred_html_chat_name(path, chat_name), _dedupe_messages(messages)


def _html_export_parts(path: Path) -> list[Path]:
    match = re.fullmatch(r"messages(?:\d+)?", path.stem, flags=re.IGNORECASE)
    if not match:
        return [path]

    parts = [
        candidate
        for candidate in path.parent.iterdir()
        if candidate.is_file()
        and candidate.suffix.lower() in {".html", ".htm"}
        and re.fullmatch(r"messages(?:\d+)?", candidate.stem, flags=re.IGNORECASE)
    ]
    if not parts:
        return [path]
    return sorted(parts, key=_html_part_sort_key)


def _html_part_sort_key(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r"messages(?:(\d+))?", path.stem, flags=re.IGNORECASE)
    if not match:
        return (10**9, path.name.lower())
    number = match.group(1)
    return (int(number) if number else 1, path.name.lower())


def _chat_name_from_html_path(path: Path) -> str:
    return path.parent.name if re.fullmatch(r"messages(?:\d+)?", path.stem, flags=re.IGNORECASE) else path.stem


def _preferred_html_chat_name(path: Path, parsed_chat_name: str) -> str:
    folder_name = path.parent.name.strip()
    if _looks_like_contact_folder(folder_name):
        return folder_name
    return parsed_chat_name or _chat_name_from_html_path(path)


def _looks_like_contact_folder(folder_name: str) -> bool:
    if not folder_name:
        return False
    technical_names = {
        "chat",
        "chats",
        "chatexport",
        "data",
        "export",
        "telegram",
        "telegram desktop",
        "telegram_export",
        "result",
        "history",
    }
    lowered = folder_name.lower()
    compact_lowered = re.sub(r"[\W_]+", "", lowered)
    if (
        lowered in technical_names
        or compact_lowered in technical_names
        or lowered.startswith("chat_")
        or lowered.startswith("export_")
        or lowered.startswith("chatexport")
        or compact_lowered.startswith("chatexport")
    ):
        return False
    if re.fullmatch(r"messages(?:\d+)?", folder_name, flags=re.IGNORECASE):
        return False
    return any(char.isalpha() for char in folder_name)


def _dedupe_messages(messages: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict] = []
    for message in messages:
        key = (
            str(message.get("date", "")),
            str(message.get("sender", "")),
            str(message.get("text", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(message)
    return unique


def _parse_html(path: Path) -> "_TelegramHtmlParser":
    parser = _TelegramHtmlParser()
    try:
        parser.feed(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        parser.feed(path.read_text(encoding="utf-8-sig", errors="ignore"))
    return parser


def _clean_message_text(text: str) -> str:
    cleaned = " ".join(text.replace("\xa0", " ").split())
    if not cleaned:
        return ""
    noise_values = {
        "edited",
        "forwarded",
        "reply",
    }
    if cleaned.lower() in noise_values:
        return ""
    return cleaned


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or "telegram-export"


class _TelegramHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.chat_name = ""
        self.messages: list[dict] = []
        self._current: dict | None = None
        self._message_depth = 0
        self._capture: str | None = None
        self._capture_depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class", "") or ""
        class_set = set(classes.split())
        if tag == "div" and "message" in class_set and "service" not in class_set:
            self._current = {"date": "", "sender": "", "text": ""}
            self._message_depth = 1
        elif self._current is not None:
            self._message_depth += 1

        if self._capture:
            self._capture_depth += 1

        if self._current is None and "page_header" in class_set:
            self._start_capture("chat_name")
            return

        if self._current is not None:
            if "from_name" in class_set:
                self._start_capture("sender")
            elif "text" in class_set:
                self._start_capture("text")
            elif "date" in class_set:
                title = dict(attrs).get("title")
                if title:
                    self._current["date"] = title

    def handle_endtag(self, tag: str) -> None:
        if self._capture == "chat_name":
            self._capture_depth -= 1
            if self._capture_depth <= 0:
                value = " ".join("".join(self._buffer).split())
                if value:
                    self.chat_name = value
                self._capture = None
                self._capture_depth = 0
                self._buffer = []
            return

        if self._capture and self._current is not None:
            self._capture_depth -= 1
        if self._capture and self._capture_depth <= 0 and self._current is not None:
            value = " ".join("".join(self._buffer).split())
            if value:
                if self._capture == "text":
                    value = _clean_message_text(value)
                self._current[self._capture] = value
            self._capture = None
            self._capture_depth = 0
            self._buffer = []
        if self._current is not None:
            self._message_depth -= 1
            if self._message_depth <= 0:
                if self._current.get("text"):
                    self.messages.append(self._current)
                self._current = None
                self._message_depth = 0

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def _start_capture(self, name: str) -> None:
        self._capture = name
        self._capture_depth = 1
        self._buffer = []
