from __future__ import annotations

import json
import sqlite3

import pytest

from voxweave.database import SCHEMA_VERSION, Database
from voxweave.model_registry import ModelRegistry


def test_future_database_schema_is_rejected(tmp_path) -> None:
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO metadata VALUES('schema_version','999')")
    with pytest.raises(RuntimeError, match="newer than supported"):
        Database(path)


def test_latest_schema_contains_growth_indexes(tmp_path) -> None:
    database = Database(tmp_path / "indexed.sqlite3")
    indexes = {
        row["name"]
        for row in database.fetch_all("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert {
        "task_events_task_id_index",
        "realtime_sessions_created_index",
        "realtime_sessions_active_index",
        "realtime_events_session_index",
        "batch_rules_watch_index",
        "batch_items_state_index",
        "batch_items_pending_index",
        "batch_runs_state_index",
    } <= indexes
    pending_plan = " ".join(
        row["detail"]
        for row in database.fetch_all(
            "EXPLAIN QUERY PLAN SELECT batch_items.id FROM batch_items "
            "JOIN tasks ON tasks.id=batch_items.task_id "
            "WHERE batch_items.task_id IS NOT NULL "
            "AND batch_items.state IN ('queued','running')"
        )
    )
    realtime_plan = " ".join(
        row["detail"]
        for row in database.fetch_all(
            "EXPLAIN QUERY PLAN SELECT id FROM realtime_sessions "
            "WHERE state IN ('starting','running','stopping') "
            "ORDER BY created_at DESC LIMIT 1"
        )
    )
    assert "batch_items_pending_index" in pending_plan
    assert "realtime_sessions_active_index" in realtime_plan
    assert "TEMP B-TREE" not in realtime_plan


def test_v8_database_adds_execution_and_index_snapshots(tmp_path) -> None:
    path = tmp_path / "v8.sqlite3"
    Database(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE tasks DROP COLUMN snapshot_json")
        db.execute("ALTER TABLE batch_rules DROP COLUMN index_sha256")
        db.execute("UPDATE metadata SET value='8' WHERE key='schema_version'")
    database = Database(path)
    version = database.fetch_one("SELECT value FROM metadata WHERE key='schema_version'")
    assert version == {"value": str(SCHEMA_VERSION)}
    assert "snapshot_json" in {
        row["name"] for row in database.fetch_all("PRAGMA table_info(tasks)")
    }
    assert "index_sha256" in {
        row["name"] for row in database.fetch_all("PRAGMA table_info(batch_rules)")
    }


def test_v11_database_updates_bundled_voice_recommendations(tmp_path) -> None:
    path = tmp_path / "v11.sqlite3"
    database = Database(path)
    registry = ModelRegistry(database)
    female_path = tmp_path / "suara_wanita_2.pth"
    male_path = tmp_path / "male.pth"
    female_path.write_bytes(b"female")
    male_path.write_bytes(b"male")
    female = registry.register(female_path, inspection={"status": "ready"})
    male = registry.register(
        male_path,
        model_id="community.zh-male-deep",
        inspection={"status": "ready"},
    )
    database.execute(
        "UPDATE models SET recommended_json='{}' WHERE id IN (?,?)",
        (female["id"], male["id"]),
    )
    database.execute("UPDATE metadata SET value='11' WHERE key='schema_version'")

    migrated = ModelRegistry(Database(path))

    assert migrated.resolve(female["id"])["recommended"]["pitch"] == 9
    assert migrated.resolve(male["id"])["recommended"] == {
        "pitch": 0,
        "f0": "rmvpe",
        "index_rate": 0.72,
        "rms_mix_rate": 0.25,
        "protect": 0.33,
        "content_mode": "clean",
    }


def test_v12_database_adds_non_destructive_model_archive_state(tmp_path) -> None:
    path = tmp_path / "v12.sqlite3"
    database = Database(path)
    registry = ModelRegistry(database)
    model_path = tmp_path / "voice.pth"
    model_path.write_bytes(b"voice")
    model = registry.register(model_path, inspection={"status": "ready"})
    database.execute("ALTER TABLE models DROP COLUMN archived")
    database.execute("UPDATE metadata SET value='12' WHERE key='schema_version'")

    migrated = ModelRegistry(Database(path)).resolve(model["id"])

    assert migrated["archived"] is False
    assert model_path.is_file()


def test_v7_batch_selector_migrates_once_to_locked_model_revision(tmp_path) -> None:
    path = tmp_path / "v7.sqlite3"
    Database(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("DROP TABLE batch_runs")
        db.execute("DROP TABLE batch_items")
        db.execute("DROP TABLE batch_rules")
        db.execute(
            "CREATE TABLE batch_rules("
            "id TEXT PRIMARY KEY,input_root TEXT NOT NULL,output_root TEXT NOT NULL,"
            "model_selector TEXT NOT NULL,preset_json TEXT NOT NULL,"
            "preset_name TEXT NOT NULL DEFAULT 'default',recursive INTEGER NOT NULL,"
            "watch_enabled INTEGER NOT NULL,extensions_json TEXT NOT NULL,last_error TEXT,"
            "last_error_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE batch_items("
            "id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,source_path TEXT NOT NULL,"
            "source_size INTEGER NOT NULL,source_mtime_ns INTEGER NOT NULL,"
            "source_sha256 TEXT NOT NULL,output_path TEXT NOT NULL,task_id TEXT,"
            "state TEXT NOT NULL,"
            "error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
            "UNIQUE(batch_id,source_path,source_sha256))"
        )
        db.execute(
            "INSERT INTO models(id,display_name,aliases_json,family,model_path,model_sha256,"
            "index_sha256,index_candidates_json,source_kind,recommended_json,status,imported_at) "
            "VALUES('voice','Voice','[\"Alias\"]','voice','C:/voice.pth',?,?,"
            "'[]','test','{}','ready','now')",
            ("a" * 64, "b" * 64),
        )
        db.execute(
            "INSERT INTO batch_rules VALUES("
            "'batch','C:/input','C:/output','alias','{}','default',1,0,'[\".wav\"]',"
            "NULL,NULL,'now','now')"
        )
        db.execute("UPDATE metadata SET value='7' WHERE key='schema_version'")
    database = Database(path)
    rule = database.fetch_one("SELECT * FROM batch_rules WHERE id='batch'")
    assert rule and rule["model_id"] == "voice"
    assert rule["model_sha256"] == "a" * 64
    assert rule["index_sha256"] == "b" * 64
    assert rule["state"] == "active"


def test_v21_database_migrates_batch_variants_model_metadata_and_result_lineage(
    tmp_path,
) -> None:
    path = tmp_path / "v21.sqlite3"
    database = Database(path)
    database.execute(
        "INSERT INTO models(id,display_name,aliases_json,family,model_path,model_sha256,"
        "index_candidates_json,source_kind,recommended_json,status,imported_at,archived) "
        "VALUES('voice','Voice','[]','voice','C:/voice.pth',?,'[]','test','{}',"
        "'ready','now',0)",
        ("a" * 64,),
    )
    database.execute(
        "INSERT INTO tasks(id,operation,arguments_json,state,progress,stage,snapshot_json,"
        "created_at,updated_at) VALUES('task','conversion.run','{}','completed',1,"
        "'completed','{}','now','now')"
    )
    database.execute(
        "INSERT INTO batch_rules(id,input_root,output_root,model_id,model_sha256,index_sha256,"
        "preset_json,preset_name,recursive,watch_enabled,extensions_json,naming_template,"
        "preserve_structure,collision_policy,output_format,include_globs_json,"
        "exclude_globs_json,variants_json,state,created_at,updated_at) "
        "VALUES('batch','C:/input','C:/output','voice',?,NULL,'{}','default',1,0,"
        "'[\".wav\"]','{stem}',1,'skip','auto','[]','[]','[]','active','now','now')",
        ("a" * 64,),
    )
    database.execute(
        "INSERT INTO batch_items(id,batch_id,source_path,source_size,source_mtime_ns,"
        "source_sha256,variant_name,variant_json,output_path,task_id,state,error,created_at,"
        "updated_at) VALUES('item','batch','C:/input/a.wav',1,1,?,'default','{}',"
        "'C:/output/a.wav','task','completed',NULL,'now','now')",
        ("b" * 64,),
    )
    database.execute(
        "INSERT INTO model_user_metadata(model_id,custom_name,tags_json,favorite,notes,"
        "sample_path,cover_path,usage_count,last_used_at,integrity_status,integrity_checked_at,"
        "integrity_error,revision,updated_at) VALUES('voice',NULL,'[]',0,'',NULL,NULL,0,NULL,"
        "'unchecked',NULL,NULL,1,'now')"
    )
    database.execute(
        "INSERT INTO result_versions(id,task_id,project_id,project_revision,input_path,"
        "input_sha256,output_path,output_sha256,model_json,parameters_json,result_json,"
        "parent_id,root_id,generation,rerun_arguments_json,differences_json,label,favorite,"
        "created_at) VALUES('result','task',NULL,NULL,'C:/input/a.wav',?,'C:/output/a.wav',?,"
        "'{}','{}','{}',NULL,'result',1,'{}','{}','',0,'now')",
        ("b" * 64, "c" * 64),
    )
    database.close()

    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("DROP INDEX result_versions_lineage_index")
        db.execute("ALTER TABLE batch_rules DROP COLUMN variants_json")
        db.execute("ALTER TABLE batch_items RENAME TO batch_items_v22")
        db.executescript(
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
            );
            INSERT INTO batch_items
            SELECT id,batch_id,source_path,source_size,source_mtime_ns,source_sha256,
                output_path,task_id,state,error,created_at,updated_at
            FROM batch_items_v22;
            DROP TABLE batch_items_v22;
            DROP TABLE storage_migrations;
            """
        )
        for column in (
            "cover_path",
            "usage_count",
            "last_used_at",
            "integrity_status",
            "integrity_checked_at",
            "integrity_error",
        ):
            db.execute(f"ALTER TABLE model_user_metadata DROP COLUMN {column}")
        for column in (
            "parent_id",
            "root_id",
            "generation",
            "rerun_arguments_json",
            "differences_json",
        ):
            db.execute(f"ALTER TABLE result_versions DROP COLUMN {column}")
        db.execute("UPDATE metadata SET value='21' WHERE key='schema_version'")

    migrated = Database(path)
    rule = migrated.fetch_one("SELECT variants_json FROM batch_rules WHERE id='batch'")
    assert rule and json.loads(rule["variants_json"])[0]["model_id"] == "voice"
    item = migrated.fetch_one("SELECT variant_name,variant_json FROM batch_items WHERE id='item'")
    assert item == {"variant_name": "default", "variant_json": "{}"}
    metadata_columns = {
        row["name"] for row in migrated.fetch_all("PRAGMA table_info(model_user_metadata)")
    }
    assert {"cover_path", "usage_count", "integrity_status"} <= metadata_columns
    result = migrated.fetch_one(
        "SELECT root_id,generation,rerun_arguments_json,differences_json "
        "FROM result_versions WHERE id='result'"
    )
    assert result == {
        "root_id": "result",
        "generation": 1,
        "rerun_arguments_json": "{}",
        "differences_json": "{}",
    }
    assert migrated.fetch_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='storage_migrations'"
    ) == {"name": "storage_migrations"}
    migrated.close()
