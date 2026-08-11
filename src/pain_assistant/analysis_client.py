from __future__ import annotations

import json

from .ai_client import AdvisorAiClient
from .config import AppConfig
from .codex_cli_client import CodexCliClient
from .models import AdvisorResult, ClientProfile
from .openclaw_client import OpenClawClient, OpenClawClientError
from .ssh_tunnel import SshTunnelError, ensure_openclaw_tunnel
from .stub_client import StubAnalysisClient, StubAnalysisClientError
from .telegram_importer import compact_messages


class AnalysisClientError(RuntimeError):
    pass


REMOTE_GATEWAY_BACKENDS = {"codex", "openclaw"}


class AnalysisClient:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.backend = config.analysis_backend.lower().strip()
        self.deprecated_openai = AdvisorAiClient(config.openai_api_key, config.openai_model)
        self.ocr_fallback = AdvisorAiClient(None, config.openai_model)
        self.stub = StubAnalysisClient(config.stub_response_path)
        self.codex_cli = CodexCliClient(config.codex_bin, config.openclaw_model)
        self.use_direct_codex = bool(config.codex_bin and self.codex_cli.available())
        self.openclaw: OpenClawClient | None = None
        if config.openclaw_base_url:
            self.openclaw = OpenClawClient(
                base_url=config.openclaw_base_url,
                token=config.openclaw_token,
                model=config.openclaw_model,
                session_key=config.openclaw_session_key,
                send_image=config.openclaw_send_image,
                backend=self.backend,
            )

    def set_model(self, model: str) -> None:
        self.codex_cli.model = model
        if self.openclaw:
            self.openclaw.model = model

    def build_profile(
        self,
        chat_name: str,
        messages: list[dict],
        previous_profile: ClientProfile | None = None,
    ) -> ClientProfile:
        if self.backend in {"stub", "mock"}:
            return self.stub.build_profile(chat_name, messages)
        if self.backend == "codex" and self.use_direct_codex:
            try:
                return self.codex_cli.build_profile(chat_name, messages, previous_profile)
            except OpenClawClientError as exc:
                raise AnalysisClientError(str(exc)) from exc
        if self.backend in REMOTE_GATEWAY_BACKENDS and self.openclaw:
            try:
                ensure_openclaw_tunnel(self.config)
                return self.openclaw.build_profile(chat_name, messages, previous_profile)
            except (OpenClawClientError, SshTunnelError) as exc:
                if self.backend == "codex":
                    raise AnalysisClientError(_gateway_unavailable_message(self.config, exc)) from exc
                return self.deprecated_openai.build_profile(chat_name, messages, previous_profile)
        return self.deprecated_openai.build_profile(chat_name, messages, previous_profile)

    def build_user_style(self, chat_name: str, messages: list[dict], previous_style: str = "") -> str:
        if self.backend in {"stub", "mock"}:
            return self.stub.build_user_style(chat_name, messages, previous_style)
        if self.backend == "codex" and self.use_direct_codex:
            try:
                return self.codex_cli.build_user_style(chat_name, messages, previous_style)
            except OpenClawClientError as exc:
                raise AnalysisClientError(str(exc)) from exc
        if self.backend in REMOTE_GATEWAY_BACKENDS and self.openclaw:
            try:
                ensure_openclaw_tunnel(self.config)
                return self.openclaw.build_user_style(chat_name, messages, previous_style)
            except (OpenClawClientError, SshTunnelError) as exc:
                if self.backend == "codex":
                    raise AnalysisClientError(_gateway_unavailable_message(self.config, exc)) from exc
                return self.deprecated_openai.build_user_style(chat_name, messages, previous_style)
        return self.deprecated_openai.build_user_style(chat_name, messages, previous_style)

    def rewrite_message(
        self,
        source_text: str,
        mode: str,
        recent_messages: list[dict],
        conversation_summary: str = "",
        user_style_profile: str = "",
    ) -> str:
        kwargs = {
            "conversation_summary": conversation_summary,
            "user_style_profile": user_style_profile,
        }
        if self.backend in {"stub", "mock"}:
            return self.stub.rewrite_message(source_text, mode, recent_messages, **kwargs)
        if self.backend == "codex" and self.use_direct_codex:
            try:
                return self.codex_cli.rewrite_message(source_text, mode, recent_messages, **kwargs)
            except OpenClawClientError as exc:
                raise AnalysisClientError(str(exc)) from exc
        if self.backend in REMOTE_GATEWAY_BACKENDS:
            if not self.openclaw:
                raise AnalysisClientError("Не настроен AI gateway.")
            try:
                ensure_openclaw_tunnel(self.config)
                return self.openclaw.rewrite_message(source_text, mode, recent_messages, **kwargs)
            except (OpenClawClientError, SshTunnelError) as exc:
                if self.backend == "codex":
                    raise AnalysisClientError(_gateway_unavailable_message(self.config, exc)) from exc
                return self.deprecated_openai.rewrite_message(source_text, mode, recent_messages, **kwargs)
        if self.backend in {"openai", "deprecated_openai"}:
            return self.deprecated_openai.rewrite_message(source_text, mode, recent_messages, **kwargs)
        return self.ocr_fallback.rewrite_message(source_text, mode, recent_messages, **kwargs)

    def extract_projects(self, profile: ClientProfile, messages: list[dict]) -> list[dict]:
        """Find only explicit work commitments; unknown values stay empty or zero."""
        history = compact_messages(messages, max_chars=40_000) or "нет сообщений"
        instructions = (
            "Ты извлекаешь рабочие проекты из переписки для локального реестра. "
            "Верни JSON строго вида {\"projects\":[...]}. Каждый проект: title, price, paid_amount, payable, paid_out, deadline, paid_at, paid_out_at, status, notes. "
            "Я в переписке - Михаил. price: контакт должен мне; paid_amount: контакт уже заплатил мне; payable: я должен контакту; paid_out: я уже заплатил контакту. "
            "Все суммы - целые рубли, только если направление и сумма прямо названы; иначе 0. "
            "deadline, paid_at и paid_out_at - YYYY-MM-DD, только если дата прямо названа или однозначно выводится из даты сообщения; иначе пустая строка. "
            "status: planned, progress, waiting_payment или done. notes - 1-4 фактических предложения. "
            "Не создавай проект для личного разговора, переноса чата, обмена контактами, общих идей или неопределённых планов. "
            "Не выдумывай название, сумму, оплату, сроки или статус. Если подтверждённой рабочей задачи нет, верни {\"projects\":[]}."
        )
        text = (
            f"Контакт: {profile.chat_name}\n"
            f"Сохранённые договорённости: {profile.agreements or 'нет'}\n"
            f"Сохранённая информация об оплате: {profile.payment_promises or 'нет'}\n\n"
            f"Переписка:\n{history}"
        )
        try:
            if self.backend == "codex" and self.use_direct_codex:
                raw = self.codex_cli._create_response(instructions, text)
            elif self.backend in REMOTE_GATEWAY_BACKENDS and self.openclaw:
                ensure_openclaw_tunnel(self.config)
                raw = self.openclaw._create_response(instructions, text)
            else:
                raise AnalysisClientError("Извлечение проектов доступно при подключённом Codex.")
            data = json.loads(raw)
        except (json.JSONDecodeError, OpenClawClientError, SshTunnelError) as exc:
            raise AnalysisClientError(f"Не удалось извлечь проекты из переписки: {exc}") from exc
        projects = data.get("projects", []) if isinstance(data, dict) else []
        return [item for item in projects if isinstance(item, dict)][:12]

    def extract_working_memory(
        self,
        profile: ClientProfile,
        messages: list[dict],
        work_profile: dict,
        current_memory: dict,
    ) -> dict:
        history = _working_memory_history(messages)
        instructions = (
            "Ты обновляешь рабочую память фрилансера по переписке. Верни только JSON без Markdown. "
            "Формат: {\"relationship_status\":\"...\",\"context_summary\":\"...\","
            "\"next_action\":\"...\",\"next_contact_at\":\"YYYY-MM-DD или пусто\",\"items\":[...]}. "
            "Каждый item: kind, category, title, details, actor, status, amount, currency, due_at, "
            "certainty, confidence, source_message_ids. "
            "kind только task, commitment, money_event, follow_up, status или note. "
            "actor: me, them или unknown. certainty: CONFIRMED, INFERRED или UNCERTAIN. "
            "Для money_event category: agreed, received, expected, payable или paid_out. "
            "Сумму указывай только если она прямо названа и понятно направление платежа. "
            "CONFIRMED допустим только для явной договорённости или сообщения о факте. "
            "Не превращай предложение, приблизительную оценку или вопрос в подтверждённый факт. "
            "source_message_ids должны содержать только message_id из переданной переписки, на которых основан item. "
            "Не создавай записи для обычного флуда. Не повторяй существующую память без новой информации. "
            "Если важных изменений нет, верни items: []."
        )
        text = (
            f"Контакт: {profile.chat_name}\n\n"
            f"Рабочий профиль пользователя:\n{json.dumps(work_profile, ensure_ascii=False)[:12000]}\n\n"
            f"Текущая рабочая память:\n{json.dumps(current_memory, ensure_ascii=False)[:16000]}\n\n"
            f"Новая порция переписки:\n{history}"
        )
        try:
            if self.backend == "codex" and self.use_direct_codex:
                raw = self.codex_cli._create_response(instructions, text)
            elif self.backend in REMOTE_GATEWAY_BACKENDS and self.openclaw:
                ensure_openclaw_tunnel(self.config)
                raw = self.openclaw._create_response(instructions, text)
            elif self.backend in {"stub", "mock"}:
                return {
                    "relationship_status": "unknown",
                    "context_summary": "",
                    "next_action": "",
                    "next_contact_at": "",
                    "items": [],
                }
            else:
                raise AnalysisClientError("Обновление рабочей памяти доступно при подключённом Codex.")
            data = json.loads(raw)
        except (json.JSONDecodeError, OpenClawClientError, SshTunnelError) as exc:
            raise AnalysisClientError(f"Не удалось обновить рабочую память: {exc}") from exc
        if not isinstance(data, dict):
            raise AnalysisClientError("Codex вернул неверный формат рабочей памяти.")
        items = data.get("items", [])
        data["items"] = [item for item in items if isinstance(item, dict)][:30] if isinstance(items, list) else []
        return data

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
        result: AdvisorResult
        if self.backend == "codex" and self.use_direct_codex:
            try:
                result = self.codex_cli.analyze_screenshot(
                    png_bytes,
                    profile,
                    recent_messages,
                    tone,
                    server_screenshot_path=server_screenshot_path,
                    previous_analysis_context=previous_analysis_context,
                    user_comment=user_comment,
                    user_style_profile=user_style_profile,
                    business_calendar_context=business_calendar_context,
                    progress=progress,
                )
                return _adjust_recommended_tone(result, profile, recent_messages, user_comment, user_style_profile)
            except OpenClawClientError as exc:
                raise AnalysisClientError(str(exc)) from exc
        if self.backend in REMOTE_GATEWAY_BACKENDS:
            if not self.openclaw:
                raise AnalysisClientError(
                    "Не настроен AI gateway. Проверь OPENCLAW_BASE_URL / OPENCLAW_TOKEN в .env."
                )
            try:
                if progress:
                    progress("Проверяю доступность Codex gateway...")
                ensure_openclaw_tunnel(self.config)
                result = self.openclaw.analyze_screenshot(
                    png_bytes,
                    profile,
                    recent_messages,
                    tone,
                    server_screenshot_path=server_screenshot_path,
                    previous_analysis_context=previous_analysis_context,
                    user_comment=user_comment,
                    user_style_profile=user_style_profile,
                    business_calendar_context=business_calendar_context,
                    progress=progress,
                )
                return _adjust_recommended_tone(result, profile, recent_messages, user_comment, user_style_profile)
            except (OpenClawClientError, SshTunnelError) as exc:
                if self.backend == "codex":
                    raise AnalysisClientError(_gateway_unavailable_message(self.config, exc)) from exc
                result = self.ocr_fallback.analyze_screenshot(
                    png_bytes,
                    profile,
                    recent_messages,
                    tone,
                    previous_analysis_context=previous_analysis_context,
                    user_comment=user_comment,
                    user_style_profile=user_style_profile,
                    business_calendar_context=business_calendar_context,
                )
                return _adjust_recommended_tone(result, profile, recent_messages, user_comment, user_style_profile)

        if self.backend in {"stub", "mock"}:
            try:
                result = self.stub.analyze_screenshot(
                    png_bytes,
                    profile,
                    recent_messages,
                    tone,
                    server_screenshot_path=server_screenshot_path,
                    previous_analysis_context=previous_analysis_context,
                    user_comment=user_comment,
                    user_style_profile=user_style_profile,
                    business_calendar_context=business_calendar_context,
                    progress=progress,
                )
                return _adjust_recommended_tone(result, profile, recent_messages, user_comment, user_style_profile)
            except StubAnalysisClientError as exc:
                raise AnalysisClientError(str(exc)) from exc

        if self.backend in {"openai", "deprecated_openai"}:
            result = self.deprecated_openai.analyze_screenshot(
                png_bytes,
                profile,
                recent_messages,
                tone,
                previous_analysis_context=previous_analysis_context,
                user_comment=user_comment,
                user_style_profile=user_style_profile,
                business_calendar_context=business_calendar_context,
            )
            return _adjust_recommended_tone(result, profile, recent_messages, user_comment, user_style_profile)

        if self.backend == "ocr":
            result = self.ocr_fallback.analyze_screenshot(
                png_bytes,
                profile,
                recent_messages,
                tone,
                previous_analysis_context=previous_analysis_context,
                user_comment=user_comment,
                user_style_profile=user_style_profile,
                business_calendar_context=business_calendar_context,
            )
            return _adjust_recommended_tone(result, profile, recent_messages, user_comment, user_style_profile)

        raise AnalysisClientError(
            f"Неизвестный ANALYSIS_BACKEND={self.config.analysis_backend!r}. "
            "Используй codex, openclaw, stub, openai или ocr."
        )


def _working_memory_history(messages: list[dict], max_chars: int = 48_000) -> str:
    lines: list[str] = []
    total = 0
    for message in reversed(messages):
        message_id = message.get("telegram_message_id")
        if message_id is None:
            message_id = f"row:{message.get('context_row_id', 0)}"
        direction = "я" if message.get("out") else "контакт"
        line = (
            f"[message_id={message_id}; date={message.get('message_date') or message.get('date') or ''}; "
            f"author={direction}; sender={message.get('sender', '')}]\n"
            f"{str(message.get('text', '')).strip()}"
        ).strip()
        if not line:
            continue
        total += len(line)
        if total > max_chars and lines:
            break
        lines.append(line)
    return "\n\n".join(reversed(lines)) or "нет сообщений"


def _gateway_unavailable_message(config: AppConfig, exc: Exception) -> str:
    return (
        "Codex gateway недоступен, локальный fallback отключен для ANALYSIS_BACKEND=codex.\n"
        f"Проверь на клиенте OPENCLAW_BASE_URL={config.openclaw_base_url!r}, "
        "а на сервере что codex-gateway отвечает на /health и /v1/responses.\n"
        f"Детали: {exc}"
    )


def _adjust_recommended_tone(
    result: AdvisorResult,
    profile: ClientProfile | None,
    recent_messages: list[dict],
    user_comment: str,
    user_style_profile: str = "",
) -> AdvisorResult:
    if result.effective_recommended_tone() != "жёсткий":
        if user_style_profile and not result.recommended_tone:
            result.recommended_tone = "мой стиль"
        return result

    context = "\n".join(
        [
            profile.chat_name if profile else "",
            profile.communication_style if profile else "",
            profile.tone_recommendations if profile else "",
            user_comment,
            "\n".join(str(message.get("text", "")) for message in recent_messages[-30:]),
            result.risk,
            result.recommended_strategy,
            result.client_intent,
        ]
    ).lower()
    if not _looks_personal_context(context):
        return result
    if _hard_tone_explicitly_requested(user_comment) or _has_severe_pressure(context):
        return result

    result.recommended_tone = "спокойный"
    reason = (
        "Личный/семейный контекст: лучше отвечать спокойно, но с ясной границей, "
        "без излишне жесткой формулировки."
    )
    result.tone_reason = f"{reason} {result.tone_reason}".strip()
    return result


def _looks_personal_context(text: str) -> bool:
    return any(
        word in text
        for word in [
            "сестр",
            "брат",
            "мама",
            "мать",
            "папа",
            "отец",
            "родител",
            "жена",
            "муж",
            "дочь",
            "дочка",
            "сын",
            "семь",
            "родствен",
        ]
    )


def _hard_tone_explicitly_requested(user_comment: str) -> bool:
    text = user_comment.lower()
    return any(word in text for word in ["жестко", "жёстко", "жесткий", "жёсткий", "строго", "резко"])


def _has_severe_pressure(text: str) -> bool:
    return any(
        word in text
        for word in [
            "угроз",
            "шантаж",
            "оскорб",
            "унижа",
            "ультимат",
            "манипуляц",
            "агресс",
            "преслед",
        ]
    )
