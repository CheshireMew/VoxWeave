from __future__ import annotations

from typing import Any

from .database import Database, utc_now


class StorageRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def tasks_by_ids(self, task_ids: list[str]) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in task_ids)
        return self.database.fetch_all(
            f"SELECT * FROM tasks WHERE id IN ({placeholders}) ORDER BY updated_at",  # noqa: S608
            tuple(task_ids),
        )

    def tasks_before(self, cutoff: str) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM tasks WHERE updated_at<=? ORDER BY updated_at", (cutoff,)
        )

    def archive(self, task_id: str) -> dict[str, Any] | None:
        return self.database.fetch_one(
            "SELECT * FROM artifact_archives WHERE task_id=?", (task_id,)
        )

    def plan_archive(
        self,
        task_id: str,
        source_path: str,
        archive_path: str,
        file_count: int,
        size_bytes: int,
    ) -> None:
        self.database.execute(
            "INSERT INTO artifact_archives("
            "task_id,source_path,archive_path,state,file_count,size_bytes,created_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
            "source_path=excluded.source_path,archive_path=excluded.archive_path,"
            "state=excluded.state,file_count=excluded.file_count,size_bytes=excluded.size_bytes",
            (
                task_id,
                source_path,
                archive_path,
                "planned",
                file_count,
                size_bytes,
                utc_now(),
            ),
        )

    def mark_moved(self, task_id: str) -> None:
        self.database.execute(
            "UPDATE artifact_archives SET state='moved' WHERE task_id=?", (task_id,)
        )

    def complete(self, task_id: str) -> dict[str, Any]:
        self.database.execute(
            "UPDATE artifact_archives SET state='completed',completed_at=? WHERE task_id=?",
            (utc_now(), task_id),
        )
        archive = self.archive(task_id)
        if archive is None:
            raise RuntimeError(f"archive record missing after move: {task_id}")
        return archive
