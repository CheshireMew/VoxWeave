from __future__ import annotations

import json
import sqlite3
from typing import Any

from .database import Database, utc_now
from .pagination import decode_cursor, encode_cursor


class BatchRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_rule(self, values: tuple[Any, ...]) -> None:
        self.database.execute(
            "INSERT INTO batch_rules("
            "id,input_root,output_root,model_id,model_sha256,index_sha256,preset_json,preset_name,"
            "recursive,watch_enabled,extensions_json,state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )

    @staticmethod
    def decode_rule(row: dict[str, Any]) -> dict[str, Any]:
        result = Database.decode_json_row(row, ("preset_json", "extensions_json"))
        result["recursive"] = bool(result["recursive"])
        result["watch_enabled"] = bool(result["watch_enabled"])
        return result

    def get(self, batch_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM batch_rules WHERE id=?", (batch_id,))
        if not row:
            raise LookupError(f"batch not found: {batch_id}")
        return self.decode_rule(row)

    def list(self, limit: int, cursor: str | None) -> dict[str, Any]:
        where = ""
        if cursor:
            created_at, batch_id = decode_cursor(cursor, resource="batch")
            where = "WHERE created_at<? OR (created_at=? AND id<?)"
            parameters: tuple[Any, ...] = (created_at, created_at, batch_id, limit + 1)
        else:
            parameters = (limit + 1,)
        with self.database.connect() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    f"SELECT * FROM batch_rules {where} "  # noqa: S608
                    "ORDER BY created_at DESC,id DESC LIMIT ?",
                    parameters,
                ).fetchall()
            ]
            visible_ids = [str(row["id"]) for row in rows[:limit]]
            counts_by_rule: dict[str, dict[str, int]] = {
                batch_id: {} for batch_id in visible_ids
            }
            if visible_ids:
                placeholders = ",".join("?" for _ in visible_ids)
                count_rows = db.execute(
                    "SELECT batch_id,state,COUNT(*) AS count FROM batch_items "
                    f"WHERE batch_id IN ({placeholders}) GROUP BY batch_id,state",  # noqa: S608
                    tuple(visible_ids),
                ).fetchall()
                for count in count_rows:
                    counts_by_rule[str(count["batch_id"])][str(count["state"])] = int(
                        count["count"]
                    )
        has_more = len(rows) > limit
        items = []
        for row in rows[:limit]:
            item = self.decode_rule(row)
            item["item_counts"] = counts_by_rule[str(item["id"])]
            items.append(item)
        next_cursor = None
        if has_more and items:
            next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"])
        return {"items": items, "next_cursor": next_cursor}

    def set_watch(self, batch_id: str, enabled: bool) -> None:
        self.database.execute(
            "UPDATE batch_rules SET watch_enabled=?,revision=revision+1,updated_at=? WHERE id=?",
            (int(enabled), utc_now(), batch_id),
        )

    def update_rule(self, batch_id: str, values: tuple[Any, ...]) -> None:
        self.database.execute(
            "UPDATE batch_rules SET input_root=?,output_root=?,model_id=?,model_sha256=?,"
            "index_sha256=?,preset_json=?,preset_name=?,recursive=?,watch_enabled=?,"
            "extensions_json=?,revision=revision+1,updated_at=? WHERE id=?",
            (*values, batch_id),
        )

    def set_archived(self, batch_id: str, archived: bool) -> None:
        self.database.execute(
            "UPDATE batch_rules SET state=?,watch_enabled=0,revision=revision+1,updated_at=? "
            "WHERE id=?",
            ("archived" if archived else "active", utc_now(), batch_id),
        )

    @staticmethod
    def assert_revision(
        db: sqlite3.Connection,
        batch_id: str,
        revision: int,
    ) -> None:
        row = db.execute(
            "SELECT revision,state FROM batch_rules WHERE id=?", (batch_id,)
        ).fetchone()
        if not row:
            raise LookupError(f"batch not found: {batch_id}")
        if int(row["revision"]) != revision or row["state"] != "active":
            from .protocol import OperationError

            raise OperationError(
                "batch_rule_changed",
                "batch rule changed while a file was being prepared; it will be reconsidered",
            )

    @staticmethod
    def find_item(
        db: sqlite3.Connection, batch_id: str, source_path: str, source_sha256: str
    ) -> sqlite3.Row | None:
        return db.execute(
            "SELECT * FROM batch_items WHERE batch_id=? AND source_path=? AND source_sha256=?",
            (batch_id, source_path, source_sha256),
        ).fetchone()

    @staticmethod
    def insert_item(db: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        db.execute(
            "INSERT INTO batch_items("
            "id,batch_id,source_path,source_size,source_mtime_ns,source_sha256,"
            "output_path,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            values,
        )

    @staticmethod
    def link_item(
        db: sqlite3.Connection, item_id: str, task_id: str, state: str = "queued"
    ) -> None:
        db.execute(
            "UPDATE batch_items SET task_id=?,state=?,error=NULL,updated_at=? WHERE id=?",
            (task_id, state, utc_now(), item_id),
        )

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        return self.database.fetch_one("SELECT * FROM batch_items WHERE id=?", (item_id,))

    def insert_run(
        self,
        task_id: str,
        batch_id: str,
        item_ids: list[str],
        failures: list[dict[str, Any]],
    ) -> None:
        now = utc_now()
        self.database.execute(
            "INSERT INTO batch_runs("
            "id,batch_id,item_ids_json,submission_failures_json,state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                task_id,
                batch_id,
                json.dumps(item_ids),
                json.dumps(failures, ensure_ascii=False),
                "running",
                now,
                now,
            ),
        )

    def relink_retry(self, previous_task_id: str, task_id: str) -> None:
        self.database.execute(
            "UPDATE batch_items SET task_id=?,state='queued',error=NULL,updated_at=? "
            "WHERE task_id=?",
            (task_id, utc_now(), previous_task_id),
        )

    def retryable_items(self, batch_id: str) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM batch_items WHERE batch_id=? "
            "AND state IN ('failed','cancelled','interrupted') ORDER BY created_at",
            (batch_id,),
        )

    def link_existing_item(self, item_id: str, task_id: str) -> None:
        self.database.execute(
            "UPDATE batch_items SET task_id=?,state='queued',error=NULL,updated_at=? WHERE id=?",
            (task_id, utc_now(), item_id),
        )

    def pending_items(self) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT batch_items.id,batch_items.state,batch_items.error,"
            "tasks.state AS task_state,tasks.error AS task_error "
            "FROM batch_items JOIN tasks ON tasks.id=batch_items.task_id "
            "WHERE batch_items.task_id IS NOT NULL "
            "AND batch_items.state IN ('queued','running')"
        )

    def update_item_state(self, item_id: str, state: str, error: str | None) -> None:
        self.database.execute(
            "UPDATE batch_items SET state=?,error=?,updated_at=? WHERE id=?",
            (state, error, utc_now(), item_id),
        )

    def active_runs(self) -> list[dict[str, Any]]:
        return self.database.fetch_all("SELECT * FROM batch_runs WHERE state='running'")

    def active_run_ids(self) -> set[str]:
        return {str(row["id"]) for row in self.active_runs()}

    def items_by_ids(self, item_ids: list[str]) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in item_ids)
        return self.database.fetch_all(
            f"SELECT * FROM batch_items WHERE id IN ({placeholders})",  # noqa: S608
            tuple(item_ids),
        )

    def finish_run(self, task_id: str, state: str) -> None:
        self.database.execute(
            "UPDATE batch_runs SET state=?,updated_at=? WHERE id=?",
            (state, utc_now(), task_id),
        )

    def watched_rules(self) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM batch_rules WHERE watch_enabled=1 AND state='active'"
        )

    def clear_rule_error(self, batch_id: str) -> None:
        self.database.execute(
            "UPDATE batch_rules SET last_error=NULL,last_error_at=NULL WHERE id=?",
            (batch_id,),
        )

    def record_rule_error(self, batch_id: str, error: str) -> None:
        now = utc_now()
        self.database.execute(
            "UPDATE batch_rules SET last_error=?,last_error_at=?,updated_at=? WHERE id=?",
            (error, now, now, batch_id),
        )
