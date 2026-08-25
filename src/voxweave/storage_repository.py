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

    def completed_archive_count(self) -> int:
        row = self.database.fetch_one(
            "SELECT COUNT(*) AS count FROM artifact_archives WHERE state='completed'"
        )
        return int((row or {}).get("count") or 0)

    def completed_archives(self, task_ids: list[str]) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in task_ids)
        return self.database.fetch_all(
            "SELECT * FROM artifact_archives "
            f"WHERE task_id IN ({placeholders}) AND state='completed' ORDER BY task_id",  # noqa: S608
            tuple(task_ids),
        )

    def mark_restored(self, task_id: str) -> dict[str, Any]:
        self.database.execute(
            "UPDATE artifact_archives SET state='restored' WHERE task_id=?",
            (task_id,),
        )
        restored = self.archive(task_id)
        if not restored:
            raise RuntimeError(f"archive record missing after restore: {task_id}")
        return restored

    def migrations(self) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM storage_migrations ORDER BY created_at DESC,id DESC"
        )

    def terminal_tasks(self) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT id,state FROM tasks "
            "WHERE state IN ('completed','failed','cancelled','interrupted')"
        )

    def active_artifact_paths(self) -> set[str]:
        return {
            str(row["path"])
            for row in self.database.fetch_all(
                "SELECT path FROM artifacts WHERE state='active'"
            )
        }

    def create_migration(
        self,
        migration_id: str,
        source_root: str,
        target_root: str,
        plan_digest: str,
        manifest_path: str,
    ) -> None:
        now = utc_now()
        self.database.execute(
            "INSERT INTO storage_migrations(id,source_root,target_root,plan_digest,state,"
            "manifest_path,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                migration_id,
                source_root,
                target_root,
                plan_digest,
                "prepared",
                manifest_path,
                None,
                now,
                now,
            ),
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
