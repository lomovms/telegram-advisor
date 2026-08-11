from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import ClientProfile


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
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
