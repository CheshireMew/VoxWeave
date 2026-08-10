from __future__ import annotations

import sqlite3

import pytest

from voxweave.database import SCHEMA_VERSION, Database


def test_future_database_schema_is_rejected(tmp_path) -> None:
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO metadata VALUES('schema_version','999')")
    with pytest.raises(RuntimeError, match="newer than supported"):
        Database(path)


def test_v8_database_adds_execution_and_index_snapshots(tmp_path) -> None:
    path = tmp_path / "v8.sqlite3"
    Database(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE tasks DROP COLUMN snapshot_json")
        db.execute("ALTER TABLE batch_rules DROP COLUMN index_sha256")
        db.execute(
            "UPDATE metadata SET value='8' WHERE key='schema_version'"
        )
    database = Database(path)
    version = database.fetch_one(
        "SELECT value FROM metadata WHERE key='schema_version'"
    )
    assert version == {"value": str(SCHEMA_VERSION)}
    assert "snapshot_json" in {
        row["name"] for row in database.fetch_all("PRAGMA table_info(tasks)")
    }
    assert "index_sha256" in {
        row["name"] for row in database.fetch_all("PRAGMA table_info(batch_rules)")
    }


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
