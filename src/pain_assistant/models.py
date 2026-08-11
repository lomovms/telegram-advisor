from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ClientProfile:
    id: int | None
    chat_name: str
    agreements: str = ""
    payment_promises: str = ""
    disputed_points: str = ""
    behavior_patterns: str = ""
    communication_style: str = ""
    tone_recommendations: str = ""


@dataclass
class AdvisorResult:
    situation_summary: str = ""
    client_intent: str = ""
    risk: str = ""
    recommended_tone: str = ""
    tone_reason: str = ""
    recommended_strategy: str = ""
    do_not_do: list[str] = field(default_factory=list)
    best_reply: str = ""
    soft_reply: str = ""
    hard_reply: str = ""
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> "AdvisorResult":
        do_not_do = data.get("do_not_do") or []
        if isinstance(do_not_do, str):
            do_not_do = [do_not_do]
        return cls(
            situation_summary=str(data.get("situation_summary", "")),
            client_intent=str(data.get("client_intent", "")),
            risk=str(data.get("risk", "")),
            recommended_tone=_normalize_tone(data.get("recommended_tone", "")),
            tone_reason=str(data.get("tone_reason", "")),
            recommended_strategy=str(data.get("recommended_strategy", "")),
            do_not_do=[str(item) for item in do_not_do],
            best_reply=_clean_reply_text(data.get("best_reply", "")),
            soft_reply=_clean_reply_text(data.get("soft_reply", "")),
            hard_reply=_clean_reply_text(data.get("hard_reply", "")),
            confidence=_safe_float(data.get("confidence", 0.0)),
        )

    def effective_recommended_tone(self) -> str:
        if self.recommended_tone:
            return self.recommended_tone
        text = f"{self.risk}\n{self.recommended_strategy}\n{self.client_intent}".lower()
        if any(word in text for word in ["выгод", "рычаг", "маневр", "манёвр", "хитр", "стратег", "уступк", "асимметр"]):
            return "стратегичный"
        if any(word in text for word in ["манипуляц", "давлен", "без оплат", "предоплат", "границ", "располз"]):
            return "жёсткий"
        if any(word in text for word in ["эскалац", "эмоцион", "конфликт", "извин", "сглад"]):
            return "спокойный"
        return "деловой"

    def reply_for_tone(self, tone: str) -> str:
        mapping = {
            "мой стиль": self.best_reply,
            "стратегичный": self.best_reply,
            "спокойный": self.soft_reply or self.best_reply,
            "деловой": self.best_reply,
            "жёсткий": self.hard_reply or self.best_reply,
            "короткий": self.best_reply,
        }
        return mapping.get(tone, self.best_reply)


def _safe_float(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _clean_reply_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    patterns = [
        r"^.*?\b(?:я бы написал(?:а)? так|можно написать так|лучше написать так|лучше ответить так|ответил(?:а)? бы так)\s*[:—-]\s*",
        r"^.*?\b(?:черновик|вариант ответа|текст ответа)\s*[:—-]\s*",
        r"^.*?\b(?:если .*? не ответит|если .*? промолчит).*?\b(?:написал(?:а)?|напиши|можно написать)\s*[:—-]\s*",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", text, flags=re.IGNORECASE)
        if cleaned != text:
            text = cleaned.strip()
            break
    return text.strip(" \"“”")


def _normalize_tone(value: object) -> str:
    text = str(value or "").strip().lower().replace("ё", "ё")
    aliases = {
        "soft": "спокойный",
        "my style": "мой стиль",
        "personal": "мой стиль",
        "user_style": "мой стиль",
        "мой стиль": "мой стиль",
        "в моем стиле": "мой стиль",
        "в моём стиле": "мой стиль",
        "strategic": "стратегичный",
        "strategy": "стратегичный",
        "machiavellian": "стратегичный",
        "стратегичный": "стратегичный",
        "стратегический": "стратегичный",
        "маккиавелист": "стратегичный",
        "макиавелист": "стратегичный",
        "манипулятивный": "стратегичный",
        "хитрый": "стратегичный",
        "выгода": "стратегичный",
        "calm": "спокойный",
        "мягкий": "спокойный",
        "спокойный": "спокойный",
        "neutral": "деловой",
        "business": "деловой",
        "деловой": "деловой",
        "нейтральный": "деловой",
        "hard": "жёсткий",
        "firm": "жёсткий",
        "жесткий": "жёсткий",
        "жёсткий": "жёсткий",
        "short": "короткий",
        "brief": "короткий",
        "короткий": "короткий",
    }
    return aliases.get(text, "")
