from __future__ import annotations

import json
import queue
import threading
import traceback
import uuid
from collections.abc import Callable
from typing import Any

from .database import Database, utc_now

Progress = Callable[[float, str, str | None], None]
Handler = Callable[[dict[str, Any], Progress, Callable[[], bool]], Any]


TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


class TaskManager:
    def __init__(self, database: Database):
        self.database = database
        self.handlers: dict[str, Handler] = {}
        self.queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._run, name="voxweave-task-worker", daemon=True)
        self.worker.start()
        for task in self.database.fetch_all(
            "SELECT id FROM tasks WHERE state='queued' ORDER BY created_at"
        ):
            self.queue.put(task["id"])

    def register(self, operation: str, handler: Handler) -> None:
        self.handlers[operation] = handler

    def submit(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if operation not in self.handlers:
            raise LookupError(f"no long-running handler registered for {operation}")
        task_id = str(uuid.uuid4())
        arguments = dict(arguments)
        arguments["_task_id"] = task_id
        now = utc_now()
        self.database.execute(
            "INSERT INTO tasks("
            "id,operation,arguments_json,state,progress,stage,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                task_id,
                operation,
                json.dumps(arguments, ensure_ascii=False),
                "queued",
                0.0,
                "queued",
                now,
                now,
            ),
        )
        self._event(task_id, "queued", 0.0, "queued", None)
        self.queue.put(task_id)
        return self.get(task_id)

    def _event(
        self,
        task_id: str,
        state: str,
        progress: float,
        stage: str | None,
        detail: str | None,
    ) -> None:
        self.database.execute(
            "INSERT INTO task_events("
            "task_id,state,progress,stage,detail,created_at) VALUES(?,?,?,?,?,?)",
            (task_id, state, progress, stage, detail, utc_now()),
        )

    def _update(
        self,
        task_id: str,
        *,
        state: str,
        progress: float,
        stage: str | None,
        detail: str | None = None,
        result: Any = None,
        error_type: str | None = None,
        error: str | None = None,
    ) -> None:
        self.database.execute(
            "UPDATE tasks SET "
            "state=?,progress=?,stage=?,result_json=?,error_type=?,error=?,updated_at=? "
            "WHERE id=?",
            (
                state,
                max(0.0, min(1.0, progress)),
                stage,
                json.dumps(result, ensure_ascii=False) if result is not None else None,
                error_type,
                error,
                utc_now(),
                task_id,
            ),
        )
        self._event(task_id, state, progress, stage, detail or error)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                task_id = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                self._execute(task_id)
            finally:
                self.queue.task_done()

    def _execute(self, task_id: str) -> None:
        row = self.database.fetch_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row or row["state"] != "queued":
            return
        operation = row["operation"]
        handler = self.handlers.get(operation)
        if not handler:
            self._update(
                task_id,
                state="failed",
                progress=0.0,
                stage="dispatch",
                error_type="operation_unavailable",
                error=f"no handler registered for {operation}",
            )
            return

        def cancelled() -> bool:
            current = self.database.fetch_one(
                "SELECT cancel_requested FROM tasks WHERE id=?", (task_id,)
            )
            return self.stop_event.is_set() or bool(current and current["cancel_requested"])

        def progress(value: float, stage: str, detail: str | None = None) -> None:
            if cancelled():
                raise InterruptedError("task cancellation requested")
            self._update(task_id, state=stage, progress=value, stage=stage, detail=detail)

        self._update(task_id, state="running", progress=0.01, stage="starting")
        try:
            result = handler(json.loads(row["arguments_json"]), progress, cancelled)
        except InterruptedError as exc:
            service_stopping = self.stop_event.is_set()
            self._update(
                task_id,
                state="interrupted" if service_stopping else "cancelled",
                progress=0.0,
                stage="service_shutdown" if service_stopping else "cancelled",
                error_type="service_shutdown" if service_stopping else "cancelled",
                error="service stopped during task" if service_stopping else str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - task errors must be persisted
            self._update(
                task_id,
                state="failed",
                progress=0.0,
                stage="failed",
                error_type=type(exc).__name__,
                error=f"{exc}\n{traceback.format_exc()}",
            )
        else:
            self._update(task_id, state="completed", progress=1.0, stage="completed", result=result)

    def get(self, task_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row:
            raise LookupError(f"task not found: {task_id}")
        result = Database.decode_json_row(row, ("arguments_json", "result_json"))
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["task_id"] = result["id"]
        return result

    def list(self) -> list[dict[str, Any]]:
        rows = self.database.fetch_all("SELECT * FROM tasks ORDER BY created_at DESC")
        results = [Database.decode_json_row(row, ("arguments_json", "result_json")) for row in rows]
        for result in results:
            result["cancel_requested"] = bool(result["cancel_requested"])
            result["task_id"] = result["id"]
        return results

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task["state"] in TERMINAL_STATES:
            return task
        self.database.execute(
            "UPDATE tasks SET cancel_requested=1,updated_at=? WHERE id=?",
            (utc_now(), task_id),
        )
        return self.get(task_id)

    def retry(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task["state"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("only failed, cancelled, or interrupted tasks can be retried")
        arguments = dict(task["arguments"])
        arguments["_resume_from_task_id"] = task_id
        return self.submit(task["operation"], arguments)

    def events(self, task_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM task_events WHERE task_id=? AND id>? ORDER BY id",
            (task_id, after_id),
        )

    def shutdown(self) -> None:
        self.stop_event.set()
        self.worker.join(timeout=15)
