from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .batch_output import output_path, resolve_collision
from .batch_repository import BatchRepository
from .batch_variants import BatchVariantService
from .hashing import sha256_file
from .task_manager import TaskManager


class BatchItemRetryService:
    """Reconfigures one terminal batch item and submits its replacement task."""

    def __init__(
        self,
        repository: BatchRepository,
        tasks: TaskManager,
        get_rule: Callable[[str], dict[str, Any]],
        variants: BatchVariantService,
    ) -> None:
        self.repository = repository
        self.tasks = tasks
        self.get_rule = get_rule
        self.variants = variants

    def retry(self, arguments: dict[str, Any]) -> dict[str, Any]:
        item = self.repository.get_item(arguments["item_id"])
        if not item:
            raise LookupError(f"batch item not found: {arguments['item_id']}")
        if item["state"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError(
                "only a failed, cancelled or interrupted batch item can be retried"
            )
        rule = self.get_rule(str(item["batch_id"]))
        variant = self.variants.resolve_one(arguments["variant"])
        source = Path(item["source_path"])
        before = source.stat()
        source_hash = sha256_file(source)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"source changed while hashing: {source}")
        if source_hash != item["source_sha256"]:
            raise RuntimeError("the batch source changed; run the batch again as a new item")
        raw_output = (
            Path(arguments["output"])
            if arguments.get("output")
            else output_path(rule, variant, source, source_hash)
        )
        output, overwrite = resolve_collision(rule, raw_output)
        task_arguments = {
            **variant["preset"],
            "input": str(source),
            "input_sha256": source_hash,
            "output": str(output),
            "model": variant["model_id"],
            "overwrite": overwrite,
        }
        with self.repository.database.connect() as db:
            self.repository.assert_revision(db, str(rule["id"]), int(rule["revision"]))
            self.repository.reconfigure_item(
                db,
                str(item["id"]),
                variant_name=variant["name"],
                variant=variant,
                output_path=str(output),
            )
            task_id, created = self.tasks.create_in_transaction(
                db,
                "conversion.run",
                task_arguments,
                request_id=(
                    f"batch-item-reconfigure:{item['id']}:"
                    f"{variant['name']}:{source_hash}"
                ),
                actor={"kind": "batch-item-retry", "batch_id": rule["id"]},
                snapshot={
                    "input": {
                        "path": str(source),
                        "size_bytes": after.st_size,
                        "modified_ns": after.st_mtime_ns,
                        "sha256": source_hash,
                    },
                    "model": {
                        "id": variant["model_id"],
                        "model_sha256": variant["model_sha256"],
                        "index_sha256": variant.get("index_sha256"),
                    },
                },
            )
            self.repository.link_item(db, str(item["id"]), task_id)
        if created:
            self.tasks.notify_enqueued(task_id)
        return {
            "batch": rule,
            "tasks": [{"id": task_id, "item_id": item["id"], "state": "queued"}],
            "failures": [],
        }
