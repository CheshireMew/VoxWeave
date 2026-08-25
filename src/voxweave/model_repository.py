from __future__ import annotations

import json
from typing import Any

from .database import Database, utc_now
from .protocol import OperationError


class ModelRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, model_id: str) -> dict[str, Any] | None:
        return self.database.fetch_one("SELECT * FROM models WHERE id=?", (model_id,))

    def save(self, values: tuple[Any, ...]) -> None:
        self.database.execute(
            """
            INSERT INTO models(
              id,display_name,aliases_json,family,checkpoint_epoch,model_path,model_sha256,
              index_path,index_sha256,index_candidates_json,rvc_version,sample_rate,f0,source_kind,
              license_spdx,source_url,recommended_json,status,imported_at,archived
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
            ON CONFLICT(id) DO UPDATE SET
              display_name=excluded.display_name,aliases_json=excluded.aliases_json,
              model_path=excluded.model_path,model_sha256=excluded.model_sha256,
              index_path=excluded.index_path,
              index_sha256=excluded.index_sha256,index_candidates_json=excluded.index_candidates_json,
              rvc_version=excluded.rvc_version,sample_rate=excluded.sample_rate,f0=excluded.f0,
              source_kind=excluded.source_kind,license_spdx=excluded.license_spdx,
              source_url=excluded.source_url,recommended_json=excluded.recommended_json,
              status=excluded.status,imported_at=excluded.imported_at
            """,
            values,
        )

    def list(self) -> list[dict[str, Any]]:
        return self.database.fetch_all("SELECT * FROM models ORDER BY family,checkpoint_epoch,id")

    def mark_catalog(self, model_id: str) -> None:
        self.database.execute("UPDATE models SET source_kind='catalog' WHERE id=?", (model_id,))

    def set_archived(self, model_id: str, archived: bool) -> None:
        self.database.execute("UPDATE models SET archived=? WHERE id=?", (int(archived), model_id))

    def metadata(self) -> dict[str, dict[str, Any]]:
        rows = self.database.fetch_all("SELECT * FROM model_user_metadata")
        results = {}
        for row in rows:
            decoded = Database.decode_json_row(row, ("tags_json",))
            decoded["favorite"] = bool(decoded["favorite"])
            results[str(decoded["model_id"])] = decoded
        return results

    def update_metadata(
        self,
        model_id: str,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.connect() as db:
            row = db.execute(
                "SELECT * FROM model_user_metadata WHERE model_id=?", (model_id,)
            ).fetchone()
            if row is None:
                if expected_revision != 0:
                    raise OperationError(
                        "revision_conflict", "model metadata does not have that revision"
                    )
                current = {
                    "custom_name": None,
                    "tags": [],
                    "favorite": False,
                    "notes": "",
                    "sample_path": None,
                    "cover_path": None,
                }
                updated = {**current, **changes}
                db.execute(
                    "INSERT INTO model_user_metadata("
                    "model_id,custom_name,tags_json,favorite,notes,sample_path,cover_path,"
                    "revision,updated_at) VALUES(?,?,?,?,?,?,?,1,?)",
                    (
                        model_id,
                        updated.get("custom_name") or None,
                        json.dumps(updated.get("tags") or [], ensure_ascii=False),
                        int(bool(updated.get("favorite"))),
                        updated.get("notes") or "",
                        updated.get("sample_path"),
                        updated.get("cover_path"),
                        now,
                    ),
                )
            else:
                decoded = Database.decode_json_row(dict(row), ("tags_json",))
                current_revision = int(decoded["revision"])
                if current_revision != expected_revision:
                    raise OperationError(
                        "revision_conflict",
                        f"model metadata revision changed: expected {expected_revision}, "
                        f"current {current_revision}",
                    )
                updated = {**decoded, **changes}
                db.execute(
                    "UPDATE model_user_metadata SET custom_name=?,tags_json=?,favorite=?,"
                    "notes=?,sample_path=?,cover_path=?,revision=?,updated_at=? "
                    "WHERE model_id=? AND revision=?",
                    (
                        updated.get("custom_name") or None,
                        json.dumps(updated.get("tags") or [], ensure_ascii=False),
                        int(bool(updated.get("favorite"))),
                        updated.get("notes") or "",
                        updated.get("sample_path"),
                        updated.get("cover_path"),
                        current_revision + 1,
                        now,
                        model_id,
                        current_revision,
                    ),
                )
        return self.metadata()[model_id]

    def record_usage(self, model_id: str) -> dict[str, Any]:
        now = utc_now()
        self.database.execute(
            "INSERT INTO model_user_metadata("
            "model_id,custom_name,tags_json,favorite,notes,sample_path,cover_path,"
            "usage_count,last_used_at,revision,updated_at) "
            "VALUES(?,NULL,'[]',0,'',NULL,NULL,1,?,1,?) "
            "ON CONFLICT(model_id) DO UPDATE SET "
            "usage_count=model_user_metadata.usage_count+1,last_used_at=excluded.last_used_at,"
            "updated_at=excluded.updated_at",
            (model_id, now, now),
        )
        return self.metadata()[model_id]

    def set_integrity(
        self,
        model_id: str,
        status: str,
        error: str | None,
    ) -> None:
        now = utc_now()
        self.database.execute(
            "INSERT INTO model_user_metadata("
            "model_id,custom_name,tags_json,favorite,notes,sample_path,cover_path,"
            "integrity_status,integrity_checked_at,integrity_error,revision,updated_at) "
            "VALUES(?,NULL,'[]',0,'',NULL,NULL,?,?,?,1,?) "
            "ON CONFLICT(model_id) DO UPDATE SET "
            "integrity_status=excluded.integrity_status,"
            "integrity_checked_at=excluded.integrity_checked_at,"
            "integrity_error=excluded.integrity_error,updated_at=excluded.updated_at",
            (model_id, status, now, error, now),
        )
