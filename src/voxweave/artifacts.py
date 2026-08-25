from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from .database import Database, utc_now
from .hashing import VerifiedFile, sha256_file


class ArtifactStore:
    def __init__(self, database: Database):
        self.database = database

    def register(
        self,
        task_id: str,
        kind: str,
        path: Path,
        verified: VerifiedFile | None = None,
    ) -> dict[str, Any]:
        path = path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        if verified is not None:
            verified = verified.assert_unchanged(path)
        artifact_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"voxweave:artifact:{task_id}:{kind}:{path}")
        )
        now = utc_now()
        self.database.execute(
            "INSERT INTO artifacts("
            "id,task_id,kind,path,sha256,size_bytes,state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
            "task_id=excluded.task_id,kind=excluded.kind,sha256=excluded.sha256,"
            "size_bytes=excluded.size_bytes,state='active',archive_path=NULL,"
            "updated_at=excluded.updated_at",
            (
                artifact_id,
                task_id,
                kind,
                str(path),
                verified.sha256 if verified is not None else sha256_file(path),
                verified.size_bytes if verified is not None else path.stat().st_size,
                "active",
                now,
                now,
            ),
        )
        return self.database.fetch_one("SELECT * FROM artifacts WHERE path=?", (str(path),)) or {}

    def list_for_task(self, task_id: str) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM artifacts WHERE task_id=? ORDER BY kind,path", (task_id,)
        )

    def mark_archived(self, source: Path, destination: Path) -> None:
        source = source.resolve()
        destination = destination.resolve()
        rows = self.database.fetch_all("SELECT id,path FROM artifacts WHERE state='active'")
        updates = []
        for row in rows:
            path = Path(row["path"])
            try:
                relative = path.resolve().relative_to(source)
            except ValueError:
                continue
            archived_path = str(destination / relative)
            updates.append((archived_path, utc_now(), row["id"]))
        with self.database.connect() as db:
            db.executemany(
                "UPDATE artifacts SET archive_path=?,state='archived',updated_at=? "
                "WHERE id=?",
                updates,
            )

    def mark_restored(self, source: Path, archive: Path) -> None:
        source = source.resolve()
        archive = archive.resolve()
        rows = self.database.fetch_all(
            "SELECT id,archive_path FROM artifacts WHERE state='archived'"
        )
        updates = []
        for row in rows:
            archived_path = row.get("archive_path")
            if not archived_path:
                continue
            try:
                relative = Path(archived_path).resolve().relative_to(archive)
            except ValueError:
                continue
            updates.append((str(source / relative), utc_now(), row["id"]))
        with self.database.connect() as db:
            db.executemany(
                "UPDATE artifacts SET path=?,archive_path=NULL,state='active',updated_at=? "
                "WHERE id=?",
                updates,
            )
