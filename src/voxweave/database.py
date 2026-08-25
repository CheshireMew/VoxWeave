from __future__ import annotations

import json
import queue
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 22

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
    def __init__(self, path: Path, *, pool_size: int = 4):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._pool_size = max(1, pool_size)
        self._pool: queue.LifoQueue[sqlite3.Connection] = queue.LifoQueue(
            maxsize=self._pool_size
        )
        self._pool_lock = threading.Lock()
        self._pool_created = 0
        self._closed = False
        self.migrate()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _acquire(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("database is closed")
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            pass
        with self._pool_lock:
            if self._closed:
                raise RuntimeError("database is closed")
            if self._pool_created < self._pool_size:
                self._pool_created += 1
                try:
                    return self._new_connection()
                except Exception:
                    self._pool_created -= 1
                    raise
        return self._pool.get()

    def _release(self, connection: sqlite3.Connection) -> None:
        if self._closed:
            connection.close()
            with self._pool_lock:
                self._pool_created -= 1
            return
        self._pool.put(connection)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._acquire()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._release(connection)

    def close(self) -> None:
        with self._pool_lock:
            if self._closed:
                return
            self._closed = True
        while True:
            try:
                connection = self._pool.get_nowait()
            except queue.Empty:
                break
            connection.close()
            with self._pool_lock:
                self._pool_created -= 1

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def migrate(self) -> None:
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            stored = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
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
                    13: self._migrate_to_13,
                    14: self._migrate_to_14,
                    15: self._migrate_to_15,
                    16: self._migrate_to_16,
                    17: self._migrate_to_17,
                    18: self._migrate_to_18,
                    19: self._migrate_to_19,
                    20: self._migrate_to_20,
                    21: self._migrate_to_21,
                    22: self._migrate_to_22,
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
                    archived INTEGER NOT NULL DEFAULT 0,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS presets (
                    id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL REFERENCES models(id),
                    name TEXT NOT NULL,
                    model_sha256 TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'conversion'
                        CHECK(kind IN ('conversion','realtime')),
                    archived INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
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
                    worker_failures INTEGER NOT NULL DEFAULT 0,
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
                CREATE INDEX IF NOT EXISTS task_events_task_id_index
                    ON task_events(task_id,id);
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
                    naming_template TEXT NOT NULL
                        DEFAULT '{stem}_{source_ext}_{model}_{preset}_{hash}',
                    preserve_structure INTEGER NOT NULL DEFAULT 1,
                    collision_policy TEXT NOT NULL DEFAULT 'skip',
                    output_format TEXT NOT NULL DEFAULT 'auto',
                    include_globs_json TEXT NOT NULL DEFAULT '[]',
                    exclude_globs_json TEXT NOT NULL DEFAULT '[]',
                    variants_json TEXT NOT NULL,
                    last_error TEXT,
                    last_error_at TEXT,
                    state TEXT NOT NULL DEFAULT 'active',
                    revision INTEGER NOT NULL DEFAULT 1,
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
                    variant_name TEXT NOT NULL,
                    variant_json TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    task_id TEXT REFERENCES tasks(id),
                    state TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(batch_id, source_path, source_sha256, variant_name)
                );
                CREATE INDEX IF NOT EXISTS batch_rules_watch_index
                    ON batch_rules(watch_enabled,state);
                CREATE INDEX IF NOT EXISTS batch_items_state_index
                    ON batch_items(batch_id,state,updated_at);
                CREATE INDEX IF NOT EXISTS batch_items_pending_index
                    ON batch_items(state,task_id,id) WHERE task_id IS NOT NULL;
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
                CREATE INDEX IF NOT EXISTS batch_runs_state_index
                    ON batch_runs(state,updated_at);
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
                CREATE INDEX IF NOT EXISTS realtime_sessions_created_index
                    ON realtime_sessions(created_at DESC,id);
                CREATE INDEX IF NOT EXISTS realtime_sessions_active_index
                    ON realtime_sessions(created_at DESC,id)
                    WHERE state IN ('starting','running','stopping');
                CREATE INDEX IF NOT EXISTS realtime_events_session_index
                    ON realtime_events(session_id,id);
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
                CREATE TABLE IF NOT EXISTS operation_receipts (
                    request_id TEXT PRIMARY KEY,
                    operation TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    actor_json TEXT,
                    state TEXT NOT NULL CHECK(state IN ('running','completed','failed')),
                    result_json TEXT,
                    error_type TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings_events (
                    revision INTEGER PRIMARY KEY,
                    changed_fields_json TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    input_sha256 TEXT,
                    output_path TEXT,
                    content_mode TEXT NOT NULL
                        CHECK(content_mode IN ('clean','mixed','singing')),
                    analysis_manifest TEXT,
                    analysis_sha256 TEXT,
                    document_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active'
                        CHECK(state IN ('active','archived')),
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS projects_updated_index
                    ON projects(state,updated_at DESC,id DESC);
                CREATE TABLE IF NOT EXISTS project_revisions (
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,revision)
                );
                CREATE TABLE IF NOT EXISTS model_user_metadata (
                    model_id TEXT PRIMARY KEY REFERENCES models(id),
                    custom_name TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    sample_path TEXT,
                    cover_path TEXT,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT,
                    integrity_status TEXT NOT NULL DEFAULT 'unchecked',
                    integrity_checked_at TEXT,
                    integrity_error TEXT,
                    revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS realtime_scenes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    settings_json TEXT NOT NULL,
                    hotkeys_json TEXT NOT NULL DEFAULT '{}',
                    archived INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS result_versions (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
                    project_id TEXT REFERENCES projects(id),
                    project_revision INTEGER,
                    input_path TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    output_sha256 TEXT NOT NULL,
                    model_json TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    parent_id TEXT REFERENCES result_versions(id),
                    root_id TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1,
                    rerun_arguments_json TEXT NOT NULL,
                    differences_json TEXT NOT NULL DEFAULT '{}',
                    label TEXT NOT NULL DEFAULT '',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS result_versions_input_index
                    ON result_versions(input_sha256,created_at DESC,id DESC);
                CREATE INDEX IF NOT EXISTS result_versions_lineage_index
                    ON result_versions(root_id,generation,id);
                CREATE TABLE IF NOT EXISTS storage_migrations (
                    id TEXT PRIMARY KEY,
                    source_root TEXT NOT NULL,
                    target_root TEXT NOT NULL,
                    plan_digest TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
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
                        row["id"],
                        row["input_root"],
                        row["output_root"],
                        model_id,
                        model_sha256,
                        index_sha256,
                        row["preset_json"],
                        row["preset_name"],
                        row["recursive"],
                        row["watch_enabled"],
                        row["extensions_json"],
                        row["last_error"],
                        row["last_error_at"],
                        state,
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
            db.execute("INSERT INTO batch_items SELECT * FROM batch_items_v7")
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
    def _migrate_to_13(cls, db: sqlite3.Connection) -> None:
        cls._add_column(db, "models", "archived INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _migrate_to_14(db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE INDEX IF NOT EXISTS task_events_task_id_index
                ON task_events(task_id,id);
            CREATE INDEX IF NOT EXISTS realtime_sessions_created_index
                ON realtime_sessions(created_at DESC,id);
            CREATE INDEX IF NOT EXISTS realtime_events_session_index
                ON realtime_events(session_id,id);
            CREATE INDEX IF NOT EXISTS batch_rules_watch_index
                ON batch_rules(watch_enabled,state);
            CREATE INDEX IF NOT EXISTS batch_items_state_index
                ON batch_items(batch_id,state,updated_at);
            CREATE INDEX IF NOT EXISTS batch_runs_state_index
                ON batch_runs(state,updated_at);
            """
        )

    @staticmethod
    def _migrate_to_15(db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE INDEX IF NOT EXISTS batch_items_pending_index
                ON batch_items(state,task_id,id) WHERE task_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS realtime_sessions_state_index
                ON realtime_sessions(state,created_at DESC,id);
            """
        )

    @staticmethod
    def _migrate_to_16(db: sqlite3.Connection) -> None:
        db.execute(
            "CREATE INDEX IF NOT EXISTS realtime_sessions_active_index "
            "ON realtime_sessions(created_at DESC,id) "
            "WHERE state IN ('starting','running','stopping')"
        )

    @staticmethod
    def _migrate_to_17(db: sqlite3.Connection) -> None:
        db.execute("DROP INDEX IF EXISTS realtime_sessions_state_index")

    @classmethod
    def _migrate_to_18(cls, db: sqlite3.Connection) -> None:
        cls._add_column(db, "tasks", "worker_failures INTEGER NOT NULL DEFAULT 0")
        cls._add_column(db, "batch_rules", "revision INTEGER NOT NULL DEFAULT 1")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS operation_receipts (
                request_id TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                actor_json TEXT,
                state TEXT NOT NULL CHECK(state IN ('running','completed','failed')),
                result_json TEXT,
                error_type TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings_events (
                revision INTEGER PRIMARY KEY,
                changed_fields_json TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    @classmethod
    def _migrate_to_19(cls, db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                input_path TEXT NOT NULL,
                input_sha256 TEXT,
                output_path TEXT,
                content_mode TEXT NOT NULL
                    CHECK(content_mode IN ('clean','mixed','singing')),
                analysis_manifest TEXT,
                analysis_sha256 TEXT,
                document_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'active'
                    CHECK(state IN ('active','archived')),
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS projects_updated_index
                ON projects(state,updated_at DESC,id DESC);
            CREATE TABLE IF NOT EXISTS project_revisions (
                project_id TEXT NOT NULL REFERENCES projects(id),
                revision INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id,revision)
            );
            CREATE TABLE IF NOT EXISTS model_user_metadata (
                model_id TEXT PRIMARY KEY REFERENCES models(id),
                custom_name TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                favorite INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                sample_path TEXT,
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS realtime_scenes (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                settings_json TEXT NOT NULL,
                hotkeys_json TEXT NOT NULL DEFAULT '{}',
                archived INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    @classmethod
    def _migrate_to_20(cls, db: sqlite3.Connection) -> None:
        cls._add_column(db, "presets", "kind TEXT NOT NULL DEFAULT 'conversion'")
        cls._add_column(db, "presets", "archived INTEGER NOT NULL DEFAULT 0")
        cls._add_column(db, "presets", "revision INTEGER NOT NULL DEFAULT 1")
        cls._add_column(db, "presets", "updated_at TEXT")
        db.execute("UPDATE presets SET updated_at=created_at WHERE updated_at IS NULL")
        cls._add_column(
            db, "model_user_metadata", "revision INTEGER NOT NULL DEFAULT 1"
        )
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS result_versions (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
                project_id TEXT REFERENCES projects(id),
                project_revision INTEGER,
                input_path TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                output_path TEXT NOT NULL,
                output_sha256 TEXT NOT NULL,
                model_json TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                favorite INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS result_versions_input_index
                ON result_versions(input_sha256,created_at DESC,id DESC);
            """
        )

    @classmethod
    def _migrate_to_21(cls, db: sqlite3.Connection) -> None:
        cls._add_column(
            db,
            "batch_rules",
            "naming_template TEXT NOT NULL DEFAULT '{stem}_{source_ext}_{model}_{preset}_{hash}'",
        )
        cls._add_column(
            db, "batch_rules", "preserve_structure INTEGER NOT NULL DEFAULT 1"
        )
        cls._add_column(
            db, "batch_rules", "collision_policy TEXT NOT NULL DEFAULT 'skip'"
        )
        cls._add_column(
            db, "batch_rules", "output_format TEXT NOT NULL DEFAULT 'auto'"
        )
        cls._add_column(
            db, "batch_rules", "include_globs_json TEXT NOT NULL DEFAULT '[]'"
        )
        cls._add_column(
            db, "batch_rules", "exclude_globs_json TEXT NOT NULL DEFAULT '[]'"
        )

    @classmethod
    def _migrate_to_22(cls, db: sqlite3.Connection) -> None:
        cls._add_column(db, "batch_rules", "variants_json TEXT NOT NULL DEFAULT '[]'")
        rows = db.execute(
            "SELECT id,model_id,model_sha256,index_sha256,preset_json,preset_name,"
            "output_format,include_globs_json,exclude_globs_json FROM batch_rules"
        ).fetchall()
        for row in rows:
            variants = [
                {
                    "name": "default",
                    "model_id": row["model_id"],
                    "model_sha256": row["model_sha256"],
                    "index_sha256": row["index_sha256"],
                    "preset": json.loads(row["preset_json"] or "{}"),
                    "preset_name": row["preset_name"],
                    "output_format": row["output_format"],
                    "include_globs": json.loads(row["include_globs_json"] or "[]"),
                    "exclude_globs": json.loads(row["exclude_globs_json"] or "[]"),
                    "extensions": [],
                }
            ]
            db.execute(
                "UPDATE batch_rules SET variants_json=? WHERE id=?",
                (json.dumps(variants, ensure_ascii=False), row["id"]),
            )

        if "variant_name" not in cls._columns(db, "batch_items"):
            db.execute("ALTER TABLE batch_items RENAME TO batch_items_v21")
            db.executescript(
                """
                CREATE TABLE batch_items (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES batch_rules(id),
                    source_path TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    variant_name TEXT NOT NULL,
                    variant_json TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    task_id TEXT REFERENCES tasks(id),
                    state TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(batch_id, source_path, source_sha256, variant_name)
                );
                INSERT INTO batch_items(
                    id,batch_id,source_path,source_size,source_mtime_ns,source_sha256,
                    variant_name,variant_json,output_path,task_id,state,error,created_at,updated_at
                )
                SELECT id,batch_id,source_path,source_size,source_mtime_ns,source_sha256,
                    'default','{}',output_path,task_id,state,error,created_at,updated_at
                FROM batch_items_v21;
                DROP TABLE batch_items_v21;
                CREATE INDEX IF NOT EXISTS batch_items_state_index
                    ON batch_items(batch_id,state,updated_at);
                CREATE INDEX IF NOT EXISTS batch_items_pending_index
                    ON batch_items(state,task_id,id) WHERE task_id IS NOT NULL;
                """
            )

        for definition in (
            "cover_path TEXT",
            "usage_count INTEGER NOT NULL DEFAULT 0",
            "last_used_at TEXT",
            "integrity_status TEXT NOT NULL DEFAULT 'unchecked'",
            "integrity_checked_at TEXT",
            "integrity_error TEXT",
        ):
            cls._add_column(db, "model_user_metadata", definition)

        for definition in (
            "parent_id TEXT REFERENCES result_versions(id)",
            "root_id TEXT",
            "generation INTEGER NOT NULL DEFAULT 1",
            "rerun_arguments_json TEXT NOT NULL DEFAULT '{}'",
            "differences_json TEXT NOT NULL DEFAULT '{}'",
        ):
            cls._add_column(db, "result_versions", definition)
        db.execute("UPDATE result_versions SET root_id=id WHERE root_id IS NULL")
        db.executescript(
            """
            CREATE INDEX IF NOT EXISTS result_versions_lineage_index
                ON result_versions(root_id,generation,id);
            CREATE TABLE IF NOT EXISTS storage_migrations (
                id TEXT PRIMARY KEY,
                source_root TEXT NOT NULL,
                target_root TEXT NOT NULL,
                plan_digest TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                manifest_path TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    @classmethod
    def _validate_schema(cls, db: sqlite3.Connection) -> None:
        required = {
            "models": {"id", "model_sha256", "status", "archived"},
            "tasks": {
                "id",
                "state",
                "request_id",
                "actor_json",
                "snapshot_json",
                "retry_of",
                "worker_failures",
            },
            "task_events": {"id", "task_id", "state"},
            "batch_rules": {
                "id",
                "model_id",
                "model_sha256",
                "index_sha256",
                "state",
                "watch_enabled",
                "revision",
                "naming_template",
                "preserve_structure",
                "collision_policy",
                "output_format",
                "include_globs_json",
                "exclude_globs_json",
                "variants_json",
            },
            "batch_items": {
                "id",
                "source_sha256",
                "variant_name",
                "variant_json",
                "task_id",
            },
            "batch_runs": {
                "id",
                "batch_id",
                "item_ids_json",
                "submission_failures_json",
                "state",
            },
            "realtime_sessions": {"id", "state", "model_sha256"},
            "artifacts": {"id", "task_id", "path", "state"},
            "operation_receipts": {"request_id", "operation", "state"},
            "settings_events": {"revision", "settings_json"},
            "projects": {
                "id",
                "input_path",
                "document_json",
                "state",
                "revision",
            },
            "project_revisions": {"project_id", "revision", "snapshot_json"},
            "model_user_metadata": {
                "model_id",
                "tags_json",
                "favorite",
                "cover_path",
                "usage_count",
                "integrity_status",
                "revision",
            },
            "realtime_scenes": {"id", "name", "settings_json", "revision"},
            "presets": {"id", "kind", "archived", "revision", "updated_at"},
            "result_versions": {
                "id",
                "task_id",
                "input_sha256",
                "output_sha256",
                "result_json",
                "root_id",
                "generation",
                "rerun_arguments_json",
                "differences_json",
            },
            "storage_migrations": {"id", "source_root", "target_root", "state"},
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
            result[field.removesuffix("_json")] = json.loads(value) if value is not None else None
        return result
