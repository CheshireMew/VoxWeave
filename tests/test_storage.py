from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from voxweave.artifacts import ArtifactStore
from voxweave.config import Settings
from voxweave.database import Database
from voxweave.storage import StorageArchiveManager
from voxweave.task_manager import TaskManager


def _wait_for_task(tasks: TaskManager, task_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = tasks.get(task_id)
        if task["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task did not finish: {task_id}")


def test_archive_moves_artifacts_without_rewriting_immutable_task_result(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path / "data"))
    settings.ensure_layout()
    database = Database(settings.database_path)
    tasks = TaskManager(database)
    artifacts = ArtifactStore(database)
    storage = StorageArchiveManager(settings, database, artifacts)

    def produce(_arguments: dict[str, Any], context: Any) -> dict[str, str]:
        work_dir = settings.artifacts_dir / context.task_id
        work_dir.mkdir()
        manifest = work_dir / "manifest.json"
        manifest.write_text('{"created":true}\n', encoding="utf-8")
        artifacts.register(context.task_id, "manifest", manifest)
        context.progress(0.9, "produced", str(manifest))
        return {"manifest_path": str(manifest)}

    tasks.register("test.produce-artifact", produce)
    tasks.register(
        "storage.archive",
        lambda arguments, context: storage.archive(
            arguments, context.progress, context.cancelled, context.task_id
        ),
    )
    tasks.start()
    try:
        produced = tasks.submit("test.produce-artifact", {})
        producer_task = _wait_for_task(tasks, produced["id"])
        assert producer_task["state"] == "completed"
        source = settings.artifacts_dir / produced["id"]
        assert Path(producer_task["result"]["manifest_path"]).read_text(
            encoding="utf-8"
        ) == '{"created":true}\n'

        archive_root = tmp_path / "archive"
        submitted = tasks.submit(
            "storage.archive",
            {
                "destination_root": str(archive_root),
                "task_ids": [produced["id"]],
                "confirm_source_removal": True,
            },
        )
        archive_task = _wait_for_task(tasks, submitted["id"])
        assert archive_task["state"] == "completed", archive_task.get("error")
        assert not source.exists()
        archived_manifest = (
            archive_root / "VoxWeave" / "artifacts" / produced["id"] / "manifest.json"
        )
        assert archived_manifest.read_text(encoding="utf-8") == '{"created":true}\n'
        refreshed = tasks.get(produced["id"])
        assert refreshed["result"]["manifest_path"] == str(source / "manifest.json")
        artifact = database.fetch_one(
            "SELECT * FROM artifacts WHERE task_id=?", (produced["id"],)
        )
        assert artifact and artifact["path"] == str(source / "manifest.json")
        assert artifact["archive_path"] == str(archived_manifest)
        assert artifact["state"] == "archived"
        record = database.fetch_one(
            "SELECT * FROM artifact_archives WHERE task_id=?", (produced["id"],)
        )
        assert record and record["state"] == "completed"
        assert record["completed_at"]
    finally:
        tasks.shutdown()
