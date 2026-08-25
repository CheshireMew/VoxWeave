from __future__ import annotations

import json
from typing import Any

from .database import Database


class PresetRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        result = Database.decode_json_row(row, ("parameters_json",))
        result["archived"] = bool(result.get("archived"))
        return result

    def get(self, preset_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM presets WHERE id=?", (preset_id,))
        if row is None:
            raise LookupError(f"preset not found: {preset_id}")
        return self._decode(row)

    def list(
        self,
        model_id: str | None,
        kind: str | None = None,
        *,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = []
        parameters: list[Any] = []
        if model_id:
            clauses.append("model_id=?")
            parameters.append(model_id)
        if kind:
            clauses.append("kind=?")
            parameters.append(kind)
        if not include_archived:
            clauses.append("archived=0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.database.fetch_all(
            f"SELECT * FROM presets {where} ORDER BY model_id,kind,name",  # noqa: S608
            tuple(parameters),
        )
        return [self._decode(row) for row in rows]

    def save(
        self,
        preset_id: str,
        model_id: str,
        name: str,
        kind: str,
        model_sha256: str,
        parameters: dict[str, Any],
        timestamp: str,
    ) -> dict[str, Any]:
        self.database.execute(
            "INSERT INTO presets("
            "id,model_id,name,model_sha256,parameters_json,kind,archived,revision,"
            "created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,0,1,?,?) ON CONFLICT(model_id,name) DO UPDATE SET "
            "model_sha256=excluded.model_sha256,parameters_json=excluded.parameters_json,"
            "kind=excluded.kind,archived=0,revision=presets.revision+1,"
            "updated_at=excluded.updated_at",
            (
                preset_id,
                model_id,
                name,
                model_sha256,
                json.dumps(parameters, ensure_ascii=False),
                kind,
                timestamp,
                timestamp,
            ),
        )
        row = self.database.fetch_one(
            "SELECT * FROM presets WHERE model_id=? AND name=?", (model_id, name)
        )
        if row is None:
            raise RuntimeError(f"preset was not persisted: {preset_id}")
        return self._decode(row)

    def update(
        self,
        preset_id: str,
        expected_revision: int,
        changes: dict[str, Any],
        timestamp: str,
    ) -> dict[str, Any]:
        current = self.get(preset_id)
        if int(current["revision"]) != expected_revision:
            from .protocol import OperationError

            raise OperationError(
                "revision_conflict",
                f"preset revision changed: expected {expected_revision}, "
                f"current {current['revision']}",
            )
        updated = {**current, **changes}
        with self.database.connect() as db:
            cursor = db.execute(
                "UPDATE presets SET name=?,model_sha256=?,parameters_json=?,archived=?,"
                "revision=?,updated_at=? "
                "WHERE id=? AND revision=?",
                (
                    updated["name"],
                    updated["model_sha256"],
                    json.dumps(updated["parameters"], ensure_ascii=False),
                    int(bool(updated["archived"])),
                    expected_revision + 1,
                    timestamp,
                    preset_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                from .protocol import OperationError

                raise OperationError("revision_conflict", "preset changed during update")
        return self.get(preset_id)
