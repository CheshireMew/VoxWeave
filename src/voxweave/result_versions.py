from __future__ import annotations

import json
import uuid
from typing import Any

from .database import Database, utc_now
from .pagination import decode_cursor, encode_cursor


class ResultVersionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        result = Database.decode_json_row(
            row,
            (
                "model_json",
                "parameters_json",
                "result_json",
                "rerun_arguments_json",
                "differences_json",
            ),
        )
        result["favorite"] = bool(result["favorite"])
        return result

    @staticmethod
    def _differences(parent: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        comparisons = {
            "model": (parent.get("model"), result.get("model")),
            "parameters": (parent.get("parameters"), result.get("parameters")),
            "project_revision": (
                (parent.get("project") or {}).get("revision"),
                (result.get("project") or {}).get("revision"),
            ),
            "output_sha256": (
                (parent.get("output") or {}).get("sha256"),
                (result.get("output") or {}).get("sha256"),
            ),
        }
        for name, (before, after) in comparisons.items():
            if before != after:
                changes[name] = {"before": before, "after": after}
        return changes

    def record(
        self,
        task_id: str,
        result: dict[str, Any],
        rerun_arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rerun_arguments = dict(rerun_arguments or {})
        version_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"voxweave:result:{task_id}"))
        project = result.get("project") or {}
        parent_id = rerun_arguments.get("parent_version_id")
        parent = self.get(parent_id) if parent_id else None
        if parent is None and project.get("id"):
            row = self.database.fetch_one(
                "SELECT * FROM result_versions WHERE project_id=? "
                "ORDER BY generation DESC,created_at DESC,id DESC LIMIT 1",
                (project["id"],),
            )
            parent = self._decode(row) if row else None
            parent_id = parent["id"] if parent else None
        root_id = str(parent["root_id"]) if parent else version_id
        generation = int(parent["generation"]) + 1 if parent else 1
        differences = self._differences(parent["result"], result) if parent else {}
        persisted_arguments = {
            key: value
            for key, value in rerun_arguments.items()
            if key != "parent_version_id"
        }
        self.database.execute(
            "INSERT INTO result_versions("
            "id,task_id,project_id,project_revision,input_path,input_sha256,output_path,"
            "output_sha256,model_json,parameters_json,result_json,parent_id,root_id,generation,"
            "rerun_arguments_json,differences_json,label,favorite,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(task_id) DO UPDATE SET "
            "result_json=excluded.result_json",
            (
                version_id,
                task_id,
                project.get("id"),
                project.get("revision"),
                result["input"]["path"],
                result["input"]["sha256"],
                result["output"]["path"],
                result["output"]["sha256"],
                json.dumps(result["model"], ensure_ascii=False),
                json.dumps(result["parameters"], ensure_ascii=False),
                json.dumps(result, ensure_ascii=False),
                parent_id,
                root_id,
                generation,
                json.dumps(persisted_arguments, ensure_ascii=False),
                json.dumps(differences, ensure_ascii=False),
                "",
                0,
                utc_now(),
            ),
        )
        return self.get(version_id)

    def get(self, version_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM result_versions WHERE id=?", (version_id,))
        if row is None:
            raise LookupError(f"result version not found: {version_id}")
        result = self._decode(row)
        children = self.database.fetch_all(
            "SELECT id FROM result_versions WHERE parent_id=? ORDER BY generation,id",
            (version_id,),
        )
        result["children"] = [str(item["id"]) for item in children]
        return result

    def list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        clauses = []
        parameters: list[Any] = []
        for key, column in (("input_sha256", "input_sha256"), ("project_id", "project_id")):
            if arguments.get(key):
                clauses.append(f"{column}=?")
                parameters.append(arguments[key])
        if arguments.get("favorites_only"):
            clauses.append("favorite=1")
        if arguments.get("cursor"):
            created_at, version_id = decode_cursor(arguments["cursor"], resource="result")
            clauses.append("(created_at<? OR (created_at=? AND id<?))")
            parameters.extend((created_at, created_at, version_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = int(arguments.get("limit", 100))
        parameters.append(limit + 1)
        rows = self.database.fetch_all(
            f"SELECT * FROM result_versions {where} "  # noqa: S608
            "ORDER BY created_at DESC,id DESC LIMIT ?",
            tuple(parameters),
        )
        items = []
        for row in rows[:limit]:
            item = self._decode(row)
            children = self.database.fetch_all(
                "SELECT id FROM result_versions WHERE parent_id=? ORDER BY generation,id",
                (item["id"],),
            )
            item["children"] = [str(child["id"]) for child in children]
            items.append(item)
        next_cursor = None
        if len(rows) > limit and items:
            next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"])
        return {"items": items, "next_cursor": next_cursor}

    def update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        current = self.get(arguments["version_id"])
        label = arguments.get("label", current["label"])
        favorite = arguments.get("favorite", current["favorite"])
        self.database.execute(
            "UPDATE result_versions SET label=?,favorite=? WHERE id=?",
            (label, int(bool(favorite)), current["id"]),
        )
        return self.get(current["id"])
