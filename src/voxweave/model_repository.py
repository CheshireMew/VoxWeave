from __future__ import annotations

from typing import Any

from .database import Database


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
              license_spdx,source_url,recommended_json,status,imported_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              display_name=excluded.display_name,aliases_json=excluded.aliases_json,
              model_path=excluded.model_path,index_path=excluded.index_path,
              index_sha256=excluded.index_sha256,index_candidates_json=excluded.index_candidates_json,
              rvc_version=excluded.rvc_version,sample_rate=excluded.sample_rate,f0=excluded.f0,
              source_kind=excluded.source_kind,license_spdx=excluded.license_spdx,
              source_url=excluded.source_url,recommended_json=excluded.recommended_json,
              status=excluded.status
            """,
            values,
        )

    def list(self) -> list[dict[str, Any]]:
        return self.database.fetch_all(
            "SELECT * FROM models ORDER BY family,checkpoint_epoch,id"
        )

    def mark_catalog(self, model_id: str) -> None:
        self.database.execute(
            "UPDATE models SET source_kind='catalog' WHERE id=?", (model_id,)
        )
