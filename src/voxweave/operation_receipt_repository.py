from __future__ import annotations

import json
import time
from typing import Any, Literal

from .database import Database, utc_now
from .protocol import OperationError


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class OperationReceiptRepository:
    """Durable exactly-once result boundary for synchronous requests."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.database.execute(
            "UPDATE operation_receipts SET state='failed',error_type='service_restart',"
            "error='service stopped before the request outcome was recorded',updated_at=? "
            "WHERE state='running' AND NOT EXISTS ("
            "SELECT 1 FROM tasks WHERE tasks.request_id=operation_receipts.request_id "
            "AND tasks.operation=operation_receipts.operation)",
            (utc_now(),),
        )

    def claim(
        self,
        request_id: str,
        operation: str,
        arguments: dict[str, Any],
        actor: dict[str, Any] | None,
    ) -> tuple[Literal["claimed", "completed", "failed", "running"], dict[str, Any] | None]:
        arguments_json = _canonical(arguments)
        actor_json = _canonical(actor) if actor is not None else None
        with self.database.connect() as db:
            existing = db.execute(
                "SELECT * FROM operation_receipts WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing:
                if (
                    existing["operation"] != operation
                    or existing["arguments_json"] != arguments_json
                    or existing["actor_json"] != actor_json
                ):
                    raise OperationError(
                        "idempotency_conflict",
                        "request_id is globally associated with a different command",
                    )
                record = dict(existing)
                if record["result_json"] is not None:
                    record["result"] = json.loads(record.pop("result_json"))
                return record["state"], record
            historical_task = db.execute(
                "SELECT 1 FROM tasks WHERE request_id=?", (request_id,)
            ).fetchone()
            if historical_task:
                raise OperationError(
                    "idempotency_conflict",
                    "request_id is globally associated with an existing task",
                )
            now = utc_now()
            db.execute(
                "INSERT INTO operation_receipts("
                "request_id,operation,arguments_json,actor_json,state,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (request_id, operation, arguments_json, actor_json, "running", now, now),
            )
        return "claimed", None

    def await_terminal(self, request_id: str, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.database.fetch_one(
                "SELECT state,result_json,error_type,error FROM operation_receipts "
                "WHERE request_id=?",
                (request_id,),
            )
            if row and row["state"] != "running":
                if row["result_json"] is not None:
                    row["result"] = json.loads(row.pop("result_json"))
                return row
            time.sleep(0.02)
        raise OperationError(
            "request_in_progress",
            "the original request is still running; query again with the same request_id",
        )

    def complete(self, request_id: str, result: Any) -> None:
        self.database.execute(
            "UPDATE operation_receipts SET state='completed',result_json=?,"
            "error_type=NULL,error=NULL,updated_at=? WHERE request_id=? AND state='running'",
            (_canonical(result), utc_now(), request_id),
        )

    def fail(self, request_id: str, error_type: str, error: str) -> None:
        self.database.execute(
            "UPDATE operation_receipts SET state='failed',result_json=NULL,error_type=?,error=?,"
            "updated_at=? WHERE request_id=? AND state='running'",
            (error_type, error, utc_now(), request_id),
        )
