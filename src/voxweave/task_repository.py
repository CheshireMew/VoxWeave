from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .database import Database, utc_now
from .pagination import decode_cursor, encode_cursor
from .protocol import OperationError


class TaskRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _insert_event(
        db: sqlite3.Connection,
        task_id: str,
        state: str,
        progress: float,
        stage: str | None,
        detail: str | None,
    ) -> None:
        db.execute(
            "INSERT INTO task_events("
            "task_id,state,progress,stage,detail,created_at) VALUES(?,?,?,?,?,?)",
            (task_id, state, progress, stage, detail, utc_now()),
        )

    def recover_for_start(self, preserved_task_ids: set[str]) -> list[str]:
        now = utc_now()
        with self.database.connect() as db:
            active = db.execute(
                "SELECT id FROM tasks WHERE state NOT IN "
                "('queued','completed','failed','cancelled','interrupted')"
            ).fetchall()
            for row in active:
                if row["id"] in preserved_task_ids:
                    continue
                db.execute(
                    "UPDATE tasks SET state='interrupted',stage='service_restart',"
                    "error_type='service_restart',error='service stopped during task',"
                    "updated_at=? WHERE id=?",
                    (now, row["id"]),
                )
                self._insert_event(
                    db,
                    row["id"],
                    "interrupted",
                    0.0,
                    "service_restart",
                    "service stopped during task",
                )
            return [
                str(row["id"])
                for row in db.execute(
                    "SELECT id FROM tasks WHERE state='queued' ORDER BY created_at"
                ).fetchall()
            ]

    def create(
        self,
        db: sqlite3.Connection,
        operation: str,
        arguments: dict[str, Any],
        *,
        request_id: str | None,
        actor: dict[str, Any] | None,
        snapshot: dict[str, Any] | None,
        retry_of: str | None,
    ) -> tuple[str, bool]:
        if request_id:
            existing = db.execute(
                "SELECT id,operation,arguments_json FROM tasks WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if existing:
                if existing["operation"] != operation or json.loads(
                    existing["arguments_json"]
                ) != arguments:
                    raise OperationError(
                        "idempotency_conflict",
                        "request_id is already associated with a different command",
                    )
                return str(existing["id"]), False
        task_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO tasks("
            "id,operation,arguments_json,state,progress,stage,request_id,actor_json,snapshot_json,"
            "retry_of,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                operation,
                json.dumps(arguments, ensure_ascii=False),
                "queued",
                0.0,
                "queued",
                request_id,
                json.dumps(actor, ensure_ascii=False) if actor else None,
                json.dumps(snapshot or {}, ensure_ascii=False),
                retry_of,
                now,
                now,
            ),
        )
        self._insert_event(db, task_id, "queued", 0.0, "queued", None)
        return task_id, True

    def update(
        self,
        task_id: str,
        *,
        state: str,
        progress: float,
        stage: str | None,
        detail: str | None,
        result: Any,
        error_type: str | None,
        error: str | None,
    ) -> None:
        with self.database.connect() as db:
            db.execute(
                "UPDATE tasks SET state=?,progress=?,stage=?,result_json=?,error_type=?,error=?,"
                "updated_at=? WHERE id=?",
                (
                    state,
                    progress,
                    stage,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error_type,
                    error,
                    utc_now(),
                    task_id,
                ),
            )
            self._insert_event(db, task_id, state, progress, stage, detail or error)

    def raw(self, task_id: str) -> dict[str, Any] | None:
        return self.database.fetch_one("SELECT * FROM tasks WHERE id=?", (task_id,))

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        result = Database.decode_json_row(
            row, ("arguments_json", "result_json", "actor_json", "snapshot_json")
        )
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["task_id"] = result["id"]
        return result

    def get(self, task_id: str) -> dict[str, Any]:
        row = self.raw(task_id)
        if not row:
            raise LookupError(f"task not found: {task_id}")
        return self._decode(row)

    def list(self, limit: int, cursor: str | None) -> dict[str, Any]:
        where = ""
        if cursor:
            created_at, task_id = decode_cursor(cursor, resource="task")
            where = "WHERE created_at<? OR (created_at=? AND id<?)"
            parameters: tuple[Any, ...] = (created_at, created_at, task_id, limit + 1)
        else:
            parameters = (limit + 1,)
        rows = self.database.fetch_all(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC,id DESC LIMIT ?",  # noqa: S608
            parameters,
        )
        has_more = len(rows) > limit
        results = [self._decode(row) for row in rows[:limit]]
        next_cursor = None
        if has_more and results:
            last = results[-1]
            next_cursor = encode_cursor(last["created_at"], last["id"])
        return {"items": results, "next_cursor": next_cursor}

    def cancel_requested(self, task_id: str) -> bool:
        current = self.database.fetch_one(
            "SELECT cancel_requested FROM tasks WHERE id=?", (task_id,)
        )
        return bool(current and current["cancel_requested"])

    def request_cancel(self, task_id: str) -> None:
        self.database.execute(
            "UPDATE tasks SET cancel_requested=1,updated_at=? WHERE id=?",
            (utc_now(), task_id),
        )

    def events(self, task_id: str, after_id: int, limit: int) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM task_events WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
            (task_id, after_id, limit),
        )

    def events_all(self, after_id: int, limit: int) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM task_events WHERE id>? ORDER BY id LIMIT ?", (after_id, limit)
        )

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM task_events ORDER BY id DESC LIMIT ?", (limit,)
        )
