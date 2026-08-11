from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import ClientProfile
from .migrations import apply_migrations


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS client_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_name TEXT NOT NULL,
                    agreements TEXT NOT NULL DEFAULT '',
                    payment_promises TEXT NOT NULL DEFAULT '',
                    disputed_points TEXT NOT NULL DEFAULT '',
                    behavior_patterns TEXT NOT NULL DEFAULT '',
                    communication_style TEXT NOT NULL DEFAULT '',
                    tone_recommendations TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS imported_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL,
                    telegram_message_id INTEGER,
                    message_date TEXT,
                    sender TEXT,
                    out INTEGER NOT NULL DEFAULT 0,
                    delivery_status TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    media_path TEXT NOT NULL DEFAULT '',
                    media_type TEXT NOT NULL DEFAULT '',
                    reply_to_telegram_message_id INTEGER,
                    FOREIGN KEY(profile_id) REFERENCES client_profiles(id)
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER,
                    title TEXT NOT NULL,
                    price INTEGER NOT NULL DEFAULT 0,
                    paid_amount INTEGER NOT NULL DEFAULT 0,
                    payable INTEGER NOT NULL DEFAULT 0,
                    paid_out INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'planned',
                    deadline TEXT NOT NULL DEFAULT '',
                    paid_at TEXT NOT NULL DEFAULT '',
                    paid_out_at TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(profile_id) REFERENCES client_profiles(id)
                );

                CREATE TABLE IF NOT EXISTS crm_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    contact_name TEXT NOT NULL,
                    email TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'reply',
                    category TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    next_action TEXT NOT NULL DEFAULT '',
                    last_message_date TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, external_id)
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(imported_messages)").fetchall()
            }
            if "telegram_message_id" not in columns:
                conn.execute("ALTER TABLE imported_messages ADD COLUMN telegram_message_id INTEGER")
            if "out" not in columns:
                conn.execute("ALTER TABLE imported_messages ADD COLUMN out INTEGER NOT NULL DEFAULT 0")
            if "delivery_status" not in columns:
                conn.execute("ALTER TABLE imported_messages ADD COLUMN delivery_status TEXT NOT NULL DEFAULT ''")
            if "media_path" not in columns:
                conn.execute("ALTER TABLE imported_messages ADD COLUMN media_path TEXT NOT NULL DEFAULT ''")
            if "media_type" not in columns:
                conn.execute("ALTER TABLE imported_messages ADD COLUMN media_type TEXT NOT NULL DEFAULT ''")
            if "reply_to_telegram_message_id" not in columns:
                conn.execute("ALTER TABLE imported_messages ADD COLUMN reply_to_telegram_message_id INTEGER")
            project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
            if "payable" not in project_columns:
                conn.execute("ALTER TABLE projects ADD COLUMN payable INTEGER NOT NULL DEFAULT 0")
            if "paid_out" not in project_columns:
                conn.execute("ALTER TABLE projects ADD COLUMN paid_out INTEGER NOT NULL DEFAULT 0")
            if "paid_out_at" not in project_columns:
                conn.execute("ALTER TABLE projects ADD COLUMN paid_out_at TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_imported_messages_profile_telegram_id
                ON imported_messages(profile_id, telegram_message_id)
                WHERE telegram_message_id IS NOT NULL
                """
            )
            apply_migrations(conn)

    def save_profile(self, profile: ClientProfile) -> int:
        with self.connect() as conn:
            if profile.id is None:
                cur = conn.execute(
                    """
                    INSERT INTO client_profiles (
                        chat_name, agreements, payment_promises, disputed_points,
                        behavior_patterns, communication_style, tone_recommendations
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.chat_name,
                        profile.agreements,
                        profile.payment_promises,
                        profile.disputed_points,
                        profile.behavior_patterns,
                        profile.communication_style,
                        profile.tone_recommendations,
                    ),
                )
                return int(cur.lastrowid)

            conn.execute(
                """
                UPDATE client_profiles
                SET chat_name = ?, agreements = ?, payment_promises = ?,
                    disputed_points = ?, behavior_patterns = ?,
                    communication_style = ?, tone_recommendations = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    profile.chat_name,
                    profile.agreements,
                    profile.payment_promises,
                    profile.disputed_points,
                    profile.behavior_patterns,
                    profile.communication_style,
                    profile.tone_recommendations,
                    profile.id,
                ),
            )
            return profile.id

    def save_messages(self, profile_id: int, messages: list[dict]) -> None:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO imported_messages (
                    profile_id, telegram_message_id, message_date, sender, out, delivery_status,
                    text, media_path, media_type, reply_to_telegram_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _message_rows(profile_id, messages),
            )
            conn.executemany(
                """
                UPDATE imported_messages
                SET media_path = ?, media_type = ?
                WHERE profile_id = ? AND telegram_message_id = ?
                """,
                [
                    (row[7], row[8], row[0], row[1])
                    for row in _message_rows(profile_id, messages)
                    if row[1] is not None and row[7]
                ],
            )

    def delete_profile(self, profile_id: int) -> None:
        setting_keys = [
            f"telegram_peer:{profile_id}",
            f"telegram_account:{profile_id}",
            f"telegram_avatar:{profile_id}",
            f"autopilot_enabled:{profile_id}",
            f"autopilot_delay_seconds:{profile_id}",
            f"autopilot_last_handled:{profile_id}",
            f"contact_kind:{profile_id}",
            f"notification_last_seen:{profile_id}",
            f"ui_last_seen_message_id:{profile_id}",
            f"last_analysis:{profile_id}",
            f"ai_read_message_id:{profile_id}",
            f"analysis_context_cursor:{profile_id}",
            f"analysis_context_summary:{profile_id}",
            f"full_history_imported:{profile_id}",
        ]
        with self.connect() as conn:
            conn.execute("DELETE FROM imported_messages WHERE profile_id = ?", (profile_id,))
            conn.execute("UPDATE projects SET profile_id = NULL WHERE profile_id = ?", (profile_id,))
            conn.execute("DELETE FROM client_profiles WHERE id = ?", (profile_id,))
            conn.executemany("DELETE FROM app_settings WHERE key = ?", [(key,) for key in setting_keys])

    def list_projects(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT projects.id, projects.profile_id, projects.title, projects.price,
                       projects.paid_amount, projects.payable, projects.paid_out,
                       projects.status, projects.deadline, projects.paid_at, projects.paid_out_at, projects.notes, projects.created_at,
                       projects.updated_at, COALESCE(client_profiles.chat_name, '') AS client_name
                FROM projects
                LEFT JOIN client_profiles ON client_profiles.id = projects.profile_id
                ORDER BY CASE projects.status WHEN 'done' THEN 1 ELSE 0 END,
                         CASE WHEN projects.deadline = '' THEN 1 ELSE 0 END,
                         projects.deadline ASC, projects.updated_at DESC, projects.id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_projects_for_profile(self, profile_id: int, limit: int = 3) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, profile_id, title, status, deadline, notes, updated_at
                FROM projects
                WHERE profile_id = ?
                ORDER BY CASE status WHEN 'done' THEN 1 WHEN 'completed_by_me' THEN 1 ELSE 0 END,
                         updated_at DESC, id DESC
                LIMIT ?
                """,
                (profile_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_project_completed_by_me(self, project_id: int) -> dict | None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE projects SET status = 'completed_by_me', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project_id,),
            )
            row = conn.execute("SELECT id, profile_id, title, status, deadline, notes, updated_at FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row) if row else None

    def save_project(self, project: dict) -> dict:
        values = (
            project.get("profile_id"), project["title"], project["price"], project["paid_amount"],
            project["payable"], project["paid_out"], project["status"], project["deadline"],
            project["paid_at"], project["paid_out_at"], project["notes"],
        )
        with self.connect() as conn:
            project_id = project.get("id")
            if project_id:
                conn.execute(
                    """
                    UPDATE projects SET profile_id = ?, title = ?, price = ?, paid_amount = ?, payable = ?, paid_out = ?,
                        status = ?, deadline = ?, paid_at = ?, paid_out_at = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (*values, project_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO projects (profile_id, title, price, paid_amount, payable, paid_out, status, deadline, paid_at, paid_out_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                project_id = int(cursor.lastrowid)
            row = conn.execute(
                """
                SELECT projects.*, COALESCE(client_profiles.chat_name, '') AS client_name
                FROM projects LEFT JOIN client_profiles ON client_profiles.id = projects.profile_id
                WHERE projects.id = ?
                """,
                (project_id,),
            ).fetchone()
        return dict(row)

    def delete_project(self, project_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def work_profile(self) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM work_profile WHERE id = 1").fetchone()
        return dict(row) if row else {
            "id": 1,
            "about": "",
            "skills": "",
            "base_rate": 0,
            "minimum_order": 0,
            "pricing_rules": "",
            "risk_rules": "",
            "style": "",
            "boundaries": "",
            "updated_at": "",
        }

    def save_work_profile(self, profile: dict) -> dict:
        values = (
            str(profile.get("about", "")).strip(),
            str(profile.get("skills", "")).strip(),
            max(0, int(profile.get("base_rate", 0) or 0)),
            max(0, int(profile.get("minimum_order", 0) or 0)),
            str(profile.get("pricing_rules", "")).strip(),
            str(profile.get("risk_rules", "")).strip(),
            str(profile.get("style", "")).strip(),
            str(profile.get("boundaries", "")).strip(),
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO work_profile (
                    id, about, skills, base_rate, minimum_order, pricing_rules,
                    risk_rules, style, boundaries, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    about = excluded.about, skills = excluded.skills,
                    base_rate = excluded.base_rate, minimum_order = excluded.minimum_order,
                    pricing_rules = excluded.pricing_rules, risk_rules = excluded.risk_rules,
                    style = excluded.style, boundaries = excluded.boundaries,
                    updated_at = CURRENT_TIMESTAMP
                """,
                values,
            )
            row = conn.execute("SELECT * FROM work_profile WHERE id = 1").fetchone()
        return dict(row)

    def contact_for_profile(self, profile_id: int) -> dict:
        with self.connect() as conn:
            return dict(self._ensure_contact_for_profile(conn, profile_id))

    def working_memory(self, profile_id: int) -> dict:
        with self.connect() as conn:
            contact = self._ensure_contact_for_profile(conn, profile_id)
            items = conn.execute(
                """
                SELECT id, contact_id, project_id, kind, category, title, details,
                       actor, status, amount, currency, due_at, certainty, confidence,
                       created_by, created_at, updated_at
                FROM memory_items
                WHERE contact_id = ?
                ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'waiting' THEN 1 ELSE 2 END,
                         CASE certainty WHEN 'CONFIRMED' THEN 0 WHEN 'INFERRED' THEN 1 ELSE 2 END,
                         updated_at DESC, id DESC
                """,
                (contact["id"],),
            ).fetchall()
            source_rows = conn.execute(
                """
                SELECT memory_item_sources.memory_item_id, communication_records.id,
                       communication_records.channel, communication_records.external_message_id,
                       communication_records.occurred_at, communication_records.sender,
                       communication_records.text, communication_records.locator_json,
                       memory_item_sources.source_excerpt
                FROM memory_item_sources
                JOIN communication_records
                  ON communication_records.id = memory_item_sources.communication_record_id
                JOIN memory_items ON memory_items.id = memory_item_sources.memory_item_id
                WHERE memory_items.contact_id = ?
                ORDER BY communication_records.occurred_at, communication_records.id
                """,
                (contact["id"],),
            ).fetchall()
            run = conn.execute(
                """
                SELECT id, status, cursor_from, cursor_to, model, prompt_version, error,
                       created_at, updated_at
                FROM memory_extraction_runs
                WHERE contact_id = ? ORDER BY id DESC LIMIT 1
                """,
                (contact["id"],),
            ).fetchone()
        sources: dict[int, list[dict]] = {}
        for row in source_rows:
            source = dict(row)
            try:
                source["locator"] = json.loads(source.pop("locator_json") or "{}")
            except json.JSONDecodeError:
                source["locator"] = {}
            sources.setdefault(int(source.pop("memory_item_id")), []).append(source)
        payload_items = []
        for row in items:
            item = dict(row)
            item["sources"] = sources.get(int(item["id"]), [])
            payload_items.append(item)
        active_money = [item for item in payload_items if item["kind"] == "money_event" and item["status"] != "deleted"]
        totals = {
            "agreed": sum(int(item["amount"] or 0) for item in active_money if item["category"] == "agreed"),
            "received": sum(int(item["amount"] or 0) for item in active_money if item["category"] == "received"),
            "expected": sum(int(item["amount"] or 0) for item in active_money if item["category"] == "expected"),
            "payable": sum(int(item["amount"] or 0) for item in active_money if item["category"] == "payable"),
            "paid_out": sum(int(item["amount"] or 0) for item in active_money if item["category"] == "paid_out"),
        }
        totals["remaining"] = max(0, totals["agreed"] - totals["received"])
        return {
            "contact": dict(contact),
            "items": payload_items,
            "totals": totals,
            "last_run": dict(run) if run else None,
        }

    def ensure_telegram_communication_records(self, profile_id: int, messages: list[dict]) -> dict[str, dict]:
        with self.connect() as conn:
            contact = self._ensure_contact_for_profile(conn, profile_id)
            account, peer = self._telegram_identity(conn, profile_id)
            channel = conn.execute(
                """
                SELECT id FROM contact_channels
                WHERE contact_id = ? AND channel = 'telegram' AND account_key = ? AND external_contact_id = ?
                """,
                (contact["id"], account, peer),
            ).fetchone()
            records: dict[str, dict] = {}
            for message in messages:
                message_id = message.get("telegram_message_id")
                external_id = str(message_id if message_id is not None else f"row:{message.get('context_row_id', 0)}")
                locator = json.dumps(
                    {"profile_id": profile_id, "telegram_message_id": message_id},
                    ensure_ascii=False,
                )
                conn.execute(
                    """
                    INSERT INTO communication_records (
                        contact_id, contact_channel_id, channel, account_key, external_contact_id,
                        external_message_id, occurred_at, sender, direction, text, locator_json
                    ) VALUES (?, ?, 'telegram', ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(channel, account_key, external_contact_id, external_message_id)
                    DO UPDATE SET occurred_at = excluded.occurred_at, sender = excluded.sender,
                                  direction = excluded.direction, text = excluded.text,
                                  locator_json = excluded.locator_json
                    """,
                    (
                        contact["id"],
                        int(channel["id"]) if channel else None,
                        account,
                        peer,
                        external_id,
                        str(message.get("message_date") or message.get("date") or ""),
                        str(message.get("sender", "")),
                        "outgoing" if message.get("out") else "incoming",
                        str(message.get("text", "")),
                        locator,
                    ),
                )
                row = conn.execute(
                    """
                    SELECT id, text FROM communication_records
                    WHERE channel = 'telegram' AND account_key = ?
                      AND external_contact_id = ? AND external_message_id = ?
                    """,
                    (account, peer, external_id),
                ).fetchone()
                if row:
                    records[external_id] = dict(row)
            return records

    def start_memory_run(self, profile_id: int, cursor_from: int, cursor_to: int, model: str) -> int:
        contact = self.contact_for_profile(profile_id)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memory_extraction_runs (
                    contact_id, legacy_profile_id, cursor_from, cursor_to, model
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (contact["id"], profile_id, cursor_from, cursor_to, model),
            )
            return int(cursor.lastrowid)

    def finish_memory_run(self, run_id: int, status: str, error: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE memory_extraction_runs
                SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, error[:2000], run_id),
            )

    def apply_memory_analysis(
        self,
        profile_id: int,
        analysis: dict,
        source_records: dict[str, dict],
    ) -> list[int]:
        with self.connect() as conn:
            contact = self._ensure_contact_for_profile(conn, profile_id)
            conn.execute(
                """
                UPDATE contacts
                SET relationship_status = ?, context_summary = ?, next_action = ?,
                    next_contact_at = ?, last_analyzed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    str(analysis.get("relationship_status", "unknown"))[:80],
                    str(analysis.get("context_summary", "")).strip()[:4000],
                    str(analysis.get("next_action", "")).strip()[:1000],
                    str(analysis.get("next_contact_at", "")).strip()[:40],
                    contact["id"],
                ),
            )
            saved_ids: list[int] = []
            for raw in analysis.get("items", []):
                if not isinstance(raw, dict):
                    continue
                item = _normalize_memory_item(raw, created_by="AI")
                if not item:
                    continue
                conn.execute(
                    """
                    INSERT INTO memory_items (
                        contact_id, project_id, kind, category, title, details, actor,
                        status, amount, currency, due_at, certainty, confidence,
                        created_by, dedupe_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(contact_id, dedupe_key) DO UPDATE SET
                        project_id = excluded.project_id, category = excluded.category,
                        title = excluded.title, details = excluded.details, actor = excluded.actor,
                        status = excluded.status, amount = excluded.amount, currency = excluded.currency,
                        due_at = excluded.due_at, certainty = excluded.certainty,
                        confidence = excluded.confidence, updated_at = CURRENT_TIMESTAMP
                    """,
                    (contact["id"], *item),
                )
                row = conn.execute(
                    "SELECT id FROM memory_items WHERE contact_id = ? AND dedupe_key = ?",
                    (contact["id"], item[-1]),
                ).fetchone()
                if not row:
                    continue
                memory_item_id = int(row["id"])
                saved_ids.append(memory_item_id)
                for source_id in raw.get("source_message_ids", []):
                    source = source_records.get(str(source_id))
                    if not source:
                        continue
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO memory_item_sources (
                            memory_item_id, communication_record_id, source_excerpt
                        ) VALUES (?, ?, ?)
                        """,
                        (memory_item_id, int(source["id"]), str(source.get("text", ""))[:500]),
                    )
            return saved_ids

    def update_memory_item(self, item_id: int, raw: dict) -> dict | None:
        allowed = {
            "category", "title", "details", "actor", "status", "amount",
            "currency", "due_at", "certainty", "confidence",
        }
        updates = {key: raw[key] for key in allowed if key in raw}
        if not updates:
            return self.memory_item(item_id)
        if "certainty" in updates:
            updates["certainty"] = _certainty(updates["certainty"])
        if "confidence" in updates:
            updates["confidence"] = _confidence(updates["confidence"])
        if "amount" in updates:
            updates["amount"] = max(0, int(updates["amount"] or 0))
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE memory_items SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*updates.values(), item_id),
            )
        return self.memory_item(item_id)

    def memory_item(self, item_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def delete_memory_item(self, item_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,))

    def list_crm_leads(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, source, external_id, contact_name, email, status, category, summary,
                       next_action, last_message_date, created_at, updated_at
                FROM crm_leads
                ORDER BY CASE status WHEN 'reply' THEN 0 WHEN 'interested' THEN 1 ELSE 2 END,
                         updated_at DESC, id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def crm_lead(self, source: str, external_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM crm_leads WHERE source = ? AND external_id = ?",
                (source, external_id),
            ).fetchone()
        return dict(row) if row else None

    def save_crm_lead(self, lead: dict) -> dict:
        values = (
            lead["source"], lead["external_id"], lead["contact_name"], lead["email"], lead["status"],
            lead["category"], lead["summary"], lead["next_action"], lead["last_message_date"],
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO crm_leads (
                    source, external_id, contact_name, email, status, category, summary, next_action, last_message_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                    contact_name = excluded.contact_name, email = excluded.email, status = excluded.status,
                    category = excluded.category, summary = excluded.summary, next_action = excluded.next_action,
                    last_message_date = excluded.last_message_date, updated_at = CURRENT_TIMESTAMP
                """,
                values,
            )
            row = conn.execute(
                "SELECT * FROM crm_leads WHERE source = ? AND external_id = ?",
                (lead["source"], lead["external_id"]),
            ).fetchone()
        return dict(row)

    def project_by_profile_and_title(self, profile_id: int, title: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE profile_id = ? AND title = ? ORDER BY id DESC LIMIT 1",
                (profile_id, title),
            ).fetchone()
        return dict(row) if row else None

    def list_profiles(self) -> list[ClientProfile]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, chat_name, agreements, payment_promises, disputed_points,
                       behavior_patterns, communication_style, tone_recommendations
                FROM client_profiles
                ORDER BY COALESCE(
                    (
                        SELECT MAX(message_date)
                        FROM imported_messages
                        WHERE imported_messages.profile_id = client_profiles.id
                    ),
                    updated_at
                ) DESC, id DESC
                """
            ).fetchall()
        return [self._row_to_profile(row) for row in rows]

    def get_profile(self, profile_id: int) -> ClientProfile | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, chat_name, agreements, payment_promises, disputed_points,
                       behavior_patterns, communication_style, tone_recommendations
                FROM client_profiles
                WHERE id = ?
                """,
                (profile_id,),
            ).fetchone()
        return self._row_to_profile(row) if row else None

    def get_profile_by_chat_name(self, chat_name: str) -> ClientProfile | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, chat_name, agreements, payment_promises, disputed_points,
                       behavior_patterns, communication_style, tone_recommendations
                FROM client_profiles
                WHERE chat_name = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (chat_name,),
            ).fetchone()
        return self._row_to_profile(row) if row else None

    def recent_messages(self, profile_id: int, limit: int = 80) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                    SELECT telegram_message_id, message_date, sender, out, delivery_status,
                       text, media_path, media_type, reply_to_telegram_message_id
                FROM imported_messages
                WHERE profile_id = ?
                ORDER BY COALESCE(telegram_message_id, id) DESC
                LIMIT ?
                """,
                (profile_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def all_messages(self, profile_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_message_id, message_date, sender, out, delivery_status,
                       text, media_path, media_type, reply_to_telegram_message_id
                FROM imported_messages WHERE profile_id = ?
                ORDER BY COALESCE(telegram_message_id, id) ASC
                """,
                (profile_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def older_messages(self, profile_id: int, before_message_id: int, limit: int = 100) -> tuple[list[dict], bool]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_message_id, message_date, sender, out, delivery_status,
                       text, media_path, media_type, reply_to_telegram_message_id
                FROM imported_messages
                WHERE profile_id = ? AND telegram_message_id < ?
                ORDER BY telegram_message_id DESC LIMIT ?
                """,
                (profile_id, before_message_id, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        return [dict(row) for row in reversed(rows[:limit])], has_more

    def messages_around(self, profile_id: int, telegram_message_id: int, radius: int = 40) -> list[dict]:
        with self.connect() as conn:
            before = conn.execute(
                """
                SELECT telegram_message_id, message_date, sender, out, delivery_status,
                       text, media_path, media_type, reply_to_telegram_message_id
                FROM imported_messages
                WHERE profile_id = ? AND telegram_message_id <= ?
                ORDER BY telegram_message_id DESC LIMIT ?
                """,
                (profile_id, telegram_message_id, max(1, radius)),
            ).fetchall()
            after = conn.execute(
                """
                SELECT telegram_message_id, message_date, sender, out, delivery_status,
                       text, media_path, media_type, reply_to_telegram_message_id
                FROM imported_messages
                WHERE profile_id = ? AND telegram_message_id > ?
                ORDER BY telegram_message_id ASC LIMIT ?
                """,
                (profile_id, telegram_message_id, max(1, radius)),
            ).fetchall()
        return [dict(row) for row in reversed(before)] + [dict(row) for row in after]

    def has_messages_before(self, profile_id: int, before_message_id: int | None) -> bool:
        if before_message_id is None:
            return False
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM imported_messages WHERE profile_id = ? AND telegram_message_id < ? LIMIT 1",
                (profile_id, before_message_id),
            ).fetchone()
        return bool(row)

    def analysis_context_messages(
        self,
        profile_id: int,
        after_row_id: int,
        initial_limit: int = 100,
        overlap_limit: int = 8,
    ) -> tuple[list[dict], int]:
        with self.connect() as conn:
            cursor_row = conn.execute(
                "SELECT MAX(id) AS max_id FROM imported_messages WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
            current_row_id = int(cursor_row["max_id"] or 0) if cursor_row else 0

            if after_row_id <= 0 or after_row_id > current_row_id:
                rows = conn.execute(
                    """
                    SELECT id AS context_row_id, telegram_message_id, message_date, sender,
                           out, delivery_status, text, media_path, media_type,
                           reply_to_telegram_message_id
                    FROM imported_messages
                    WHERE profile_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (profile_id, max(1, initial_limit)),
                ).fetchall()
                return [dict(row) for row in reversed(rows)], current_row_id

            overlap_rows = conn.execute(
                """
                SELECT id AS context_row_id, telegram_message_id, message_date, sender,
                       out, delivery_status, text, media_path, media_type,
                       reply_to_telegram_message_id
                FROM imported_messages
                WHERE profile_id = ? AND id <= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (profile_id, after_row_id, max(0, overlap_limit)),
            ).fetchall()
            new_rows = conn.execute(
                """
                SELECT id AS context_row_id, telegram_message_id, message_date, sender,
                       out, delivery_status, text, media_path, media_type,
                       reply_to_telegram_message_id
                FROM imported_messages
                WHERE profile_id = ? AND id > ?
                ORDER BY id ASC
                """,
                (profile_id, after_row_id),
            ).fetchall()
        rows = list(reversed(overlap_rows)) + list(new_rows)
        return [dict(row) for row in rows], current_row_id

    def replace_messages(self, profile_id: int, messages: list[dict]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM imported_messages WHERE profile_id = ?", (profile_id,))
            conn.executemany(
                """
                INSERT OR IGNORE INTO imported_messages (
                    profile_id, telegram_message_id, message_date, sender, out, delivery_status,
                    text, media_path, media_type, reply_to_telegram_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _message_rows(profile_id, messages),
            )

    def message_media_path(self, profile_id: int, telegram_message_id: int) -> str:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT media_path
                FROM imported_messages
                WHERE profile_id = ? AND telegram_message_id = ?
                LIMIT 1
                """,
                (profile_id, telegram_message_id),
            ).fetchone()
        return str(row["media_path"] or "") if row else ""

    def mark_outgoing_messages_read(self, profile_id: int, max_message_id: int) -> None:
        if max_message_id <= 0:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE imported_messages
                SET delivery_status = 'read'
                WHERE profile_id = ?
                  AND out = 1
                  AND telegram_message_id IS NOT NULL
                  AND telegram_message_id <= ?
                """,
                (profile_id, max_message_id),
            )

    def max_telegram_message_id(self, profile_id: int) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(telegram_message_id) AS max_id
                FROM imported_messages
                WHERE profile_id = ?
                """,
                (profile_id,),
            ).fetchone()
        return int(row["max_id"] or 0) if row else 0

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )

    def _ensure_contact_for_profile(self, conn: sqlite3.Connection, profile_id: int) -> sqlite3.Row:
        profile = conn.execute(
            "SELECT id, chat_name FROM client_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if not profile:
            raise ValueError(f"Profile {profile_id} does not exist")
        conn.execute(
            """
            INSERT INTO contacts (legacy_profile_id, display_name)
            VALUES (?, ?)
            ON CONFLICT(legacy_profile_id) DO UPDATE SET
                display_name = excluded.display_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (profile_id, profile["chat_name"]),
        )
        contact = conn.execute(
            "SELECT * FROM contacts WHERE legacy_profile_id = ?",
            (profile_id,),
        ).fetchone()
        account, peer = self._telegram_identity(conn, profile_id)
        conn.execute(
            """
            INSERT INTO contact_channels (
                contact_id, channel, account_key, external_contact_id, display_label
            ) VALUES (?, 'telegram', ?, ?, ?)
            ON CONFLICT(channel, account_key, external_contact_id) DO UPDATE SET
                contact_id = excluded.contact_id, display_label = excluded.display_label,
                updated_at = CURRENT_TIMESTAMP
            """,
            (contact["id"], account, peer, account),
        )
        return contact

    @staticmethod
    def _telegram_identity(conn: sqlite3.Connection, profile_id: int) -> tuple[str, str]:
        account_row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (f"telegram_account:{profile_id}",),
        ).fetchone()
        peer_row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (f"telegram_peer:{profile_id}",),
        ).fetchone()
        account = str(account_row["value"] if account_row else "work")
        peer = str(peer_row["value"] if peer_row else f"profile:{profile_id}")
        return account, peer

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> ClientProfile:
        return ClientProfile(
            id=int(row["id"]),
            chat_name=row["chat_name"],
            agreements=row["agreements"],
            payment_promises=row["payment_promises"],
            disputed_points=row["disputed_points"],
            behavior_patterns=row["behavior_patterns"],
            communication_style=row["communication_style"],
            tone_recommendations=row["tone_recommendations"],
        )


def _delivery_status(message: dict) -> str:
    status = str(message.get("delivery_status", "") or "").strip().lower()
    if status in {"sent", "read"}:
        return status
    return "sent" if message.get("out") else ""


def _message_rows(profile_id: int, messages: list[dict]) -> list[tuple]:
    return [
        (
            profile_id,
            message.get("telegram_message_id"),
            message.get("date", ""),
            message.get("sender", ""),
            1 if message.get("out") else 0,
            _delivery_status(message),
            message.get("text", ""),
            message.get("media_path", ""),
            message.get("media_type", ""),
            message.get("reply_to_telegram_message_id"),
        )
        for message in messages
        if message.get("text") or message.get("media_path")
    ]


def _normalize_memory_item(raw: dict, created_by: str) -> tuple | None:
    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in {"task", "commitment", "money_event", "follow_up", "status", "note"}:
        return None
    title = re.sub(r"\s+", " ", str(raw.get("title", "")).strip())[:500]
    if not title:
        return None
    actor = str(raw.get("actor", "unknown")).strip().lower()
    if actor not in {"me", "them", "unknown"}:
        actor = "unknown"
    status = str(raw.get("status", "active")).strip().lower()[:40] or "active"
    category = str(raw.get("category", "")).strip().lower()[:60]
    try:
        amount = max(0, int(float(raw.get("amount", 0) or 0)))
    except (TypeError, ValueError):
        amount = 0
    currency = str(raw.get("currency", "RUB")).strip().upper()[:8] or "RUB"
    due_at = str(raw.get("due_at", "")).strip()[:40]
    certainty = _certainty(raw.get("certainty", "UNCERTAIN"))
    confidence = _confidence(raw.get("confidence", 0))
    details = str(raw.get("details", "")).strip()[:4000]
    project_id = raw.get("project_id")
    try:
        project_id = int(project_id) if project_id else None
    except (TypeError, ValueError):
        project_id = None
    normalized = "|".join(
        [kind, category, actor, re.sub(r"[^\w]+", " ", title.casefold()).strip(), str(amount), due_at]
    )
    dedupe_key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return (
        project_id,
        kind,
        category,
        title,
        details,
        actor,
        status,
        amount,
        currency,
        due_at,
        certainty,
        confidence,
        created_by,
        dedupe_key,
    )


def _certainty(value: object) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in {"CONFIRMED", "INFERRED", "UNCERTAIN"} else "UNCERTAIN"


def _confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
