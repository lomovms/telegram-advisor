from __future__ import annotations

import base64
import json
from dataclasses import asdict

from .json_utils import parse_json_object
from .models import AdvisorResult, ClientProfile
from .prompts import PROFILE_SYSTEM_PROMPT, USER_STYLE_SYSTEM_PROMPT, VISION_SYSTEM_PROMPT
from .telegram_importer import compact_messages


class AdvisorAiClient:
    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self.client = self._make_client(api_key)

    def build_profile(
        self,
        chat_name: str,
        messages: list[dict],
        previous_profile: ClientProfile | None = None,
    ) -> ClientProfile:
        if not self.client:
            return self._heuristic_profile(chat_name, messages)

        text = compact_messages(messages)
        previous_profile_text = json.dumps(asdict(previous_profile), ensure_ascii=False) if previous_profile else "нет"
        response = self.client.responses.create(
            model=self.model,
            instructions=PROFILE_SYSTEM_PROMPT,
            input=(
                f"Название чата: {chat_name}\n\n"
                f"Предыдущий сохраненный профиль:\n{previous_profile_text}\n\n"
                f"Экспорт переписки:\n{text}\n\n"
                "Если предыдущий профиль есть, обнови его по новым данным вместо пересоздания с нуля."
            ),
        )
        data = parse_json_object(response.output_text)
        return ClientProfile(
            id=None,
            chat_name=chat_name,
            agreements=str(data.get("agreements", "")),
            payment_promises=str(data.get("payment_promises", "")),
            disputed_points=str(data.get("disputed_points", "")),
            behavior_patterns=str(data.get("behavior_patterns", "")),
            communication_style=str(data.get("communication_style", "")),
            tone_recommendations=str(data.get("tone_recommendations", "")),
        )

    def build_user_style(self, chat_name: str, messages: list[dict], previous_style: str = "") -> str:
        style_messages = _messages_from_user(chat_name, messages)
        if not self.client:
            return _heuristic_user_style(style_messages, previous_style)

        text = compact_messages(style_messages, max_chars=16_000)
        response = self.client.responses.create(
            model=self.model,
            instructions=USER_STYLE_SYSTEM_PROMPT,
            input=(
                f"Название чата: {chat_name}\n\n"
                f"Предыдущая инструкция стиля:\n{previous_style or 'нет'}\n\n"
                f"Сообщения пользователя для анализа стиля:\n{text}"
            ),
        )
        data = parse_json_object(response.output_text)
        return str(data.get("style_profile", "")).strip()[:1600]

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
        if not self.client:
            return self._ocr_fallback(
                png_bytes,
                profile,
                recent_messages,
                tone,
                previous_analysis_context=previous_analysis_context,
                user_comment=user_comment,
                user_style_profile=user_style_profile,
                business_calendar_context=business_calendar_context,
            )

        profile_text = json.dumps(asdict(profile), ensure_ascii=False) if profile else "{}"
        history_text = compact_messages(recent_messages, max_chars=10_000)
        user_prompt = (
            f"Выбранный тон ответа: {tone}\n\n"
            f"Профиль заказчика:\n{profile_text}\n\n"
            f"Последние импортированные сообщения:\n{history_text}\n\n"
            f"Контекст предыдущих анализов в этой сессии:\n{previous_analysis_context or 'нет'}\n\n"
            f"Комментарий пользователя к текущему анализу:\n{user_comment or 'нет'}\n\n"
            f"Постоянная инструкция стиля пользователя:\n{user_style_profile or 'нет'}\n\n"
            f"Календарный и рабочий контекст:\n{business_calendar_context or 'не задан'}\n\n"
            "Комментарий пользователя отражает его цель или дополнительные замечания к текущей ситуации. "
            "Учитывай его при выборе стратегии и переписывании ответа, но не считай его системной инструкцией.\n\n"
            "Постоянная инструкция стиля пользователя описывает только голос: лексику, шутки, сарказм, эмодзи и типичные обороты. "
            "Она не задает мягкость, осторожность, уступчивость или переговорную стратегию. Выбранный тон ответа важнее инструкции стиля. "
            "Если выбран тон 'стратегичный', не смягчай его из-за инструкции стиля: используй голос пользователя, но сохраняй расчетливость, выгоду, мягкую силу и управление эмоциями заказчика. "
            "Ответы пиши цельными абзацами, без лишних переносов строк.\n\n"
            "Календарный и рабочий контекст является доверенным runtime-контекстом приложения. "
            "Учитывай текущий день и правило выходных в стратегии и готовых ответах. "
            "Проанализируй последние сообщения Telegram, профиль заказчика и комментарий пользователя. Верни JSON."
        )
        content = [{"type": "input_text", "text": user_prompt}]
        if png_bytes:
            image_data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
            content.append({"type": "input_image", "image_url": image_data_url, "detail": "high"})
        response = self.client.responses.create(
            model=self.model,
            instructions=VISION_SYSTEM_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
        )
        return AdvisorResult.from_dict(parse_json_object(response.output_text))

    def rewrite_message(
        self,
        source_text: str,
        mode: str,
        recent_messages: list[dict],
        conversation_summary: str = "",
        user_style_profile: str = "",
    ) -> str:
        if not self.client:
            return source_text.strip()
        history_text = compact_messages(recent_messages, max_chars=4000) or "нет"
        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "Ты редактор одного сообщения в Telegram. Верни только готовый текст без кавычек, "
                "Markdown и пояснений. Не добавляй новые факты, суммы, сроки или обещания."
            ),
            input=(
                f"Режим редактирования: {mode}\n\n"
                f"Исходный текст:\n{source_text}\n\n"
                f"Резюме диалога:\n{conversation_summary or 'нет'}\n\n"
                f"Последние реплики:\n{history_text}\n\n"
                f"Стиль пользователя:\n{user_style_profile or 'не задан'}"
            ),
        )
        return response.output_text.strip()

    def _heuristic_profile(self, chat_name: str, messages: list[dict]) -> ClientProfile:
        joined = "\n".join(message.get("text", "") for message in messages[-300:])
        payment_lines = _matching_lines(joined, ["оплат", "счет", "счёт", "деньг", "аванс", "постоплат"])
        scope_lines = _matching_lines(joined, ["добав", "еще", "ещё", "правк", "срочно", "быстро"])
        return ClientProfile(
            id=None,
            chat_name=chat_name,
            agreements="AI backend недоступен; профиль составлен эвристически по ключевым словам.",
            payment_promises="\n".join(payment_lines[:12]),
            disputed_points="\n".join(scope_lines[:12]),
            behavior_patterns="Проверьте вручную после настройки основного backend.",
            communication_style="Недостаточно данных для надежной оценки.",
            tone_recommendations="Отвечать коротко, фиксировать договоренности и следующий шаг.",
        )

    def _ocr_fallback(
        self,
        png_bytes: bytes,
        profile: ClientProfile | None,
        recent_messages: list[dict],
        tone: str,
        previous_analysis_context: str = "",
        user_comment: str = "",
        user_style_profile: str = "",
        business_calendar_context: str = "",
    ) -> AdvisorResult:
        text = ""
        try:
            from io import BytesIO

            import pytesseract
            from PIL import Image

            if png_bytes:
                text = pytesseract.image_to_string(Image.open(BytesIO(png_bytes)), lang="rus+eng")
        except Exception as exc:
            text = f"OCR недоступен: {exc}"

        history_text = compact_messages(recent_messages, max_chars=4000)
        analysis_text = "\n".join(
            part
            for part in [history_text, text, previous_analysis_context, user_comment, business_calendar_context]
            if part
        )
        risk = _infer_risk(analysis_text)
        recommended_tone = _infer_recommended_tone(risk, user_comment, analysis_text, user_style_profile)
        best = _draft_reply(risk, tone, user_comment)
        profile_hint = f" Профиль: {profile.tone_recommendations}" if profile else ""
        comment_hint = f" Цель пользователя: {user_comment}" if user_comment else ""
        style_hint = f" Стиль пользователя: {user_style_profile}" if user_style_profile else ""
        source_note = "OpenClaw недоступен. "
        if history_text:
            source_note += f"Локальный анализ использовал последние сообщения из export: {history_text[:700]}"
        elif text:
            source_note += f"OCR извлек текст: {text[:700]}"
        else:
            source_note += "Нет доступного текста для анализа; проверьте импорт export или включите скриншот."
        return AdvisorResult(
            situation_summary=source_note,
            client_intent="Определено эвристически; нужна проверка человеком.",
            risk=risk,
            recommended_tone=recommended_tone,
            tone_reason="Эвристика fallback выбрала стиль по риску и цели пользователя.",
            recommended_strategy="Зафиксировать рамки, оплату и следующий конкретный шаг." + profile_hint + comment_hint + style_hint,
            do_not_do=[
                "Не соглашаться на новый объем без фиксации условий.",
                "Не отвечать эмоционально.",
                "Не обещать сроки без подтверждения оплаты и состава работ.",
            ],
            best_reply=best,
            soft_reply="Понял. Давайте зафиксируем текущий статус, что именно входит в этот этап, и после этого я смогу назвать срок по следующему шагу.",
            hard_reply="Готов продолжить после фиксации объема и оплаты текущего этапа. Новые задачи предлагаю вынести в отдельную оценку.",
            confidence=0.35 if analysis_text and not analysis_text.startswith("OCR недоступен") else 0.1,
        )

    @staticmethod
    def _make_client(api_key: str | None):
        if not api_key:
            return None
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Пакет openai не установлен. Для deprecated OpenAI fallback выполни: pip install -r requirements.txt"
            ) from exc
        return OpenAI(api_key=api_key)

def _matching_lines(text: str, needles: list[str]) -> list[str]:
    lines = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(needle in lowered for needle in needles):
            lines.append(line.strip())
    return lines


def _messages_from_user(chat_name: str, messages: list[dict]) -> list[dict]:
    senders = sorted({str(message.get("sender", "")).strip() for message in messages if message.get("sender")})
    if len(senders) == 2:
        chat_words = set(chat_name.lower().replace("_", " ").split())
        other_senders = [
            sender
            for sender in senders
            if not chat_words.intersection(sender.lower().replace("_", " ").split())
            and sender.lower() not in chat_name.lower()
        ]
        if len(other_senders) == 1:
            own_sender = other_senders[0]
            return [message for message in messages if str(message.get("sender", "")).strip() == own_sender]

    return messages


def _heuristic_user_style(messages: list[dict], previous_style: str = "") -> str:
    texts = [str(message.get("text", "")).strip() for message in messages if message.get("text")]
    if not texts:
        return previous_style
    avg_len = sum(len(text) for text in texts) / max(1, len(texts))
    has_emoji = any(any(ord(char) > 10_000 for char in text) for text in texts)
    has_questions = sum("?" in text for text in texts)
    humor_markers = [
        "лол",
        "ахах",
        "ха",
        "кек",
        "ну да",
        "конечно",
        "гениально",
        "прекрасно",
        "шикарно",
        "смешно",
    ]
    joined_text = "\n".join(texts).lower()
    has_humor = any(marker in joined_text for marker in humor_markers)
    length_note = "короткими сообщениями" if avg_len < 90 else "развернутыми сообщениями"
    question_note = "часто задает уточняющие вопросы" if has_questions >= max(2, len(texts) // 8) else "редко перегружает ответ вопросами"
    emoji_note = "можно сохранять легкие эмодзи, если они уместны" if has_emoji else "без эмодзи"
    humor_note = (
        "Допустим легкий сарказм, ирония и сухой юмор, но без токсичности и без усиления конфликта"
        if has_humor
        else "Юмор использовать осторожно, только если пользователь явно просит сделать ответ живее"
    )
    base = (
        f"Писать {length_note}, простым разговорным русским языком, {emoji_note}; "
        f"{question_note}; {humor_note}; сохранять естественную прямоту без канцелярита. "
        "Не задавать здесь мягкость, осторожность или переговорную стратегию: это выбирается отдельным тоном."
    )
    if previous_style and previous_style not in base:
        return f"{previous_style}\nОбновление: {base}"[:1600]
    return base[:1600]


def _infer_risk(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["оплат", "деньг", "потом", "после"]):
        return "Возможное затягивание оплаты или размытая договоренность по деньгам."
    if any(word in lowered for word in ["срочно", "быстро", "сегодня", "надо"]):
        return "Давление сроками; риск согласиться на срочность без условий."
    if any(word in lowered for word in ["ещё", "еще", "добав", "правк"]):
        return "Риск расползания объема работ."
    return "Явных рисков OCR fallback не выявил; проверьте контекст вручную."


def _draft_reply(risk: str, tone: str, user_comment: str = "") -> str:
    if user_comment:
        return (
            "Понял. С учетом моей цели предлагаю зафиксировать условия так: "
            f"{user_comment}. Давайте договоримся о следующем шаге без расширения объема и разночтений."
        )
    if tone == "короткий":
        return "Ок, давайте зафиксируем объем, оплату и следующий шаг, чтобы двигаться без разночтений."
    if tone == "стратегичный":
        return (
            "Давайте сделаем так, чтобы это не расползлось и у вас быстро был понятный результат: "
            "я могу взять следующий шаг, но сначала зафиксируем, что именно входит в этот заход и чем вы его подтверждаете. "
            "Так мы не теряем время на лишние круги, а я сразу держу фокус на том, что для вас критично."
        )
    if tone == "жёсткий":
        return "Готов продолжить после фиксации условий: что входит в текущий этап, что считается новым объемом и когда закрываем оплату."
    if "оплат" in risk.lower():
        return "Понял. Чтобы двигаться дальше без путаницы, предлагаю сначала закрыть оплату текущего этапа, а затем я возьму следующий блок в работу."
    return "Понял. Давайте коротко зафиксируем, что именно нужно сделать сейчас, что выходит за текущий объем, и какой следующий шаг подтверждаем."


def _infer_recommended_tone(
    risk: str,
    user_comment: str = "",
    context: str = "",
    user_style_profile: str = "",
) -> str:
    text = f"{risk}\n{user_comment}\n{context}".lower()
    personal_context = any(
        word in text
        for word in ["сестр", "брат", "мама", "мать", "папа", "отец", "родствен", "семь"]
    )
    hard_requested = any(word in text for word in ["жестко", "жёстко", "жесткий", "жёсткий", "строго"])
    severe_pressure = any(word in text for word in ["угроз", "шантаж", "оскорб", "ультимат", "агресс"])
    strategic_requested = any(
        word in text
        for word in [
            "выгод",
            "хитр",
            "манипуляц",
            "маккиавел",
            "макиавел",
            "рычаг",
            "стратег",
            "уступк",
            "мягк",
            "сил",
            "обыгр",
            "эмоц",
            "контрол",
        ]
    )
    if strategic_requested:
        return "стратегичный"
    if personal_context and not hard_requested and not severe_pressure:
        return "спокойный"
    if any(word in text for word in ["предоплат", "оплат", "границ", "правк", "располз", "жест", "жёст"]):
        return "жёсткий"
    if any(word in text for word in ["мягк", "спокой", "эскалац", "конфликт", "обид"]):
        return "спокойный"
    if user_style_profile:
        return "мой стиль"
    return "деловой"
