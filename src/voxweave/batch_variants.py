from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


def filesystem_slug(value: str) -> str:
    normalized = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "-", value.strip())
    return re.sub(r"[-\s]+", "-", normalized).strip("-. ") or "default"


class BatchVariantService:
    """Resolves user-facing batch variants into immutable model snapshots."""

    def __init__(self, resolve_model: Callable[[str], dict[str, Any]]) -> None:
        self.resolve_model = resolve_model

    def resolve_many(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        source_variants = list(arguments.get("variants") or [])
        if not source_variants and arguments.get("model"):
            source_variants = [
                {
                    "name": "default",
                    "model": arguments["model"],
                    "preset": arguments.get("preset") or {},
                    "preset_name": arguments.get("preset_name") or "default",
                    "output_format": arguments.get("output_format") or "auto",
                }
            ]
        results = []
        for variant in source_variants:
            model = self.resolve_model(variant["model"])
            results.append(
                {
                    "name": filesystem_slug(variant["name"]),
                    "model_id": model["id"],
                    "model_sha256": model["model_sha256"],
                    "index_sha256": model.get("index_sha256"),
                    "preset": dict(variant.get("preset") or {}),
                    "preset_name": filesystem_slug(variant.get("preset_name") or "default"),
                    "output_format": variant.get("output_format") or "auto",
                    "extensions": list(variant.get("extensions") or []),
                    "include_globs": list(variant.get("include_globs") or []),
                    "exclude_globs": list(variant.get("exclude_globs") or []),
                }
            )
        if not results:
            raise ValueError("batch requires at least one output variant")
        names = [str(result["name"]).casefold() for result in results]
        if len(names) != len(set(names)):
            raise ValueError("batch variant names must be unique")
        return results

    def resolve_one(self, value: dict[str, Any]) -> dict[str, Any]:
        return self.resolve_many({"variants": [value]})[0]
