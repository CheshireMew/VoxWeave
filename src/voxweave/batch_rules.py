from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .batch_repository import BatchRepository
from .batch_variants import BatchVariantService
from .capabilities import MEDIA_EXTENSIONS
from .database import utc_now

DEFAULT_EXTENSIONS = list(MEDIA_EXTENSIONS)


class BatchRuleService:
    """Owns validation and persistence of batch rules."""

    def __init__(
        self,
        repository: BatchRepository,
        resolve_model: Callable[[str], dict[str, Any]],
    ) -> None:
        self.repository = repository
        self.variants = BatchVariantService(resolve_model)

    @staticmethod
    def _roots(arguments: dict[str, Any]) -> tuple[Path, Path]:
        input_root = Path(arguments["input_root"]).expanduser().resolve()
        output_root = Path(arguments["output_root"]).expanduser().resolve()
        if not input_root.is_dir():
            raise NotADirectoryError(input_root)
        if output_root == input_root or input_root in output_root.parents:
            raise ValueError("output_root cannot be the input directory or a child of it")
        output_root.mkdir(parents=True, exist_ok=True)
        return input_root, output_root

    def create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        input_root, output_root = self._roots(arguments)
        variants = self.variants.resolve_many(arguments)
        model = variants[0]
        batch_id = str(uuid.uuid4())
        now = utc_now()
        self.repository.create_rule(
            (
                batch_id,
                str(input_root),
                str(output_root),
                model["model_id"],
                model["model_sha256"],
                model["index_sha256"],
                json.dumps(model["preset"], ensure_ascii=False),
                model["preset_name"],
                int(bool(arguments.get("recursive", True))),
                int(bool(arguments.get("watch", False))),
                json.dumps(arguments.get("extensions") or DEFAULT_EXTENSIONS),
                arguments.get("naming_template", "{stem}_{source_ext}_{model}_{preset}_{hash}"),
                int(arguments.get("preserve_structure", True)),
                arguments.get("collision_policy", "skip"),
                model["output_format"],
                json.dumps(model["include_globs"], ensure_ascii=False),
                json.dumps(model["exclude_globs"], ensure_ascii=False),
                json.dumps(variants, ensure_ascii=False),
                "active",
                now,
                now,
            )
        )
        return self.get(batch_id)

    def update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        batch_id = arguments["batch_id"]
        self.get(batch_id)
        input_root, output_root = self._roots(arguments)
        variants = self.variants.resolve_many(arguments)
        model = variants[0]
        self.repository.update_rule(
            batch_id,
            (
                str(input_root),
                str(output_root),
                model["model_id"],
                model["model_sha256"],
                model["index_sha256"],
                json.dumps(model["preset"], ensure_ascii=False),
                model["preset_name"],
                int(bool(arguments.get("recursive", True))),
                int(bool(arguments.get("watch", False))),
                json.dumps(arguments.get("extensions") or DEFAULT_EXTENSIONS),
                arguments.get("naming_template", "{stem}_{source_ext}_{model}_{preset}_{hash}"),
                int(arguments.get("preserve_structure", True)),
                arguments.get("collision_policy", "skip"),
                model["output_format"],
                json.dumps(model["include_globs"], ensure_ascii=False),
                json.dumps(model["exclude_globs"], ensure_ascii=False),
                json.dumps(variants, ensure_ascii=False),
                utc_now(),
            ),
        )
        return self.get(batch_id)

    def archive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        batch_id = arguments["batch_id"]
        self.get(batch_id)
        self.repository.set_archived(batch_id, bool(arguments.get("archived", True)))
        return self.get(batch_id)

    def get(self, batch_id: str) -> dict[str, Any]:
        return self.repository.get(batch_id)

    def list(self, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        return self.repository.list(limit, cursor)

    def set_watch(self, batch_id: str, enabled: bool) -> dict[str, Any]:
        batch = self.get(batch_id)
        if batch["state"] != "active":
            raise ValueError(f"batch rule is not active: {batch_id}")
        self.repository.set_watch(batch_id, enabled)
        return self.get(batch_id)
