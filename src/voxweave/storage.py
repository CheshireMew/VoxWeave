from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .config import Settings
from .database import Database
from .hashing import sha256_file
from .storage_repository import StorageRepository

Progress = Callable[[float, str, str | None], None]
Cancelled = Callable[[], bool]
TERMINAL_TASK_STATES = {"completed", "failed", "cancelled", "interrupted"}


def _tree_summary(root: Path) -> tuple[int, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return len(files), sum(path.stat().st_size for path in files)


def _tree_records(root: Path) -> list[tuple[str, int, str]]:
    records = []
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda candidate: str(candidate.relative_to(root)).casefold(),
    ):
        records.append(
            (
                str(path.relative_to(root)),
                path.stat().st_size,
                sha256_file(path),
            )
        )
    return records


def _copy_file_verified(source: Path, destination: Path, cancelled: Cancelled) -> None:
    source_hash = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            if cancelled():
                raise InterruptedError("storage archive cancellation requested")
            source_hash.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(source, destination)
    destination_hash = hashlib.sha256()
    with destination.open("rb") as reader:
        while chunk := reader.read(1024 * 1024):
            if cancelled():
                raise InterruptedError("storage archive cancellation requested")
            destination_hash.update(chunk)
    if source_hash.digest() != destination_hash.digest():
        raise OSError(f"archive verification failed: {source}")


def _copy_tree_verified(
    source: Path,
    staging: Path,
    cancelled: Cancelled,
    progress: Progress,
    base_progress: float,
    share: float,
) -> None:
    directories = sorted(
        [source, *[path for path in source.rglob("*") if path.is_dir()]],
        key=lambda path: len(path.parts),
    )
    for directory in directories:
        relative = directory.relative_to(source)
        (staging / relative).mkdir(parents=True, exist_ok=False)
    files = [path for path in source.rglob("*") if path.is_file()]
    total_bytes = sum(path.stat().st_size for path in files)
    copied_bytes = 0
    for path in files:
        _copy_file_verified(path, staging / path.relative_to(source), cancelled)
        copied_bytes += path.stat().st_size
        fraction = copied_bytes / total_bytes if total_bytes else 1.0
        progress(base_progress + share * fraction, "archiving", str(path))


class StorageArchiveManager:
    def __init__(
        self, settings: Settings, database: Database, artifacts: ArtifactStore
    ):
        self.settings = settings
        self.database = database
        self.repository = StorageRepository(database)
        self.artifacts = artifacts

    def _candidate_tasks(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        requested = arguments.get("task_ids")
        if requested:
            rows = self.repository.tasks_by_ids(requested)
            found = {row["id"] for row in rows}
            missing = sorted(set(requested) - found)
            if missing:
                raise LookupError(f"tasks not found: {missing}")
        else:
            cutoff = datetime.now(UTC) - timedelta(
                days=int(arguments.get("older_than_days", 30))
            )
            rows = self.repository.tasks_before(cutoff.isoformat())
        invalid = [row["id"] for row in rows if row["state"] not in TERMINAL_TASK_STATES]
        if invalid:
            raise ValueError(f"only terminal tasks can be archived: {invalid}")
        return [row for row in rows if row["operation"] != "storage.archive"]

    def _move_one(
        self,
        row: dict[str, Any],
        destination_root: Path,
        archive_task_id: str,
        confirm_source_removal: bool,
        cancelled: Cancelled,
        progress: Progress,
        base_progress: float,
        share: float,
    ) -> dict[str, Any] | None:
        task_id = row["id"]
        source = (self.settings.artifacts_dir / task_id).resolve()
        destination = (destination_root / "VoxWeave" / "artifacts" / task_id).resolve()
        existing = self.repository.archive(task_id)
        if existing:
            source = Path(existing["source_path"])
            destination = Path(existing["archive_path"])
            if existing["state"] == "completed":
                return existing
        if not source.exists() and not destination.exists():
            return None
        if destination.exists() and source.exists():
            if _tree_records(source) != _tree_records(destination):
                raise FileExistsError(f"archive destination conflicts: {destination}")
        elif not destination.exists() and source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            file_count, size_bytes = _tree_summary(source)
            self.repository.plan_archive(
                task_id,
                str(source),
                str(destination),
                file_count,
                size_bytes,
            )
            if source.stat().st_dev == destination.parent.stat().st_dev:
                if cancelled():
                    raise InterruptedError("storage archive cancellation requested")
                source.replace(destination)
            else:
                if not confirm_source_removal:
                    raise ValueError(
                        "cross-volume archive requires confirm_source_removal=true"
                    )
                staging = destination.with_name(
                    f".{destination.name}.staging-{archive_task_id}"
                )
                if staging.exists():
                    failed_root = destination_root / "VoxWeave" / "archive-failures"
                    failed_root.mkdir(parents=True, exist_ok=True)
                    staging.replace(
                        failed_root / f"{staging.name}-{uuid.uuid4().hex}"
                    )
                _copy_tree_verified(
                    source, staging, cancelled, progress, base_progress, share
                )
                staging.replace(destination)
        if not destination.exists():
            raise FileNotFoundError(destination)
        self.repository.mark_moved(task_id)
        self.artifacts.mark_archived(source, destination)
        if source.exists():
            if not confirm_source_removal:
                raise ValueError("source removal was not confirmed")
            shutil.rmtree(source)
        return self.repository.complete(task_id)

    def archive(
        self,
        arguments: dict[str, Any],
        progress: Progress,
        cancelled: Cancelled,
        task_id: str,
    ) -> dict[str, Any]:
        destination_root = Path(arguments["destination_root"]).expanduser().resolve()
        artifacts_root = self.settings.artifacts_dir.resolve()
        if destination_root == artifacts_root or artifacts_root in destination_root.parents:
            raise ValueError("archive destination must be outside the active artifacts directory")
        destination_root.mkdir(parents=True, exist_ok=True)
        candidates = self._candidate_tasks(arguments)
        archived: list[dict[str, Any]] = []
        total = len(candidates)
        for index, row in enumerate(candidates):
            if cancelled():
                raise InterruptedError("storage archive cancellation requested")
            base_progress = 0.05 + 0.9 * (index / total) if total else 0.95
            share = 0.9 / total if total else 0.0
            progress(base_progress, "archiving", row["id"])
            result = self._move_one(
                row,
                destination_root,
                task_id,
                bool(arguments["confirm_source_removal"]),
                cancelled,
                progress,
                base_progress,
                share,
            )
            if result:
                archived.append(result)
        progress(0.98, "verifying", f"archived {len(archived)} task directories")
        return {
            "destination_root": str(destination_root),
            "candidate_count": total,
            "archived_count": len(archived),
            "archives": archived,
        }
