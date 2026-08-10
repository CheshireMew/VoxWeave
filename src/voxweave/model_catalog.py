from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import Any

from .protocol import ModelImportCommand


class ModelCatalogClient:
    def import_arguments(
        self,
        catalog_url: str,
        model_id: str,
        cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        if not catalog_url.lower().startswith("https://"):
            raise ValueError("catalog URL must use HTTPS")
        if cancelled():
            raise InterruptedError("task cancellation requested")
        with urllib.request.urlopen(catalog_url, timeout=15) as response:
            catalog = json.load(response)
        if cancelled():
            raise InterruptedError("task cancellation requested")
        if catalog.get("protocol") != "voxweave-model-catalog" or catalog.get("version") != 1:
            raise ValueError("unsupported VoxWeave model catalog")
        entry = next(
            (item for item in catalog.get("models", []) if item.get("id") == model_id),
            None,
        )
        if not entry:
            raise LookupError(f"catalog model not found: {model_id}")
        if not entry.get("license_spdx"):
            raise ValueError("catalog model has no SPDX license")
        required = {"model_url", "model_sha256", "model_size_bytes", "display_name"}
        missing = required - set(entry)
        if missing:
            raise ValueError(f"catalog model is missing fields: {sorted(missing)}")
        arguments = {
            "model": entry["model_url"],
            "id": entry["id"],
            "display_name": entry["display_name"],
            "aliases": entry.get("aliases", []),
            "license_spdx": entry["license_spdx"],
            "source_url": entry.get("source_url") or catalog_url,
            "model_sha256": entry["model_sha256"],
            "download_size_bytes": entry["model_size_bytes"],
        }
        if entry.get("index_url"):
            missing_index = {"index_sha256", "index_size_bytes"} - set(entry)
            if missing_index:
                raise ValueError(f"catalog index is missing fields: {sorted(missing_index)}")
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
