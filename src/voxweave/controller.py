from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .batch import BatchManager
from .config import Settings
from .database import Database, utc_now
from .hashing import sha256_file
from .media_pipeline import MediaPipeline
from .model_registry import ModelRegistry
from .protocol import OPERATIONS, describe, validate_arguments
from .runtime import inspect_runtime, install_runtime
from .task_manager import TaskManager


class Controller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_layout()
        self.database = Database(settings.database_path)
        self.models = ModelRegistry(self.database, settings)
        self.tasks = TaskManager(self.database)
        self.media = MediaPipeline(settings, self.models)
        self.batch = BatchManager(self.database, self.tasks)
        self.tasks.register(
            "runtime.install",
            lambda args, progress, _cancelled: install_runtime(settings, args, progress),
        )
        self.tasks.register(
            "model.catalog.install",
            lambda args, progress, cancelled: self._catalog_install(args, progress, cancelled),
        )
        self.tasks.register(
            "model.import",
            lambda args, progress, cancelled: self.models.import_model(args, progress, cancelled),
        )
        self.tasks.register("media.analyze", self.media.analyze)
        self.tasks.register("conversion.preview", self.media.preview)
        self.tasks.register("conversion.run", self.media.convert)

    def _catalog_install(
        self, arguments: dict[str, Any], progress: Any, cancelled: Any
    ) -> dict[str, Any]:
        progress(0.1, "download", "reading model catalog")
        result = self.models.install_from_catalog(
            arguments["catalog_url"], arguments["model_id"], progress, cancelled
        )
        progress(1.0, "completed", result["display_name"])
        return result

    def _preset_list(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        selector = arguments.get("model")
        if selector:
            model_id = self.models.resolve(selector)["id"]
            rows = self.database.fetch_all(
                "SELECT * FROM presets WHERE model_id=? ORDER BY name", (model_id,)
            )
        else:
            rows = self.database.fetch_all("SELECT * FROM presets ORDER BY model_id,name")
        results = [Database.decode_json_row(row, ("parameters_json",)) for row in rows]
        models = {model["id"]: model for model in self.models.list_models()}
        actual_hashes: dict[str, str | None] = {}
        for result in results:
            model = models.get(result["model_id"])
            if model and model["id"] not in actual_hashes:
                path = Path(model["model_path"])
                actual_hashes[model["id"]] = sha256_file(path) if path.is_file() else None
            result["needs_reconfirmation"] = (
                not model or actual_hashes.get(result["model_id"]) != result["model_sha256"]
            )
        return results

    def _preset_save(self, arguments: dict[str, Any]) -> dict[str, Any]:
        model = self.models.resolve(arguments["model"])
        name = str(arguments["name"]).strip()
        if not name:
            raise ValueError("preset name is required")
        parameters = dict(arguments["parameters"])
        allowed = {"pitch", "f0", "index_rate", "rms_mix_rate", "protect", "content_mode"}
        unknown = set(parameters) - allowed
        if unknown:
            raise ValueError(f"unsupported preset parameters: {sorted(unknown)}")
        preset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"voxweave:{model['id']}:{name}"))
        now = utc_now()
        self.database.execute(
            "INSERT INTO presets("
            "id,model_id,name,model_sha256,parameters_json,created_at) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(model_id,name) DO UPDATE SET "
            "model_sha256=excluded.model_sha256,parameters_json=excluded.parameters_json",
            (
                preset_id,
                model["id"],
                name,
                model["model_sha256"],
                json.dumps(parameters, ensure_ascii=False),
                now,
            ),
        )
        row = self.database.fetch_one("SELECT * FROM presets WHERE id=?", (preset_id,)) or {}
        return Database.decode_json_row(row, ("parameters_json",))

    def execute(self, operation: str, arguments: dict[str, Any]) -> Any:
        validate_arguments(operation, arguments)
        if OPERATIONS[operation]["long_running"]:
            return self.tasks.submit(operation, arguments)
        if operation == "runtime.inspect":
            return inspect_runtime(self.settings)
        if operation == "model.scan":
            return self.models.scan(arguments.get("weight_roots"), arguments.get("index_roots"))
        if operation == "model.list":
            return self.models.list_models()
        if operation == "model.resolve":
            return self.models.resolve(arguments["voice"])
        if operation == "preset.list":
            return self._preset_list(arguments)
        if operation == "preset.save":
            return self._preset_save(arguments)
        if operation == "media.inspect":
            return self.media.inspect(arguments)
        if operation == "batch.create":
            return self.batch.create(arguments)
        if operation == "batch.run":
            return self.batch.run(arguments["batch_id"])
        if operation == "batch.watch":
            return self.batch.set_watch(arguments["batch_id"], bool(arguments["enabled"]))
        if operation == "task.list":
            return self.tasks.list()
        if operation == "task.get":
            return self.tasks.get(arguments["task_id"])
        if operation == "task.cancel":
            return self.tasks.cancel(arguments["task_id"])
        if operation == "task.retry":
            return self.batch.retry_task(arguments["task_id"])
        raise LookupError(f"operation is described but not dispatched: {operation}")

    def describe(self) -> dict[str, Any]:
        payload = describe()
        payload["runtime"] = {
            "configured": bool(self.settings.rvc_root and self.settings.rvc_python),
            "rvc_root": self.settings.rvc_root,
            "hardware_backend": self.settings.hardware_backend,
            "inspection_operation": "runtime.inspect",
        }
        return payload

    def shutdown(self) -> None:
        self.batch.shutdown()
        self.tasks.shutdown()
