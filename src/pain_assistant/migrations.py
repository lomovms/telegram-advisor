from __future__ import annotations

import sqlite3


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        int(row["version"])
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    if 1 not in applied:
        _working_memory_v1(conn)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")


def _working_memory_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS work_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            about TEXT NOT NULL DEFAULT '',
            skills TEXT NOT NULL DEFAULT '',
            base_rate INTEGER NOT NULL DEFAULT 0,
            minimum_order INTEGER NOT NULL DEFAULT 0,
            pricing_rules TEXT NOT NULL DEFAULT '',
            risk_rules TEXT NOT NULL DEFAULT '',
            style TEXT NOT NULL DEFAULT '',
            boundaries TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            legacy_profile_id INTEGER UNIQUE,
            display_name TEXT NOT NULL,
            relationship_status TEXT NOT NULL DEFAULT 'unknown',
            context_summary TEXT NOT NULL DEFAULT '',
            next_action TEXT NOT NULL DEFAULT '',
            next_contact_at TEXT NOT NULL DEFAULT '',
            last_analyzed_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(legacy_profile_id) REFERENCES client_profiles(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS contact_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            account_key TEXT NOT NULL DEFAULT '',
            external_contact_id TEXT NOT NULL,
            display_label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(contact_id) REFERENCES contacts(id) ON DELETE CASCADE,
            UNIQUE(channel, account_key, external_contact_id)
        );

        CREATE TABLE IF NOT EXISTS communication_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL,
            contact_channel_id INTEGER,
            channel TEXT NOT NULL,
            account_key TEXT NOT NULL DEFAULT '',
            external_contact_id TEXT NOT NULL,
            external_message_id TEXT NOT NULL,
            external_thread_id TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL DEFAULT '',
            sender TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL DEFAULT 'incoming',
            text TEXT NOT NULL DEFAULT '',
            locator_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(contact_id) REFERENCES contacts(id) ON DELETE CASCADE,
            FOREIGN KEY(contact_channel_id) REFERENCES contact_channels(id) ON DELETE SET NULL,
            UNIQUE(channel, account_key, external_contact_id, external_message_id)
        );

        CREATE TABLE IF NOT EXISTS memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL,
            project_id INTEGER,
            kind TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT 'unknown',
            status TEXT NOT NULL DEFAULT 'active',
            amount INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'RUB',
            due_at TEXT NOT NULL DEFAULT '',
            certainty TEXT NOT NULL DEFAULT 'UNCERTAIN',
            confidence REAL NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL DEFAULT 'AI',
            dedupe_key TEXT NOT NULL,
            superseded_by_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(contact_id) REFERENCES contacts(id) ON DELETE CASCADE,
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE SET NULL,
            FOREIGN KEY(superseded_by_id) REFERENCES memory_items(id) ON DELETE SET NULL,
            UNIQUE(contact_id, dedupe_key)
        );

        CREATE TABLE IF NOT EXISTS memory_item_sources (
            memory_item_id INTEGER NOT NULL,
            communication_record_id INTEGER NOT NULL,
            source_excerpt TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(memory_item_id, communication_record_id),
            FOREIGN KEY(memory_item_id) REFERENCES memory_items(id) ON DELETE CASCADE,
            FOREIGN KEY(communication_record_id) REFERENCES communication_records(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS memory_extraction_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL,
            legacy_profile_id INTEGER,
            status TEXT NOT NULL DEFAULT 'running',
            cursor_from INTEGER NOT NULL DEFAULT 0,
            cursor_to INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '',
            prompt_version TEXT NOT NULL DEFAULT 'working-memory-v1',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(contact_id) REFERENCES contacts(id) ON DELETE CASCADE,
            FOREIGN KEY(legacy_profile_id) REFERENCES client_profiles(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_contact_channels_contact ON contact_channels(contact_id);
        CREATE INDEX IF NOT EXISTS idx_communication_records_contact_date ON communication_records(contact_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_memory_items_contact_kind_status ON memory_items(contact_id, kind, status);
        CREATE INDEX IF NOT EXISTS idx_memory_runs_contact ON memory_extraction_runs(contact_id, id DESC);
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO work_profile (id, style)
        VALUES (1, COALESCE((SELECT value FROM app_settings WHERE key = 'user_style_profile'), ''))
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO contacts (legacy_profile_id, display_name)
        SELECT id, chat_name FROM client_profiles
        """
    )
    profiles = conn.execute("SELECT id FROM client_profiles").fetchall()
    for row in profiles:
        profile_id = int(row["id"])
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
        conn.execute(
            """
            INSERT OR IGNORE INTO contact_channels (
                contact_id, channel, account_key, external_contact_id, display_label
            )
            SELECT id, 'telegram', ?, ?, ? FROM contacts WHERE legacy_profile_id = ?
            """,
            (account, peer, account, profile_id),
        )
