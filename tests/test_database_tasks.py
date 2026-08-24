from __future__ import annotations

import threading
import time

import pytest

from voxweave.database import Database
from voxweave.operation_receipt_repository import OperationReceiptRepository
from voxweave.protocol import OperationError
from voxweave.task_manager import DeferredTask, TaskManager


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
    attempts = 0

    def work(args, _context):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("first attempt fails")
        return {"value": args["value"]}

    manager.register("test.work", work)
    manager.start()
    first = manager.submit("test.work", {"value": 7})
    assert first["task_id"] == first["id"]
    failed = wait_terminal(manager, first["task_id"])
    assert failed["state"] == "failed"
    retried = manager.retry(failed["id"])
    completed = wait_terminal(manager, retried["task_id"])
    assert completed["result"] == {"value": 7}
    assert completed["arguments"] == {"value": 7}
    assert completed["retry_of"] == failed["id"]
    manager.shutdown()


def test_task_manager_owns_restart_recovery(tmp_path) -> None:
    path = tmp_path / "state.sqlite3"
    database = Database(path)
    database.execute(
        "INSERT INTO tasks(id,operation,arguments_json,state,progress,stage,created_at,updated_at) "
        "VALUES('active','conversion.run','{}','validating',0.9,'validating','now','now')"
    )
    Database(path)
    row = database.fetch_one("SELECT state,stage FROM tasks WHERE id='active'")
    assert row == {"state": "validating", "stage": "validating"}
    manager = TaskManager(Database(path))
    manager.register("conversion.run", lambda _args, _context: None)
    manager.start()
    row = database.fetch_one("SELECT state,stage FROM tasks WHERE id='active'")
    assert row == {"state": "interrupted", "stage": "service_restart"}
    manager.shutdown()


def test_persisted_queue_starts_only_after_handlers_are_registered(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.execute(
        "INSERT INTO tasks(id,operation,arguments_json,state,progress,stage,created_at,updated_at) "
        "VALUES('queued','test.work','{\"value\":11}','queued',0,'queued','now','now')"
    )
    manager = TaskManager(database)
    manager.register("test.work", lambda args, _context: {"value": args["value"]})
    manager.start()
    completed = wait_terminal(manager, "queued")
    assert completed["state"] == "completed"
    assert completed["result"] == {"value": 11}
    assert completed["error_type"] is None
    manager.shutdown()


def test_terminal_event_is_published_once_with_result_committed(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    manager = TaskManager(database)

    def work(_args, context):
        context.progress(0.5, "working", "halfway")
        return {"value": 23}

    manager.register("test.work", work)
    manager.start()
    submitted = manager.submit("test.work", {})
    completed = wait_terminal(manager, submitted["id"])
    events = manager.events(submitted["id"])
    assert completed["result"] == {"value": 23}
    assert [event["state"] for event in events].count("completed") == 1
    assert all(event["state"] in {"queued", "running", "completed"} for event in events)
    manager.shutdown()


def test_progress_updates_are_rate_and_delta_limited(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    manager = TaskManager(database)

    def work(_args, context):
        started = time.perf_counter()
        for index in range(2000):
            context.progress(index / 2000, "working", str(index))
        return {"done": True, "elapsed_seconds": time.perf_counter() - started}

    manager.register("test.work", work)
    manager.start()
    task = manager.submit("test.work", {})
    completed = wait_terminal(manager, task["id"])
    assert completed["state"] == "completed"
    assert completed["result"]["elapsed_seconds"] < 2.0
    count = database.fetch_one(
        "SELECT COUNT(*) AS count FROM task_events WHERE task_id=?",
        (task["id"],),
    )["count"]
    assert count < 70
    manager.shutdown()


def test_cancellation_checks_do_not_query_sqlite(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    manager = TaskManager(database)

    def work(_args, context):
        started = time.perf_counter()
        assert not any(context.cancelled() for _ in range(5000))
        return {"elapsed_seconds": time.perf_counter() - started}

    manager.register("test.work", work)
    manager.start()
    task = manager.submit("test.work", {})
    completed = wait_terminal(manager, task["id"])
    assert completed["state"] == "completed"
    assert completed["result"]["elapsed_seconds"] < 0.5
    manager.shutdown()


def test_realtime_pause_keeps_new_tasks_queued_and_rejects_an_active_task(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    manager = TaskManager(database)
    release = threading.Event()

    def work(_args, _context):
        release.wait(timeout=2)
        return {"done": True}

    manager.register("test.work", work)
    manager.start()
    assert manager.pause_dispatch("realtime") is True
    queued = manager.submit("test.work", {})
    time.sleep(0.1)
    assert manager.get(queued["id"])["state"] == "queued"
    manager.resume_dispatch("realtime")

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and manager.get(queued["id"])["state"] != "running":
        time.sleep(0.02)
    assert manager.get(queued["id"])["state"] == "running"
    assert manager.pause_dispatch("realtime") is False
    release.set()
    assert wait_terminal(manager, queued["id"])["state"] == "completed"
    manager.shutdown()


def test_invalid_public_results_fail_without_killing_the_worker(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    manager = TaskManager(database)
    manager.register("model.scan", lambda _args, _context: {"not": "a list"})
    manager.register("batch.run", lambda _args, _context: DeferredTask("children", ""))
    manager.register("test.work", lambda _args, _context: {"alive": True})
    manager.start()

    invalid = manager.submit("model.scan", {})
    failed = wait_terminal(manager, invalid["id"])
    assert failed["state"] == "failed"
    assert failed["error_type"] == "invalid_result"

    deferred = manager.submit("batch.run", {})
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if manager.get(deferred["id"])["stage"] == "children":
            break
        time.sleep(0.02)
    manager.complete_deferred(deferred["id"], "not an object")
    deferred_failed = manager.get(deferred["id"])
    assert deferred_failed["state"] == "failed"
    assert deferred_failed["error_type"] == "invalid_result"

    healthy = manager.submit("test.work", {})
    assert wait_terminal(manager, healthy["id"])["result"] == {"alive": True}
    manager.shutdown()


def test_unhandled_worker_failures_are_durably_bounded(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "state.sqlite3")
    manager = TaskManager(database)
    manager.register("test.work", lambda _args, _context: {"unused": True})
    manager.start()

    def fail_outside_handler(_task_id: str) -> None:
        raise RuntimeError("worker boundary failed")

    monkeypatch.setattr(manager, "_execute", fail_outside_handler)
    submitted = manager.submit("test.work", {})
    failed = wait_terminal(manager, submitted["id"])

    assert failed["state"] == "failed"
    assert failed["error_type"] == "worker_failure"
    stored = database.fetch_one(
        "SELECT worker_failures FROM tasks WHERE id=?", (submitted["id"],)
    )
    assert stored == {"worker_failures": 3}
    assert manager.worker and manager.worker.is_alive()
    manager.shutdown()


def test_receipt_recovery_only_preserves_matching_long_task_operations(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    database.execute(
        "INSERT INTO tasks(id,operation,arguments_json,state,progress,stage,request_id,"
        "created_at,updated_at) VALUES('retry-child','conversion.run','{}','queued',0,"
        "'queued','retry-request','now','now')"
    )
    database.execute(
        "INSERT INTO tasks(id,operation,arguments_json,state,progress,stage,request_id,"
        "created_at,updated_at) VALUES('long-task','media.inspect','{}','queued',0,"
        "'queued','long-request','now','now')"
    )
    database.execute(
        "INSERT INTO tasks(id,operation,arguments_json,state,progress,stage,request_id,"
        "created_at,updated_at) VALUES('historical','model.scan','{}','completed',1,"
        "'completed','historical-request','now','now')"
    )
    for request_id, operation in (
        ("retry-request", "task.retry"),
        ("long-request", "media.inspect"),
    ):
        database.execute(
            "INSERT INTO operation_receipts(request_id,operation,arguments_json,state,"
            "created_at,updated_at) VALUES(?,?,?,'running','now','now')",
            (request_id, operation, "{}"),
        )

    receipts = OperationReceiptRepository(database)

    assert database.fetch_one(
        "SELECT state,error_type FROM operation_receipts WHERE request_id='retry-request'"
    ) == {"state": "failed", "error_type": "service_restart"}
    assert database.fetch_one(
        "SELECT state,error_type FROM operation_receipts WHERE request_id='long-request'"
    ) == {"state": "running", "error_type": None}
    with pytest.raises(OperationError, match="existing task"):
        receipts.claim("historical-request", "settings.update", {}, None)
