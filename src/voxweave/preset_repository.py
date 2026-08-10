from __future__ import annotations

import json
from typing import Any

from .database import Database


class PresetRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        return Database.decode_json_row(row, ("parameters_json",))

    def list(self, model_id: str | None) -> list[dict[str, Any]]:
        if model_id:
            rows = self.database.fetch_all(
                "SELECT * FROM presets WHERE model_id=? ORDER BY name", (model_id,)
            )
        else:
            rows = self.database.fetch_all("SELECT * FROM presets ORDER BY model_id,name")
        return [self._decode(row) for row in rows]

    def save(
        self,
        preset_id: str,
        model_id: str,
        name: str,
        model_sha256: str,
        parameters: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        self.database.execute(
            "INSERT INTO presets("
            "id,model_id,name,model_sha256,parameters_json,created_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(model_id,name) DO UPDATE SET "
            "model_sha256=excluded.model_sha256,parameters_json=excluded.parameters_json",
            (
                preset_id,
                model_id,
                name,
                model_sha256,
                json.dumps(parameters, ensure_ascii=False),
                created_at,
            ),
        )
        row = self.database.fetch_one("SELECT * FROM presets WHERE id=?", (preset_id,))
        if row is None:
            raise RuntimeError(f"preset was not persisted: {preset_id}")
        return self._decode(row)
