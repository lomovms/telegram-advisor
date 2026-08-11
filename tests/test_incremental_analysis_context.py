from __future__ import annotations

import threading
import unittest
from datetime import datetime
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from pain_assistant.db import Database
from pain_assistant.calendar_context import business_calendar_context
from pain_assistant.models import AdvisorResult, ClientProfile
from pain_assistant.web_backend import AdvisorWebService, WebApiError, _normalize_analysis_model
from pain_assistant.gmail_client import (
    _append_contact_thread,
    _clean_message_text,
    _headers,
    _is_delivery_failure,
    _message_belongs_to_contact,
    _split_message_text,
    _thread_contacts,
    protect_secret,
    unprotect_secret,
)


class CapturingAi:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def analyze_screenshot(self, _image: bytes, _profile: ClientProfile, messages: list[dict], _tone: str, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return AdvisorResult(
            situation_summary="Клиент ждёт ответ.",
            client_intent="Получить подтверждение.",
            risk="Не потерять договорённость.",
            recommended_strategy="Ответить коротко.",
            do_not_do=["Не обещать лишнего."],
            best_reply="Да, подтверждаю.",
        )

    def rewrite_message(self, source_text: str, mode: str, messages: list[dict], **kwargs) -> str:
        self.calls.append({"source_text": source_text, "mode": mode, "messages": messages, **kwargs})
        return f"Переработано: {source_text}"


def make_service(db_path: Path, ai: CapturingAi) -> AdvisorWebService:
    service = AdvisorWebService.__new__(AdvisorWebService)
    service.db = Database(db_path)
    service.ai = ai
    service._analysis_lock = threading.Lock()
    return service


def messages(first_id: int, last_id: int) -> list[dict]:
    return [
        {
            "telegram_message_id": message_id,
            "date": f"2026-07-23T12:{message_id % 60:02d}:00+03:00",
            "sender": "Клиент" if message_id % 2 else "Я",
            "out": message_id % 2 == 0,
            "text": f"Сообщение {message_id}",
        }
        for message_id in range(first_id, last_id + 1)
    ]


class IncrementalAnalysisContextTests(unittest.TestCase):
    def test_calendar_marks_time_before_workday_start_as_non_working(self) -> None:
        context = business_calendar_context(
            workday_start_hour=9,
            workday_end_hour=19,
            now=datetime(2026, 8, 5, 3, 15),
        )
        self.assertIn("сейчас нерабочее время", context)
        self.assertIn("Рабочий день: с 09:00 до 19:00", context)

    def test_profile_stays_unread_until_marked_viewed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = make_service(Path(temp_dir) / "advisor.db", CapturingAi())
            profile_id = service.db.save_profile(ClientProfile(id=None, chat_name="Клиент"))
            service.db.save_messages(profile_id, messages(1, 3))
            profile = service.db.get_profile(profile_id)
            self.assertFalse(service._profile_payload(profile)["unread"])
            service.db.save_messages(profile_id, messages(4, 5))

            self.assertTrue(service._profile_payload(profile)["unread"])
            viewed = service.mark_profile_viewed(profile_id)

            self.assertFalse(viewed["unread"])
            self.assertEqual(viewed["ui_last_seen_message_id"], 5)

    def test_conversation_goal_is_scoped_to_profile(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = make_service(Path(temp_dir) / "advisor.db", CapturingAi())
            first = service.db.save_profile(ClientProfile(id=None, chat_name="Первый"))
            second = service.db.save_profile(ClientProfile(id=None, chat_name="Второй"))

            service.set_conversation_goal(first, "Зафиксировать оплату")

            self.assertEqual(service.conversation_goal(first), "Зафиксировать оплату")
            self.assertEqual(service.conversation_goal(second), "")

    def test_profiles_are_sorted_by_latest_message_time(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "advisor.db")
            older = db.save_profile(ClientProfile(id=None, chat_name="Старый чат"))
            newer = db.save_profile(ClientProfile(id=None, chat_name="Новый чат"))
            db.save_messages(older, [{"telegram_message_id": 20, "date": "2026-07-25T10:00:00+03:00", "text": "старое"}])
            db.save_messages(newer, [{"telegram_message_id": 10, "date": "2026-07-25T12:00:00+03:00", "text": "новое"}])

            self.assertEqual([profile.chat_name for profile in db.list_profiles()], ["Новый чат", "Старый чат"])

    def test_reply_target_is_persisted_with_message(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db = Database(Path(temp_dir) / "advisor.db")
            profile_id = db.save_profile(ClientProfile(id=None, chat_name="Клиент"))
            db.save_messages(profile_id, [{"telegram_message_id": 2, "date": "2026-07-25T12:00:00+03:00", "text": "ответ", "out": True, "reply_to_telegram_message_id": 1}])

            self.assertEqual(db.recent_messages(profile_id)[0]["reply_to_telegram_message_id"], 1)

    def test_rewrite_uses_compact_context_and_does_not_move_analysis_cursor(self) -> None:
        with TemporaryDirectory() as temp_dir:
            ai = CapturingAi()
            service = make_service(Path(temp_dir) / "advisor.db", ai)
            profile_id = service.db.save_profile(ClientProfile(id=None, chat_name="Клиент"))
            service.db.save_messages(profile_id, messages(1, 20))
            service.db.set_setting(f"analysis_context_summary:{profile_id}:topic:авто", "Сохранённое резюме")

            result = service.rewrite_message(profile_id, "  исходный текст  ", "short")

            self.assertEqual(result["text"], "Переработано: исходный текст")
            self.assertEqual([item["telegram_message_id"] for item in ai.calls[0]["messages"]], list(range(9, 21)))
            self.assertIn("Роль помощника:", ai.calls[0]["conversation_summary"])
            self.assertIn("Тема: Авто", ai.calls[0]["conversation_summary"])
            self.assertIn("Сохранённое резюме", ai.calls[0]["conversation_summary"])
            self.assertEqual(service.db.get_setting(f"analysis_context_cursor:{profile_id}"), "")
            with self.assertRaisesRegex(WebApiError, "Неизвестный режим"):
                service.rewrite_message(profile_id, "текст", "translate")

    def test_analysis_model_is_validated_and_persisted(self) -> None:
        class ModelAwareAi(CapturingAi):
            model = ""

            def set_model(self, model: str) -> None:
                self.model = model

        with TemporaryDirectory() as temp_dir:
            ai = ModelAwareAi()
            service = make_service(Path(temp_dir) / "advisor.db", ai)

            self.assertEqual(service.set_analysis_model("gpt-5.6-terra"), "gpt-5.6-terra")
            self.assertEqual(service.db.get_setting("analysis_model"), "gpt-5.6-terra")
            self.assertEqual(ai.model, "gpt-5.6-terra")
            with self.assertRaisesRegex(WebApiError, "неподдерживаемая"):
                service.set_analysis_model("unknown-model")
            self.assertEqual(_normalize_analysis_model(""), "gpt-5.6-luna")

    def test_gmail_reads_only_configured_label(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = make_service(Path(temp_dir) / "advisor.db", CapturingAi())

            self.assertFalse(service.gmail_status()["gmail_connected"])
            self.assertEqual(service.gmail_status()["gmail_label"], "Advisor")
            service.set_gmail_label("Клиенты")
            self.assertEqual(service.gmail_status()["gmail_label"], "Клиенты")
            protected = protect_secret("gmail-secret")
            self.assertNotIn("gmail-secret", protected)
            self.assertEqual(unprotect_secret(protected), "gmail-secret")

    def test_gmail_hides_delivery_failures_and_quoted_history(self) -> None:
        self.assertTrue(_is_delivery_failure({"from": "Mail Delivery Subsystem <mailer-daemon@gmail.com>"}))
        self.assertEqual(
            _clean_message_text("Понял, спасибо.\n\nOn Monday Client <client@example.com> wrote:\n> Старый текст"),
            "Понял, спасибо.",
        )
        main, technical = _split_message_text(
            "Спасибо, предложение получил.\n\n"
            "Кому: «Калуга ONLINE» (onlinekaluga@yandex.ru);\n"
            "Тема: Фронтенд-разработчик;\n\n"
            "--\nС уважением,\nВладислав\nDigital Agency"
        )
        self.assertEqual(main, "Спасибо, предложение получил.")
        self.assertIn("Кому: «Калуга ONLINE»", technical)

    def test_gmail_splits_mass_mail_replies_by_contact(self) -> None:
        def message(sender: str, to: str = "", bcc: str = "") -> dict:
            return {"payload": {"headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Bcc", "value": bcc},
            ]}}

        own_email = "me@example.com"
        initial = message(own_email, own_email, "Alpha <alpha@example.com>, Beta <beta@example.com>")
        alpha_reply = message("Alpha <alpha@example.com>", own_email)
        beta_reply = message("Beta <beta@example.com>", own_email)
        contacts = _thread_contacts([initial, alpha_reply, beta_reply], own_email)

        self.assertEqual(set(contacts), {"alpha@example.com", "beta@example.com"})
        self.assertTrue(_message_belongs_to_contact(_headers(initial), "alpha@example.com", own_email, 2))
        self.assertFalse(_message_belongs_to_contact(_headers(alpha_reply), "beta@example.com", own_email, 2))

    def test_gmail_contact_history_does_not_duplicate_threads(self) -> None:
        contact = {"thread_ids": [], "messages": []}
        detail = {
            "id": "thread-1",
            "messages": [{
                "id": "message-1",
                "internalDate": "1",
                "snippet": "История без ярлыка",
                "payload": {"headers": [
                    {"name": "From", "value": "client@example.com"},
                    {"name": "To", "value": "me@example.com"},
                ]},
            }],
        }

        _append_contact_thread(contact, detail, "client@example.com", "me@example.com", 1)
        _append_contact_thread(contact, detail, "client@example.com", "me@example.com", 1)

        self.assertEqual(contact["thread_ids"], ["thread-1"])
        self.assertEqual(len(contact["messages"]), 1)

    def test_gmail_uses_saved_contacts_without_network(self) -> None:
        with TemporaryDirectory() as temp_dir:
            service = make_service(Path(temp_dir) / "advisor.db", CapturingAi())
            service.db.set_setting("gmail_refresh_token", protect_secret("refresh"))
            service.db.set_setting("gmail_contacts_cache_label", "Advisor")
            service.db.set_setting("gmail_contacts_cache", '[{"id":"client@example.com","messages":[]}]')

            with patch("pain_assistant.web_backend.fetch_labeled_contacts", side_effect=AssertionError("network called")):
                payload = service.gmail_messages()

            self.assertTrue(payload["gmail_cached"])
            self.assertEqual(payload["contacts"][0]["id"], "client@example.com")

    def test_gmail_analysis_uses_saved_brief_and_clean_history(self) -> None:
        with TemporaryDirectory() as temp_dir:
            ai = CapturingAi()
            service = make_service(Path(temp_dir) / "advisor.db", ai)
            service.db.set_setting(
                "gmail_contacts_cache",
                '[{"id":"client@example.com","name":"Клиент","email":"client@example.com",'
                '"messages":[{"id":"m1","date":"2026-08-05","sender":"Клиент","out":false,'
                '"subject":"Новый сайт","text":"Сколько стоит разработка?"}]}]',
            )

            result = service.analyze_gmail("client@example.com", "деловой", "Уточнить объём работ")

            self.assertEqual(result["context"]["contact"], "Клиент <client@example.com>")
            self.assertEqual(service.db.get_setting("gmail_reply_brief"), "Уточнить объём работ")
            self.assertEqual(ai.calls[0]["messages"][0]["text"], "Сколько стоит разработка?")
            self.assertIn("Уточнить объём работ", ai.calls[0]["user_comment"])

            service.analyze_gmail("client@example.com", "деловой", "")
            self.assertIn("Ответить по сути последнего письма", ai.calls[1]["user_comment"])

    def test_subsequent_analysis_sends_overlap_and_only_new_messages(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "advisor.db"
            ai = CapturingAi()
            service = make_service(db_path, ai)
            profile_id = service.db.save_profile(ClientProfile(id=None, chat_name="Клиент"))
            service.db.save_messages(profile_id, messages(1, 25))

            service.analyze(profile_id, "мой стиль", "", message_limit=20)

            first_ids = [item["telegram_message_id"] for item in ai.calls[0]["messages"]]
            self.assertEqual(first_ids, list(range(6, 26)))
            self.assertIn("Ситуация: Клиент ждёт ответ.", service.db.get_setting(
                f"analysis_context_summary:{profile_id}:topic:авто"
            ))

            service.db.save_messages(profile_id, messages(26, 27))
            restarted_ai = CapturingAi()
            restarted_service = make_service(db_path, restarted_ai)
            restarted_service.analyze(profile_id, "мой стиль", "")

            second_call = restarted_ai.calls[0]
            second_ids = [item["telegram_message_id"] for item in second_call["messages"]]
            self.assertEqual(second_ids, list(range(18, 28)))
            self.assertIn("Стратегия: Ответить коротко.", second_call["previous_analysis_context"])

    def test_topic_switch_uses_separate_analysis_memory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            ai = CapturingAi()
            service = make_service(Path(temp_dir) / "advisor.db", ai)
            profile_id = service.db.save_profile(ClientProfile(id=None, chat_name="Клиент"))
            service.db.save_messages(profile_id, messages(1, 5))

            first_result = service.analyze(profile_id, "мой стиль", "")
            self.assertEqual(first_result["context"]["message_count"], 5)
            self.assertEqual(first_result["context"]["topic"], "Авто")
            service.set_active_topic(
                profile_id,
                "Редизайн сайта",
                "Макеты, адаптив и правки сайта; личные разговоры исключить.",
            )
            service.analyze(profile_id, "мой стиль", "")

            self.assertEqual(ai.calls[1]["previous_analysis_context"], "")
            self.assertIn("Редизайн сайта", ai.calls[1]["user_comment"])
            self.assertIn("Макеты, адаптив и правки", ai.calls[1]["user_comment"])
            self.assertTrue(service.db.get_setting(
                f"analysis_context_summary:{profile_id}:topic:авто"
            ))
            self.assertTrue(service.db.get_setting(
                f"analysis_context_summary:{profile_id}:topic:редизайн сайта"
            ))

    def test_context_cursor_is_saved_only_after_successful_analysis(self) -> None:
        class FailingAi(CapturingAi):
            def analyze_screenshot(self, *args, **kwargs):
                raise RuntimeError("AI unavailable")

        with TemporaryDirectory() as temp_dir:
            service = make_service(Path(temp_dir) / "advisor.db", FailingAi())
            profile_id = service.db.save_profile(ClientProfile(id=None, chat_name="Клиент"))
            service.db.save_messages(profile_id, messages(1, 3))

            with self.assertRaisesRegex(RuntimeError, "AI unavailable"):
                service.analyze(profile_id, "мой стиль", "")

            self.assertEqual(service.db.get_setting(f"analysis_context_cursor:{profile_id}"), "")


if __name__ == "__main__":
    unittest.main()
