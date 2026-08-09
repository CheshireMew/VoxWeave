from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()

    def migrate(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS models (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    family TEXT NOT NULL,
                    checkpoint_epoch INTEGER,
                    model_path TEXT NOT NULL,
                    model_sha256 TEXT NOT NULL,
                    index_path TEXT,
                    index_sha256 TEXT,
                    index_candidates_json TEXT NOT NULL,
                    rvc_version TEXT,
                    sample_rate INTEGER,
                    f0 INTEGER,
                    source_kind TEXT NOT NULL,
                    license_spdx TEXT,
                    source_url TEXT,
                    recommended_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS presets (
                    id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL REFERENCES models(id),
                    name TEXT NOT NULL,
                    model_sha256 TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(model_id, name)
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    stage TEXT,
                    result_json TEXT,
                    error_type TEXT,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    state TEXT NOT NULL,
                    progress REAL NOT NULL,
                    stage TEXT,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_rules (
                    id TEXT PRIMARY KEY,
                    input_root TEXT NOT NULL,
                    output_root TEXT NOT NULL,
                    model_selector TEXT NOT NULL,
                    preset_json TEXT NOT NULL,
                    preset_name TEXT NOT NULL DEFAULT 'default',
                    recursive INTEGER NOT NULL,
                    watch_enabled INTEGER NOT NULL,
                    extensions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_items (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batch_rules(id),
                    source_path TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    task_id TEXT REFERENCES tasks(id),
                    state TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(batch_id, source_path, source_size, source_mtime_ns)
                );
                """
            )
            batch_rule_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(batch_rules)").fetchall()
            }
            if "preset_name" not in batch_rule_columns:
                db.execute(
                    "ALTER TABLE batch_rules ADD COLUMN preset_name TEXT NOT NULL DEFAULT 'default'"
                )
            db.execute(
                "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )
            now = utc_now()
            db.execute(
                "UPDATE tasks SET state='interrupted', stage='service_restart', updated_at=? "
                "WHERE state IN ("
                "'running','analyzing','converting','muxing','validating',"
                "'download','environment','dependencies')",
                (now,),
            )

    def fetch_all(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, parameters).fetchall()]

    def fetch_one(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(sql, parameters).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        with self.connect() as db:
            db.execute(sql, parameters)

    @staticmethod
    def decode_json_row(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        result = dict(row)
        for field in fields:
            value = result.get(field)
            if value is not None:
                result[field.removesuffix("_json")] = json.loads(value)
                del result[field]
        return result
