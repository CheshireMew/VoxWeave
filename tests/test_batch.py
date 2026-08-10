from __future__ import annotations

import os
import time
from pathlib import Path

from voxweave.batch import BatchManager
from voxweave.database import Database
from voxweave.model_registry import ModelRegistry
from voxweave.task_manager import TaskManager


def _wait_completed(tasks: TaskManager, task_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = tasks.get(task_id)
        if task["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        time.sleep(0.02)
    raise AssertionError("task did not finish")


def _managers(tmp_path, *, fail_first: bool = False):
    database = Database(tmp_path / "state.sqlite3")
    tasks = TaskManager(database)
    model_path = tmp_path / "model.pth"
    model_path.write_bytes(b"model")
    models = ModelRegistry(database)
    models.register(
        model_path,
        model_id="model.example",
        display_name="Example",
        inspection={"status": "ready"},
    )
    batch = BatchManager(database, tasks, models.resolve_for_execution)
    attempts: dict[str, int] = {}

    def convert(arguments, _context):
        attempts[arguments["input"]] = attempts.get(arguments["input"], 0) + 1
        if fail_first and attempts[arguments["input"]] == 1:
            raise RuntimeError("first conversion attempt failed")
        output = Path(arguments["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(Path(arguments["input"]).read_bytes())
        return {"input": arguments["input"], "output": str(output)}

    tasks.register("conversion.run", convert)
    tasks.register("batch.run", batch.run)
    tasks.register("batch.retry", batch.retry)
    tasks.start()
    batch.start()
    return database, tasks, batch


def test_batch_identity_uses_content_hash_even_when_metadata_is_unchanged(tmp_path) -> None:
    database, tasks, batch = _managers(tmp_path)
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    source = input_root / "voice.wav"
    source.write_bytes(b"AAAA")
    original_stat = source.stat()
    rule = batch.create(
        {
            "input_root": str(input_root),
            "output_root": str(output_root),
            "model": "model.example",
            "preset": {},
            "recursive": True,
            "watch": False,
        }
    )

    first_run = tasks.submit("batch.run", {"batch_id": rule["id"]})
    first_parent = _wait_completed(tasks, first_run["id"])
    assert first_parent["state"] == "completed"
    first = first_parent["result"]["items"][0]
    first_output = Path(first["output_path"])
    assert first_output.read_bytes() == b"AAAA"
    source.write_bytes(b"BBBB")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    second_run = tasks.submit("batch.run", {"batch_id": rule["id"]})
    second_parent = _wait_completed(tasks, second_run["id"])
    assert second_parent["state"] == "completed"
    second = second_parent["result"]["items"][0]

    assert first["source_size"] == second["source_size"]
    assert first["source_mtime_ns"] == second["source_mtime_ns"]
    assert first["source_sha256"] != second["source_sha256"]
    assert first["id"] != second["id"]
    assert first["output_path"] != second["output_path"]
    assert Path(second["output_path"]).read_bytes() == b"BBBB"
    assert database.fetch_one("SELECT COUNT(*) AS count FROM batch_items")["count"] == 2
    batch.shutdown()
    tasks.shutdown()


def test_watcher_records_rule_errors_without_stopping(tmp_path) -> None:
    database, tasks, batch = _managers(tmp_path)
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    rule = batch.create(
        {
            "input_root": str(input_root),
            "output_root": str(output_root),
            "model": "model.example",
            "preset": {},
            "recursive": True,
            "watch": True,
        }
    )
    database.execute(
        "UPDATE batch_rules SET extensions_json='not-json' WHERE id=?", (rule["id"],)
    )
    deadline = time.monotonic() + 5
    stored = None
    while time.monotonic() < deadline:
        stored = database.fetch_one(
            "SELECT last_error,last_error_at FROM batch_rules WHERE id=?", (rule["id"],)
        )
        if stored and stored["last_error"]:
            break
        time.sleep(0.05)
    assert stored and stored["last_error"]
    assert stored["last_error_at"]
    assert batch.thread.is_alive()
    batch.shutdown()
    tasks.shutdown()


def test_same_stem_different_extensions_publish_distinct_outputs(tmp_path) -> None:
    _database, tasks, batch = _managers(tmp_path)
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "voice.wav").write_bytes(b"wave")
    (input_root / "voice.mp3").write_bytes(b"mpeg")
    rule = batch.create(
        {
            "input_root": str(input_root),
            "output_root": str(tmp_path / "output"),
            "model": "model.example",
            "preset": {},
            "watch": False,
        }
    )
    parent = tasks.submit("batch.run", {"batch_id": rule["id"]})
    completed = _wait_completed(tasks, parent["id"])
    assert completed["state"] == "completed"
    outputs = [Path(item["output_path"]) for item in completed["result"]["items"]]
    assert len(set(outputs)) == 2
    assert {path.read_bytes() for path in outputs} == {b"wave", b"mpeg"}
    assert any("_wav_" in path.name for path in outputs)
    assert any("_mp3_" in path.name for path in outputs)
    batch.shutdown()
    tasks.shutdown()


def test_partial_batch_submission_is_visible_on_parent_task(tmp_path, monkeypatch) -> None:
    _database, tasks, batch = _managers(tmp_path)
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "good.wav").write_bytes(b"good")
    (input_root / "bad.wav").write_bytes(b"bad")
    rule = batch.create(
        {
            "input_root": str(input_root),
            "output_root": str(tmp_path / "output"),
            "model": "model.example",
            "preset": {},
            "watch": False,
        }
    )
    submit_file = batch._submit_file

    def fail_one(stored_rule, source):
        if source.name == "bad.wav":
            raise OSError("source became unavailable")
        return submit_file(stored_rule, source)

    monkeypatch.setattr(batch, "_submit_file", fail_one)
    parent = tasks.submit("batch.run", {"batch_id": rule["id"]})
    failed = _wait_completed(tasks, parent["id"])
    assert failed["state"] == "failed"
    assert failed["error_type"] == "batch_run_failed"
    assert failed["result"]["counts"] == {"completed": 1}
    assert failed["result"]["submission_failures"][0]["source_path"].endswith(
        "bad.wav"
    )
    batch.shutdown()
    tasks.shutdown()


def test_deferred_batch_parent_recovers_after_service_restart(tmp_path) -> None:
    database, tasks, batch = _managers(tmp_path)
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "voice.wav").write_bytes(b"voice")
    rule = batch.create(
        {
            "input_root": str(input_root),
            "output_root": str(tmp_path / "output"),
            "model": "model.example",
            "preset": {},
            "watch": False,
        }
    )
    parent = tasks.submit("batch.run", {"batch_id": rule["id"]})
    deadline = time.monotonic() + 2
    child = None
    while time.monotonic() < deadline:
        item = database.fetch_one(
            "SELECT task_id FROM batch_items WHERE batch_id=?", (rule["id"],)
        )
        if item and item["task_id"]:
            child = _wait_completed(tasks, item["task_id"])
            break
        time.sleep(0.01)
    assert child and child["state"] == "completed"
    batch.shutdown()
    assert tasks.get(parent["id"])["state"] == "running"
    tasks.shutdown()

    recovered_tasks = TaskManager(Database(database.path))
    recovered_models = ModelRegistry(recovered_tasks.database)
    recovered_batch = BatchManager(
        recovered_tasks.database,
        recovered_tasks,
        recovered_models.resolve_for_execution,
    )
    recovered_tasks.register("conversion.run", lambda _arguments, _context: {})
    recovered_tasks.register("batch.run", recovered_batch.run)
    recovered_tasks.start(preserved_task_ids=recovered_batch.durable_task_ids())
    recovered_batch.start()
    recovered_parent = _wait_completed(recovered_tasks, parent["id"])
    assert recovered_parent["state"] == "completed"
    assert recovered_parent["result"]["counts"] == {"completed": 1}
    recovered_batch.shutdown()
    recovered_tasks.shutdown()


def test_batch_retry_reuses_failed_item_and_completes_parent(tmp_path) -> None:
    database, tasks, batch = _managers(tmp_path, fail_first=True)
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / "voice.wav").write_bytes(b"voice")
    rule = batch.create(
        {
            "input_root": str(input_root),
            "output_root": str(tmp_path / "output"),
            "model": "model.example",
            "preset": {},
            "watch": False,
        }
    )
    first = tasks.submit("batch.run", {"batch_id": rule["id"]})
    assert _wait_completed(tasks, first["id"])["state"] == "failed"
    failed_item = database.fetch_one(
        "SELECT * FROM batch_items WHERE batch_id=?", (rule["id"],)
    )
    assert failed_item and failed_item["state"] == "failed"
    retry = tasks.submit("batch.retry", {"batch_id": rule["id"]})
    completed = _wait_completed(tasks, retry["id"])
    assert completed["state"] == "completed"
    assert completed["result"]["counts"] == {"completed": 1}
    refreshed = database.fetch_one(
        "SELECT * FROM batch_items WHERE id=?", (failed_item["id"],)
    )
    assert refreshed and refreshed["state"] == "completed"
    assert refreshed["task_id"] != failed_item["task_id"]
    assert Path(refreshed["output_path"]).read_bytes() == b"voice"
    batch.shutdown()
    tasks.shutdown()
