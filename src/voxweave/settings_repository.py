from __future__ import annotations

import json
from typing import Any

from .database import Database, utc_now


class SettingsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def record(
        self,
        revision: int,
        changed_fields: list[str],
        settings: dict[str, Any],
    ) -> None:
        self.database.execute(
            "INSERT INTO settings_events(revision,changed_fields_json,settings_json,created_at) "
            "VALUES(?,?,?,?) ON CONFLICT(revision) DO NOTHING",
            (
                revision,
                json.dumps(changed_fields, ensure_ascii=False),
                json.dumps(settings, ensure_ascii=False),
                utc_now(),
            ),
        )

    def events(self, after_revision: int, limit: int) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            "SELECT * FROM settings_events WHERE revision>? ORDER BY revision LIMIT ?",
            (after_revision, limit),
        )
        return [
            Database.decode_json_row(row, ("changed_fields_json", "settings_json"))
            for row in rows
        ]
