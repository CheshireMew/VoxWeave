from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .batch_discovery import discover_files, matches_rule, matches_variant
from .batch_output import output_path, plan_outputs, resolve_collision
from .batch_repository import BatchRepository
from .database import Database, utc_now
from .hashing import sha256_file
from .task_manager import TaskManager


class BatchSubmissionService:
    """Owns source discovery, identity checks, output naming and child submission."""

    def __init__(
        self,
        database: Database,
        repository: BatchRepository,
        tasks: TaskManager,
    ) -> None:
        self.database = database
        self.repository = repository
        self.tasks = tasks
        self._lock = threading.RLock()

    def files(
        self,
        rule: dict[str, Any],
        cancelled: Callable[[], bool] | None = None,
    ) -> list[Path]:
        return discover_files(rule, cancelled, scan_directory=os.scandir)

    @staticmethod
    def matches(rule: dict[str, Any], path: Path) -> bool:
        return matches_rule(rule, path)

    def plan(
        self,
        rule: dict[str, Any],
        progress: Callable[[float, str, str], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        return plan_outputs(rule, self.files(rule, cancelled), progress, cancelled)

    def submit_file(
        self,
        rule: dict[str, Any],
        source: Path,
        cancelled: Callable[[], bool] | None = None,
        variant: dict[str, Any] | None = None,
    ) -> str:
        with self._lock:
            variant = variant or list(rule.get("variants") or [])[0]
            if cancelled and cancelled():
                raise InterruptedError("batch submission cancelled")
            before = source.stat()
            source_hash = sha256_file(source, cancelled=cancelled)
            after = source.stat()
            if cancelled and cancelled():
                raise InterruptedError("batch submission cancelled")
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"source changed while hashing: {source}")
            output = output_path(rule, variant, source, source_hash)
            output, overwrite = resolve_collision(rule, output)
            skip_existing = output.exists() and rule.get("collision_policy") == "skip"
            item_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"voxweave:{rule['id']}:{source}:{source_hash}:{variant['name']}",
                )
            )
            now = utc_now()
            arguments = {
                **variant["preset"],
                "input": str(source),
                "input_sha256": source_hash,
                "output": str(output),
                "model": variant["model_id"],
                "overwrite": overwrite,
            }
            created = False
            with self.database.connect() as db:
                self.repository.assert_revision(db, str(rule["id"]), int(rule["revision"]))
                existing = self.repository.find_item(
                    db, rule["id"], str(source), source_hash, variant["name"]
                )
                if existing and (existing["task_id"] or existing["state"] == "skipped"):
                    return str(existing["id"])
                if not existing:
                    self.repository.insert_item(
                        db,
                        (
                            item_id,
                            rule["id"],
                            str(source),
                            after.st_size,
                            after.st_mtime_ns,
                            source_hash,
                            variant["name"],
                            json.dumps(variant, ensure_ascii=False),
                            str(output),
                            "skipped" if skip_existing else "submitting",
                            now,
                            now,
                        ),
                    )
                else:
                    item_id = existing["id"]
                if skip_existing:
                    return str(item_id)
                task_id, created = self.tasks.create_in_transaction(
                    db,
                    "conversion.run",
                    arguments,
                    request_id=f"batch-item:{item_id}",
                    actor={"kind": "batch", "batch_id": rule["id"]},
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
                self.repository.link_item(db, item_id, task_id)
            if created:
                self.tasks.notify_enqueued(task_id)
            return str(item_id)

def submit_source(
    service: BatchSubmissionService,
    rule: dict[str, Any],
    source: Path,
    cancelled: Callable[[], bool] | None = None,
) -> list[str]:
    matching = [
        variant
        for variant in rule.get("variants") or []
        if matches_variant(rule, variant, source)
    ]
    if len(matching) == 1:
        return [service.submit_file(rule, source, cancelled)]
    return [service.submit_file(rule, source, cancelled, variant) for variant in matching]
