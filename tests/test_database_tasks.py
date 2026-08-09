from __future__ import annotations

import time

from voxweave.database import Database
from voxweave.task_manager import TaskManager


def wait_terminal(manager: TaskManager, task_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = manager.get(task_id)
        if task["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        time.sleep(0.02)
    raise AssertionError("task did not reach a terminal state")


def test_task_result_and_retry_contract(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    manager = TaskManager(database)
    manager.register("test.work", lambda args, progress, _cancelled: {"value": args["value"]})
    first = manager.submit("test.work", {"value": 7})
    assert first["task_id"] == first["id"]
    completed = wait_terminal(manager, first["task_id"])
    assert completed["result"] == {"value": 7}
    manager.shutdown()


def test_database_marks_active_tasks_interrupted_on_reopen(tmp_path) -> None:
    path = tmp_path / "state.sqlite3"
    database = Database(path)
    database.execute(
        "INSERT INTO tasks(id,operation,arguments_json,state,progress,stage,created_at,updated_at) "
        "VALUES('active','conversion.run','{}','validating',0.9,'validating','now','now')"
    )
    Database(path)
    row = database.fetch_one("SELECT state,stage FROM tasks WHERE id='active'")
    assert row == {"state": "interrupted", "stage": "service_restart"}
