from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 12

MODEL_RECOMMENDATION_DEFAULTS = {
    "pitch": 0,
    "f0": "rmvpe",
    "index_rate": 0.72,
    "rms_mix_rate": 0.25,
    "protect": 0.33,
    "content_mode": "clean",
}
FEMALE_MODEL_IDS = {
    "community.zh-female-keke",
    "community.zh-female-senior",
    "community.zh-female-yalin",
}
MALE_MODEL_IDS = {
    "community.zh-male-young",
    "community.zh-male-raspy",
    "community.zh-male-deep",
}
FEMALE_MODEL_FAMILIES = {
    "public_yujie_v2",
    "keruan_v1",
    "guaiguai_v2",
    "guanguan_v1",
    "jiazi_v2",
    "loli_2888",
    "tingbai_v1",
    "self_female_v1",
    "suara_wanita_2",
}


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
            db.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            stored = db.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            version = int(stored["value"]) if stored else 0
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {version} is newer than supported schema {SCHEMA_VERSION}"
                )
            existing_tables = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name!='metadata'"
                ).fetchall()
            }
            if version == 0 and not existing_tables:
                self._create_latest(db)
                self._set_schema_version(db, SCHEMA_VERSION)
            else:
                if version == 0:
                    version = 1
                migrations = {
                    2: self._migrate_to_2,
                    3: self._migrate_to_3,
                    4: self._migrate_to_4,
                    5: self._migrate_to_5,
                    6: self._migrate_to_6,
                    7: self._migrate_to_7,
                    8: self._migrate_to_8,
                    9: self._migrate_to_9,
                    10: self._migrate_to_10,
                    11: self._migrate_to_11,
                    12: self._migrate_to_12,
                }
                while version < SCHEMA_VERSION:
                    target = version + 1
                    migrations[target](db)
                    self._set_schema_version(db, target)
                    version = target
            self._validate_schema(db)

    @staticmethod
    def _set_schema_version(db: sqlite3.Connection, version: int) -> None:
        db.execute(
            "INSERT INTO metadata(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(version),),
        )

    @staticmethod
    def _columns(db: sqlite3.Connection, table: str) -> set[str]:
        return {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}

    @classmethod
    def _add_column(cls, db: sqlite3.Connection, table: str, definition: str) -> None:
        name = definition.split()[0]
        if name not in cls._columns(db, table):
            db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    @staticmethod
    def _create_latest(db: sqlite3.Connection) -> None:
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
                    request_id TEXT,
                    actor_json TEXT,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    retry_of TEXT REFERENCES tasks(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS tasks_request_id_unique
                    ON tasks(request_id) WHERE request_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS tasks_created_at_index ON tasks(created_at DESC,id);
                CREATE INDEX IF NOT EXISTS tasks_updated_at_index ON tasks(updated_at DESC,id);
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
                    model_id TEXT NOT NULL,
                    model_sha256 TEXT,
                    index_sha256 TEXT,
                    preset_json TEXT NOT NULL,
                    preset_name TEXT NOT NULL DEFAULT 'default',
                    recursive INTEGER NOT NULL,
                    watch_enabled INTEGER NOT NULL,
                    extensions_json TEXT NOT NULL,
                    last_error TEXT,
                    last_error_at TEXT,
                    state TEXT NOT NULL DEFAULT 'active',
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
                    UNIQUE(batch_id, source_path, source_sha256)
                );
                CREATE TABLE IF NOT EXISTS batch_item_history (
                    id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    task_id TEXT,
                    state TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    archive_reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_runs (
                    id TEXT PRIMARY KEY REFERENCES tasks(id),
                    batch_id TEXT NOT NULL REFERENCES batch_rules(id),
                    item_ids_json TEXT NOT NULL,
                    submission_failures_json TEXT NOT NULL DEFAULT '[]',
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifact_archives (
                    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
                    source_path TEXT NOT NULL,
                    archive_path TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS realtime_sessions (
                    id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL REFERENCES models(id),
                    model_sha256 TEXT NOT NULL,
                    index_sha256 TEXT,
                    arguments_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT,
                    metrics_json TEXT,
                    error_type TEXT,
                    error TEXT,
                    started_at TEXT,
                    stopped_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS realtime_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES realtime_sessions(id),
                    state TEXT NOT NULL,
                    stage TEXT,
                    detail TEXT,
                    metrics_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('active','archived','missing')),
                    archive_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS artifacts_task_index ON artifacts(task_id,state);
                """
        )

    @classmethod
    def _migrate_to_2(cls, db: sqlite3.Connection) -> None:
        cls._add_column(db, "batch_rules", "preset_name TEXT NOT NULL DEFAULT 'default'")

    @classmethod
    def _migrate_to_3(cls, db: sqlite3.Connection) -> None:
        cls._add_column(db, "batch_rules", "last_error TEXT")
        cls._add_column(db, "batch_rules", "last_error_at TEXT")

    @classmethod
    def _migrate_to_4(cls, db: sqlite3.Connection) -> None:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_item_history (
                id TEXT NOT NULL,batch_id TEXT NOT NULL,source_path TEXT NOT NULL,
                source_size INTEGER NOT NULL,source_mtime_ns INTEGER NOT NULL,
                source_sha256 TEXT NOT NULL,output_path TEXT NOT NULL,task_id TEXT,
                state TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                archived_at TEXT NOT NULL,archive_reason TEXT NOT NULL
            )
            """
        )
        schema = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='batch_items'"
        ).fetchone()
        normalized = "".join((schema["sql"] or "").lower().split())
        if "unique(batch_id,source_path,source_size,source_mtime_ns)" in normalized:
            db.execute("ALTER TABLE batch_items RENAME TO batch_items_legacy")
            db.execute(
                """
                    CREATE TABLE batch_items (
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
                        UNIQUE(batch_id, source_path, source_sha256)
                    )
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO batch_items SELECT * FROM batch_items_legacy "
                "ORDER BY updated_at DESC"
            )
            db.execute(
                "INSERT INTO batch_item_history SELECT legacy.*, ?, 'schema-v4-content-identity' "
                "FROM batch_items_legacy AS legacy "
                "WHERE NOT EXISTS (SELECT 1 FROM batch_items WHERE id=legacy.id)",
                (utc_now(),),
            )
            db.execute("DROP TABLE batch_items_legacy")

    @staticmethod
    def _migrate_to_5(db: sqlite3.Connection) -> None:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_archives (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id),source_path TEXT NOT NULL,
                archive_path TEXT NOT NULL UNIQUE,state TEXT NOT NULL,file_count INTEGER NOT NULL,
                size_bytes INTEGER NOT NULL,created_at TEXT NOT NULL,completed_at TEXT
            )
            """
        )

    @staticmethod
    def _migrate_to_6(db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS realtime_sessions (
                id TEXT PRIMARY KEY,model_id TEXT NOT NULL REFERENCES models(id),
                model_sha256 TEXT NOT NULL,index_sha256 TEXT,arguments_json TEXT NOT NULL,
                state TEXT NOT NULL,stage TEXT,metrics_json TEXT,error_type TEXT,error TEXT,
                started_at TEXT,stopped_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS realtime_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES realtime_sessions(id),state TEXT NOT NULL,
                stage TEXT,detail TEXT,metrics_json TEXT,created_at TEXT NOT NULL
            );
            """
        )

    @classmethod
    def _migrate_to_7(cls, db: sqlite3.Connection) -> None:
        cls._add_column(db, "tasks", "request_id TEXT")
        cls._add_column(db, "tasks", "actor_json TEXT")
        cls._add_column(db, "tasks", "retry_of TEXT REFERENCES tasks(id)")
        db.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS tasks_request_id_unique
                ON tasks(request_id) WHERE request_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS tasks_created_at_index ON tasks(created_at DESC,id);
            CREATE INDEX IF NOT EXISTS tasks_updated_at_index ON tasks(updated_at DESC,id);
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),kind TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('active','archived','missing')),
                archive_path TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS artifacts_task_index ON artifacts(task_id,state);
            """
        )

    @classmethod
    def _migrate_to_8(cls, db: sqlite3.Connection) -> None:
        rule_columns = cls._columns(db, "batch_rules")
        if "model_selector" in rule_columns:
            db.execute("ALTER TABLE batch_items RENAME TO batch_items_v7")
            db.execute("ALTER TABLE batch_rules RENAME TO batch_rules_v7")
            db.executescript(
                """
                CREATE TABLE batch_rules (
                    id TEXT PRIMARY KEY,input_root TEXT NOT NULL,output_root TEXT NOT NULL,
                    model_id TEXT NOT NULL,model_sha256 TEXT,index_sha256 TEXT,
                    preset_json TEXT NOT NULL,
                    preset_name TEXT NOT NULL DEFAULT 'default',recursive INTEGER NOT NULL,
                    watch_enabled INTEGER NOT NULL,extensions_json TEXT NOT NULL,last_error TEXT,
                    last_error_at TEXT,state TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL
                );
                CREATE TABLE batch_items (
                    id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES batch_rules(id),
                    source_path TEXT NOT NULL,source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,source_sha256 TEXT NOT NULL,
                    output_path TEXT NOT NULL,task_id TEXT REFERENCES tasks(id),state TEXT NOT NULL,
                    error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                    UNIQUE(batch_id,source_path,source_sha256)
                );
                """
            )
            models = db.execute(
                "SELECT id,display_name,aliases_json,model_sha256,index_sha256 FROM models"
            ).fetchall()
            selectors: dict[str, list[sqlite3.Row]] = {}
            for model in models:
                values = [model["id"], model["display_name"], *json.loads(model["aliases_json"])]
                for value in values:
                    selectors.setdefault(str(value).casefold(), []).append(model)
            for row in db.execute("SELECT * FROM batch_rules_v7").fetchall():
                matches = selectors.get(str(row["model_selector"]).casefold(), [])
                model_id = matches[0]["id"] if len(matches) == 1 else row["model_selector"]
                model_sha256 = matches[0]["model_sha256"] if len(matches) == 1 else None
                index_sha256 = matches[0]["index_sha256"] if len(matches) == 1 else None
                state = "active" if len(matches) == 1 else "invalid"
                db.execute(
                    "INSERT INTO batch_rules VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["id"],row["input_root"],row["output_root"],model_id,
                        model_sha256,index_sha256,row["preset_json"],row["preset_name"],
                        row["recursive"],
                        row["watch_enabled"],row["extensions_json"],row["last_error"],
                        row["last_error_at"],state,row["created_at"],row["updated_at"],
                    ),
                )
            db.execute(
                "INSERT INTO batch_items SELECT * FROM batch_items_v7"
            )
            db.execute("DROP TABLE batch_items_v7")
            db.execute("DROP TABLE batch_rules_v7")
        else:
            cls._add_column(db, "batch_rules", "model_sha256 TEXT")
            cls._add_column(db, "batch_rules", "state TEXT NOT NULL DEFAULT 'active'")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_runs (
                id TEXT PRIMARY KEY REFERENCES tasks(id),
                batch_id TEXT NOT NULL REFERENCES batch_rules(id),
                item_ids_json TEXT NOT NULL,
                submission_failures_json TEXT NOT NULL DEFAULT '[]',state TEXT NOT NULL,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL
            )
            """
        )

    @classmethod
    def _migrate_to_9(cls, db: sqlite3.Connection) -> None:
        cls._add_column(db, "tasks", "snapshot_json TEXT NOT NULL DEFAULT '{}'")

    @classmethod
    def _migrate_to_10(cls, db: sqlite3.Connection) -> None:
        cls._add_column(db, "batch_rules", "index_sha256 TEXT")

    @classmethod
    def _migrate_to_11(cls, db: sqlite3.Connection) -> None:
        cls._add_column(
            db,
            "batch_runs",
            "submission_failures_json TEXT NOT NULL DEFAULT '[]'",
        )

    @classmethod
    def _migrate_to_12(cls, db: sqlite3.Connection) -> None:
        for row in db.execute("SELECT id,family FROM models").fetchall():
            if row["id"] in FEMALE_MODEL_IDS or row["family"] in FEMALE_MODEL_FAMILIES:
                pitch = 9
            elif row["id"] in MALE_MODEL_IDS:
                pitch = 0
            else:
                continue
            recommended = {**MODEL_RECOMMENDATION_DEFAULTS, "pitch": pitch}
            db.execute(
                "UPDATE models SET recommended_json=? WHERE id=?",
                (json.dumps(recommended, ensure_ascii=False), row["id"]),
            )

    @classmethod
    def _validate_schema(cls, db: sqlite3.Connection) -> None:
        required = {
            "models": {"id", "model_sha256", "status"},
            "tasks": {
                "id",
                "state",
                "request_id",
                "actor_json",
                "snapshot_json",
                "retry_of",
            },
            "task_events": {"id", "task_id", "state"},
            "batch_rules": {
                "id",
                "model_id",
                "model_sha256",
                "index_sha256",
                "state",
                "watch_enabled",
            },
            "batch_items": {"id", "source_sha256", "task_id"},
            "batch_runs": {
                "id",
                "batch_id",
                "item_ids_json",
                "submission_failures_json",
                "state",
            },
            "realtime_sessions": {"id", "state", "model_sha256"},
            "artifacts": {"id", "task_id", "path", "state"},
        }
        for table, columns in required.items():
            actual = cls._columns(db, table)
            missing = columns - actual
            if missing:
                raise RuntimeError(f"database schema is missing {table} columns: {sorted(missing)}")

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
            if field not in result:
                continue
            value = result.pop(field)
            result[field.removesuffix("_json")] = (
                json.loads(value) if value is not None else None
            )
        return result
