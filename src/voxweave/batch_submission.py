from __future__ import annotations

import os
import stat as stat_module
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .batch_repository import BatchRepository
from .batch_rules import filesystem_slug
from .capabilities import VIDEO_EXTENSIONS
from .database import Database, utc_now
from .hashing import sha256_file
from .task_manager import TaskManager

TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", ".download")


def _is_hidden(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    attributes = getattr(path.stat(), "st_file_attributes", 0)
    hidden_flag = getattr(stat_module, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    return bool(attributes & hidden_flag) if os.name == "nt" else False


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
        root = Path(rule["input_root"])
        extensions = {value.casefold() for value in rule["extensions"]}
        files: list[Path] = []
        pending = [root]
        while pending:
            if cancelled and cancelled():
                raise InterruptedError("task cancellation requested")
            current = pending.pop()
            try:
                entries = list(os.scandir(current))
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                continue
            for entry in entries:
                path = Path(entry.path)
                try:
                    if entry.name.startswith(".") or _is_hidden(path):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if rule["recursive"]:
                            pending.append(path)
                    elif (
                        entry.is_file(follow_symlinks=False)
                        and path.suffix.casefold() in extensions
                        and not path.name.casefold().endswith(TEMP_SUFFIXES)
                    ):
                        files.append(path)
                except (FileNotFoundError, PermissionError, OSError):
                    continue
        return sorted(files)

    @staticmethod
    def matches(rule: dict[str, Any], path: Path) -> bool:
        root = Path(rule["input_root"])
        try:
            relative = path.relative_to(root)
        except ValueError:
            return False
        if not rule["recursive"] and len(relative.parts) != 1:
            return False
        if (
            path.suffix.casefold()
            not in {value.casefold() for value in rule["extensions"]}
            or path.name.casefold().endswith(TEMP_SUFFIXES)
        ):
            return False
        current = root
        try:
            for part in relative.parts:
                current /= part
                if _is_hidden(current):
                    return False
            return path.is_file()
        except (FileNotFoundError, PermissionError, OSError):
            return False

    @staticmethod
    def output_path(rule: dict[str, Any], source: Path, source_hash: str) -> Path:
        relative = source.relative_to(Path(rule["input_root"]))
        model_slug = filesystem_slug(rule["model_id"])
        preset_slug = filesystem_slug(rule["preset_name"])
        suffix = source.suffix if source.suffix.casefold() in VIDEO_EXTENSIONS else ".wav"
        source_type = source.suffix.casefold().removeprefix(".")
        name = (
            f"{source.stem}_{source_type}_{model_slug}_{preset_slug}_"
            f"{source_hash[:12]}{suffix}"
        )
        return Path(rule["output_root"]) / relative.parent / name

    def submit_file(
        self,
        rule: dict[str, Any],
        source: Path,
        cancelled: Callable[[], bool] | None = None,
    ) -> str:
        with self._lock:
            if cancelled and cancelled():
                raise InterruptedError("batch submission cancelled")
            before = source.stat()
            source_hash = sha256_file(source, cancelled=cancelled)
            after = source.stat()
            if cancelled and cancelled():
                raise InterruptedError("batch submission cancelled")
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"source changed while hashing: {source}")
            output = self.output_path(rule, source, source_hash)
            item_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"voxweave:{rule['id']}:{source}:{source_hash}",
                )
            )
            now = utc_now()
            arguments = {
                **rule["preset"],
                "input": str(source),
                "input_sha256": source_hash,
                "output": str(output),
                "model": rule["model_id"],
                "overwrite": False,
            }
            created = False
            with self.database.connect() as db:
                self.repository.assert_revision(db, str(rule["id"]), int(rule["revision"]))
                existing = self.repository.find_item(db, rule["id"], str(source), source_hash)
                if existing and existing["task_id"]:
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
                            str(output),
                            "submitting",
                            now,
                            now,
                        ),
                    )
                else:
                    item_id = existing["id"]
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
                            "id": rule["model_id"],
                            "model_sha256": rule["model_sha256"],
                            "index_sha256": rule.get("index_sha256"),
                        },
                    },
                )
                self.repository.link_item(db, item_id, task_id)
            if created:
                self.tasks.notify_enqueued(task_id)
            return str(item_id)
