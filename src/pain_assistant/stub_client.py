from __future__ import annotations

import json
from pathlib import Path

from .json_utils import parse_json_object
from .models import AdvisorResult, ClientProfile


class StubAnalysisClientError(RuntimeError):
    pass


class StubAnalysisClient:
    def __init__(self, response_path: Path | None = None) -> None:
        self.response_path = response_path

    def build_profile(self, chat_name: str, messages: list[dict]) -> ClientProfile:
        senders = sorted({str(message.get("sender", "")) for message in messages if message.get("sender")})
        return ClientProfile(
            id=None,
            chat_name=chat_name,
            agreements="Stub profile: договоренности не анализировались реальной моделью.",
            payment_promises="Stub profile: обещания оплаты не анализировались реальной моделью.",
            disputed_points="Stub profile: спорные моменты не анализировались реальной моделью.",
            behavior_patterns=f"Stub profile: найдено сообщений: {len(messages)}.",
            communication_style=", ".join(senders[:5]) or "Stub profile: отправители не определены.",
            tone_recommendations="Stub profile: используйте короткий деловой ответ с фиксацией следующего шага.",
        )

    def build_user_style(self, chat_name: str, messages: list[dict], previous_style: str = "") -> str:
        return previous_style or "Stub style: писать коротко, естественно и без канцелярита."

    def rewrite_message(
        self,
        source_text: str,
        mode: str,
        recent_messages: list[dict],
        conversation_summary: str = "",
        user_style_profile: str = "",
    ) -> str:
        if mode == "short":
            return source_text.split(".")[0].strip()
        return source_text.strip()

    def analyze_screenshot(
        self,
        png_bytes: bytes,
        profile: ClientProfile | None,
        recent_messages: list[dict],
        tone: str,
        server_screenshot_path: str | None = None,
        previous_analysis_context: str = "",
        user_comment: str = "",
        user_style_profile: str = "",
        business_calendar_context: str = "",
        progress=None,
    ) -> AdvisorResult:
        if self.response_path:
            try:
                raw = self.response_path.read_text(encoding="utf-8")
                return AdvisorResult.from_dict(parse_json_object(raw))
            except OSError as exc:
                raise StubAnalysisClientError(f"Не удалось прочитать STUB_RESPONSE_PATH: {self.response_path}") from exc
            except ValueError as exc:
                raise StubAnalysisClientError(
                    f"STUB_RESPONSE_PATH должен содержать валидный AdvisorResult JSON: {self.response_path}"
                ) from exc

        profile_name = profile.chat_name if profile else "без выбранного профиля"
        comment_note = f" Комментарий пользователя получен: {user_comment[:120]}." if user_comment else ""
        style_note = " Стиль пользователя получен." if user_style_profile else ""
        return AdvisorResult(
            situation_summary=(
                "Stub response: сеть и модель не вызывались. "
                f"Получен скриншот {len(png_bytes)} bytes, профиль: {profile_name}.{comment_note}{style_note}"
            ),
            client_intent="Stub response: намерение клиента не анализировалось реальной моделью.",
            risk="Stub response: контракт ответа работает, но это не реальная оценка риска.",
            recommended_tone="деловой",
            tone_reason="Stub response: базовый нейтральный стиль для проверки UI.",
            recommended_strategy=(
                f"Stub response: выбран тон '{tone}'. Зафиксируйте объем, оплату и следующий шаг."
            ),
            do_not_do=[
                "Не считать stub-ответ реальной рекомендацией.",
                "Не отправлять ответ без проверки человеком.",
            ],
            best_reply="Понял. Давайте зафиксируем текущий объем, оплату и следующий шаг, чтобы двигаться без разночтений.",
            soft_reply="Понял вас. Предлагаю спокойно зафиксировать, что входит в текущий этап, и после этого двигаться дальше.",
            hard_reply="Готов продолжить после фиксации объема и оплаты текущего этапа. Новые задачи предлагаю оценить отдельно.",
            confidence=0.0,
        )

    def raw_stub_json(self) -> str:
        result = self.analyze_screenshot(b"stub-png-bytes", None, [], "деловой")
        return json.dumps(result.__dict__, ensure_ascii=False, indent=2)
