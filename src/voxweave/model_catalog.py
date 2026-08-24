from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .capabilities import CATALOG_PROTOCOL, CATALOG_PROTOCOL_VERSION
from .config import PACKAGE_ROOT
from .protocol import ModelImportCommand


class ModelCatalogClient:
    def __init__(self, bundled_path: Path | None = None) -> None:
        self.bundled_path = bundled_path or (
            PACKAGE_ROOT / "resources" / "catalog.v1.json"
        )

    @staticmethod
    def _validate(catalog: dict[str, Any]) -> dict[str, Any]:
        if (
            catalog.get("protocol") != CATALOG_PROTOCOL
            or catalog.get("version") != CATALOG_PROTOCOL_VERSION
        ):
            raise ValueError("unsupported VoxWeave model catalog")
        models = catalog.get("models")
        if not isinstance(models, list):
            raise ValueError("catalog models must be a list")
        if not models:
            raise ValueError("catalog must contain at least one model")
        validated: list[dict[str, Any]] = []
        identities: set[str] = set()
        for index, raw_entry in enumerate(models):
            if not isinstance(raw_entry, dict):
                raise ValueError(f"catalog model {index} must be an object")
            entry = dict(raw_entry)
            model_id = str(entry.get("id") or "").strip()
            if not model_id:
                raise ValueError(f"catalog model {index} has no id")
            if model_id in identities:
                raise ValueError(f"catalog model id is duplicated: {model_id}")
            identities.add(model_id)
            arguments = ModelCatalogClient._import_arguments(entry, None)
            if int(arguments["download_size_bytes"]) <= 0:
                raise ValueError(f"catalog model has no download size: {model_id}")
            validated.append(entry)
        catalog["models"] = validated
        return catalog

    @staticmethod
    def _import_arguments(
        entry: dict[str, Any], catalog_url: str | None
    ) -> dict[str, Any]:
        model_id = str(entry.get("id") or "").strip()
        if not entry.get("license_spdx"):
            raise ValueError(f"catalog model has no SPDX license: {model_id}")
        required = {
            "id",
            "model_url",
            "model_sha256",
            "model_size_bytes",
            "display_name",
        }
        missing = required - {key for key, value in entry.items() if value not in (None, "")}
        if missing:
            raise ValueError(
                f"catalog model is missing fields: {sorted(missing)} ({model_id})"
            )
        arguments = {
            "model": entry["model_url"],
            "id": entry["id"],
            "display_name": entry["display_name"],
            "aliases": entry.get("aliases", []),
            "license_spdx": entry["license_spdx"],
            "source_url": entry.get("source_url") or catalog_url,
            "model_sha256": entry["model_sha256"],
            "download_size_bytes": entry["model_size_bytes"],
            "recommended": entry.get("recommended"),
        }
        if entry.get("index_url"):
            missing_index = {
                "index_sha256",
                "index_size_bytes",
            } - {key for key, value in entry.items() if value not in (None, "")}
            if missing_index:
                raise ValueError(
                    f"catalog index is missing fields: {sorted(missing_index)} ({model_id})"
                )
            arguments.update(
                {
                    "index_url": entry["index_url"],
                    "index_sha256": entry["index_sha256"],
                    "index_size_bytes": entry["index_size_bytes"],
                }
            )
        return ModelImportCommand.model_validate(arguments).model_dump(
            mode="json",
            exclude_none=True,
        )

    def load(
        self,
        catalog_url: str | None,
        cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        if cancelled():
            raise InterruptedError("task cancellation requested")
        if catalog_url:
            if not catalog_url.lower().startswith("https://"):
                raise ValueError("catalog URL must use HTTPS")
            with urllib.request.urlopen(catalog_url, timeout=15) as response:
                catalog = json.load(response)
        else:
            catalog = json.loads(self.bundled_path.read_text(encoding="utf-8"))
        if cancelled():
            raise InterruptedError("task cancellation requested")
        return self._validate(catalog)

    def list_entries(self) -> list[dict[str, Any]]:
        catalog = self.load(None, lambda: False)
        return [dict(item) for item in catalog["models"]]

    def import_arguments(
        self,
        catalog_url: str | None,
        model_id: str,
        cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        catalog = self.load(catalog_url, cancelled)
        entry = next(
            (item for item in catalog.get("models", []) if item.get("id") == model_id),
            None,
        )
        if not entry:
            raise LookupError(f"catalog model not found: {model_id}")
        return self._import_arguments(dict(entry), catalog_url)
