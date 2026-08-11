from __future__ import annotations

from datetime import datetime, timedelta


DEFAULT_WEEKEND_POLICY = (
    "В субботу и воскресенье не обещать работу по обычной цене. "
    "Сообщить, что сегодня выходной, и предложить ближайший будний день. "
    "Если клиенту нужно именно сегодня, обозначить срочность и предложить повышенный тариф, "
    "не придумывая конкретную сумму или процент."
)

DEFAULT_AFTER_HOURS_POLICY = (
    "После окончания рабочего дня не обещать немедленное выполнение по обычной цене. "
    "Сообщить, что рабочий день уже закончился, и предложить ответ или выполнение на следующий рабочий период. "
    "Если клиенту нужно срочно сегодня, предложить повышенный тариф, не придумывая сумму или процент."
)

_RU_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


def business_calendar_context(
    weekend_policy: str = "",
    after_hours_policy: str = "",
    workday_start_hour: int = 9,
    workday_end_hour: int = 19,
    now: datetime | None = None,
) -> str:
    local_now = now or datetime.now().astimezone()
    weekend = local_now.weekday() >= 5
    start_hour = max(0, min(23, workday_start_hour))
    end_hour = max(0, min(23, workday_end_hour))
    outside_work_hours = local_now.hour < start_hour or local_now.hour >= end_hour
    next_business_day = local_now + timedelta(days=1) if local_now.hour >= end_hour else local_now
    while next_business_day.weekday() >= 5:
        next_business_day += timedelta(days=1)
    active_weekend_policy = weekend_policy.strip() or DEFAULT_WEEKEND_POLICY
    active_after_hours_policy = after_hours_policy.strip() or DEFAULT_AFTER_HOURS_POLICY
    return (
        f"Текущие локальные дата и время: {local_now:%d.%m.%Y %H:%M}; "
        f"день недели: {_RU_WEEKDAYS[local_now.weekday()]}; "
        f"сегодня {'выходной' if weekend else 'рабочий день'}; "
        f"сейчас {'нерабочее время' if outside_work_hours else 'рабочее время'}.\n"
        f"Рабочий день: с {start_hour:02d}:00 до {end_hour:02d}:00.\n"
        f"Ближайший будний день: {next_business_day:%d.%m.%Y}.\n"
        f"Правило работы в выходные: {active_weekend_policy}\n"
        f"Правило после окончания рабочего дня: {active_after_hours_policy}\n"
        "Применяй рабочие правила только когда собеседник просит работу, срок или срочное действие, "
        "а не в обычной беседе. Сообщения с локальным временем до начала или после окончания рабочего дня считаются "
        "пришедшими в нерабочее время, даже если ответ готовится позже. Учитывай даты сообщений: "
        "не называй старое сообщение сегодняшним."
    )
