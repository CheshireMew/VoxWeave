from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from .database import Database, utc_now
from .pagination import decode_cursor, encode_cursor
from .protocol import OperationError


class ProjectRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        return Database.decode_json_row(row, ("document_json",))

    @staticmethod
    def _snapshot(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: record.get(key)
            for key in (
                "name",
                "input_path",
                "input_sha256",
                "output_path",
                "content_mode",
                "analysis_manifest",
                "analysis_sha256",
                "document",
                "state",
            )
        }

    @staticmethod
    def _insert_revision(
        db: sqlite3.Connection,
        project_id: str,
        revision: int,
        snapshot: dict[str, Any],
        created_at: str,
    ) -> None:
        db.execute(
            "INSERT INTO project_revisions(project_id,revision,snapshot_json,created_at) "
            "VALUES(?,?,?,?)",
            (
                project_id,
                revision,
                json.dumps(snapshot, ensure_ascii=False),
                created_at,
            ),
        )

    def create(
        self,
        *,
        name: str,
        input_path: str,
        output_path: str | None,
        content_mode: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        project_id = str(uuid.uuid4())
        now = utc_now()
        record = {
            "id": project_id,
            "name": name,
            "input_path": input_path,
            "input_sha256": None,
            "output_path": output_path,
            "content_mode": content_mode,
            "analysis_manifest": None,
            "analysis_sha256": None,
            "document": document,
            "state": "active",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        with self.database.connect() as db:
            db.execute(
                "INSERT INTO projects("
                "id,name,input_path,input_sha256,output_path,content_mode,analysis_manifest,"
                "analysis_sha256,document_json,state,revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    name,
                    input_path,
                    None,
                    output_path,
                    content_mode,
                    None,
                    None,
                    json.dumps(document, ensure_ascii=False),
                    "active",
                    1,
                    now,
                    now,
                ),
            )
            self._insert_revision(db, project_id, 1, self._snapshot(record), now)
        return record

    def get(self, project_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM projects WHERE id=?", (project_id,))
        if row is None:
            raise LookupError(f"project not found: {project_id}")
        return self._decode(row)

    def list(
        self,
        limit: int,
        cursor: str | None,
        *,
        include_archived: bool,
    ) -> dict[str, Any]:
        clauses = [] if include_archived else ["state='active'"]
        parameters: list[Any] = []
        if cursor:
            updated_at, project_id = decode_cursor(cursor, resource="project")
            clauses.append("(updated_at<? OR (updated_at=? AND id<?))")
            parameters.extend((updated_at, updated_at, project_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit + 1)
        rows = self.database.fetch_all(
            "SELECT * FROM projects "
            f"{where} ORDER BY updated_at DESC,id DESC LIMIT ?",  # noqa: S608
            tuple(parameters),
        )
        decoded = [self._decode(row) for row in rows[:limit]]
        items = []
        for record in decoded:
            segments = list((record.get("document") or {}).get("segments") or [])
            default_model = (record.get("document") or {}).get("default_model")
            items.append(
                {
                    key: record.get(key)
                    for key in (
                        "id",
                        "name",
                        "input_path",
                        "output_path",
                        "content_mode",
                        "state",
                        "revision",
                        "created_at",
                        "updated_at",
                    )
                }
                | {
                    "segment_count": len(segments),
                    "assigned_segment_count": sum(
                        1
                        for segment in segments
                        if segment.get("enabled", True)
                        and (segment.get("model") or default_model)
                    ),
                }
            )
        next_cursor = None
        if len(rows) > limit and decoded:
            last = decoded[-1]
            next_cursor = encode_cursor(last["updated_at"], last["id"])
        return {"items": items, "next_cursor": next_cursor}

    def update(
        self,
        project_id: str,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = {
            "name",
            "input_path",
            "input_sha256",
            "output_path",
            "content_mode",
            "analysis_manifest",
            "analysis_sha256",
            "document",
            "state",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported project fields: {sorted(unknown)}")
        now = utc_now()
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise LookupError(f"project not found: {project_id}")
            current = self._decode(dict(row))
            if int(current["revision"]) != expected_revision:
                raise OperationError(
                    "revision_conflict",
                    f"project revision changed: expected {expected_revision}, "
                    f"current {current['revision']}",
                )
            updated = {**current, **changes}
            revision = expected_revision + 1
            db.execute(
                "UPDATE projects SET name=?,input_path=?,input_sha256=?,output_path=?,"
                "content_mode=?,analysis_manifest=?,analysis_sha256=?,document_json=?,state=?,"
                "revision=?,updated_at=? WHERE id=? AND revision=?",
                (
                    updated["name"],
                    updated["input_path"],
                    updated.get("input_sha256"),
                    updated.get("output_path"),
                    updated["content_mode"],
                    updated.get("analysis_manifest"),
                    updated.get("analysis_sha256"),
                    json.dumps(updated["document"], ensure_ascii=False),
                    updated["state"],
                    revision,
                    now,
                    project_id,
                    expected_revision,
                ),
            )
            updated.update(revision=revision, updated_at=now)
            self._insert_revision(
                db, project_id, revision, self._snapshot(updated), now
            )
        return updated

    def history(self, project_id: str) -> list[dict[str, Any]]:
        self.get(project_id)
        rows = self.database.fetch_all(
            "SELECT project_id,revision,snapshot_json,created_at "
            "FROM project_revisions WHERE project_id=? ORDER BY revision DESC",
            (project_id,),
        )
        return [Database.decode_json_row(row, ("snapshot_json",)) for row in rows]

    def restore(
        self,
        project_id: str,
        expected_revision: int,
        revision: int,
    ) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT snapshot_json FROM project_revisions WHERE project_id=? AND revision=?",
            (project_id, revision),
        )
        if row is None:
            raise LookupError(f"project revision not found: {project_id}@{revision}")
        return self.update(
            project_id,
            expected_revision,
            json.loads(row["snapshot_json"]),
        )
