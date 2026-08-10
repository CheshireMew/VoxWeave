from __future__ import annotations

import uuid
from typing import Any

from .database import utc_now
from .model_registry import ModelRegistry
from .preset_repository import PresetRepository


class PresetService:
    def __init__(self, models: ModelRegistry, repository: PresetRepository) -> None:
        self.models = models
        self.repository = repository

    def list(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        selector = arguments.get("model")
        model_id = self.models.resolve(selector)["id"] if selector else None
        results = self.repository.list(model_id)
        models = {model["id"]: model for model in self.models.list_models()}
        for result in results:
            model = models.get(result["model_id"])
            result["needs_reconfirmation"] = (
                not model
                or model["status"] != "ready"
                or model["model_sha256"] != result["model_sha256"]
            )
        return results

    def save(self, arguments: dict[str, Any]) -> dict[str, Any]:
        model = self.models.resolve(arguments["model"])
        name = str(arguments["name"]).strip()
        parameters = dict(arguments["parameters"])
        preset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"voxweave:{model['id']}:{name}"))
        return self.repository.save(
            preset_id,
            model["id"],
            name,
            model["model_sha256"],
            parameters,
            utc_now(),
        )
