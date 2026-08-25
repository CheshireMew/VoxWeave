from __future__ import annotations

import uuid
from typing import Any

from .database import utc_now
from .model_registry import ModelRegistry
from .preset_repository import PresetRepository
from .protocol import ConversionParameters, RealtimeVoiceParameters


class PresetService:
    def __init__(self, models: ModelRegistry, repository: PresetRepository) -> None:
        self.models = models
        self.repository = repository

    def list(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        selector = arguments.get("model")
        model_id = self.models.resolve(selector)["id"] if selector else None
        results = self.repository.list(
            model_id,
            arguments.get("kind"),
            include_archived=bool(arguments.get("include_archived", False)),
        )
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
        kind = str(arguments.get("kind") or "conversion")
        preset_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"voxweave:{model['id']}:{kind}:{name}")
        )
        return self.repository.save(
            preset_id,
            model["id"],
            name,
            kind,
            model["model_sha256"],
            parameters,
            utc_now(),
        )

    def update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        current = self.repository.get(arguments["preset_id"])
        model = self.models.resolve(current["model_id"])
        changes: dict[str, Any] = {"model_sha256": model["model_sha256"]}
        if "name" in arguments:
            changes["name"] = arguments["name"]
        if "parameters" in arguments:
            changes["parameters"] = dict(arguments["parameters"])
        return self.repository.update(
            current["id"], arguments["expected_revision"], changes, utc_now()
        )

    def archive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        current = self.repository.get(arguments["preset_id"])
        return self.repository.update(
            current["id"],
            arguments["expected_revision"],
            {"archived": bool(arguments.get("archived", True))},
            utc_now(),
        )

    def copy(self, arguments: dict[str, Any]) -> dict[str, Any]:
        current = self.repository.get(arguments["preset_id"])
        name = str(arguments["name"]).strip()
        existing = self.repository.list(
            current["model_id"],
            current["kind"],
            include_archived=True,
        )
        if any(str(item["name"]).casefold() == name.casefold() for item in existing):
            raise ValueError(f"preset name already exists: {name}")
        preset_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"voxweave:{current['model_id']}:{current['kind']}:{name}",
            )
        )
        return self.repository.save(
            preset_id,
            current["model_id"],
            name,
            current["kind"],
            current["model_sha256"],
            dict(current["parameters"]),
            utc_now(),
        )

    def export(self, arguments: dict[str, Any]) -> dict[str, Any]:
        records = [self.repository.get(preset_id) for preset_id in arguments["preset_ids"]]
        return {
            "protocol": "voxweave-preset-bundle",
            "version": 1,
            "presets": [
                {
                    "model_id": record["model_id"],
                    "model_sha256": record["model_sha256"],
                    "name": record["name"],
                    "kind": record["kind"],
                    "parameters": record["parameters"],
                }
                for record in records
            ],
        }

    def import_bundle(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        imported = []
        for item in arguments["presets"]:
            model = self.models.resolve(item["model_id"])
            name = str(item["name"]).strip()
            kind = str(item["kind"])
            parameter_type = (
                RealtimeVoiceParameters if kind == "realtime" else ConversionParameters
            )
            parameters = parameter_type.model_validate(item["parameters"]).model_dump(
                mode="json"
            )
            preset_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"voxweave:{model['id']}:{kind}:{name}")
            )
            imported.append(
                self.repository.save(
                    preset_id,
                    model["id"],
                    name,
                    kind,
                    item["model_sha256"],
                    parameters,
                    utc_now(),
                )
            )
        models = {model["id"]: model for model in self.models.list_models()}
        for result in imported:
            model = models.get(result["model_id"])
            result["needs_reconfirmation"] = bool(
                not model or model["model_sha256"] != result["model_sha256"]
            )
        return imported
