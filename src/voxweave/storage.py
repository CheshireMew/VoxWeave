from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .config import LOCAL_POINTER, Settings
from .database import Database, utc_now
from .hashing import sha256_file
from .storage_repository import StorageRepository

Progress = Callable[[float, str, str | None], None]
Cancelled = Callable[[], bool]
TERMINAL_TASK_STATES = {"completed", "failed", "cancelled", "interrupted"}


def _tree_summary(root: Path) -> tuple[int, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    return len(files), sum(path.stat().st_size for path in files)


def _tree_records(
    root: Path, exclude: Callable[[Path], bool] | None = None
) -> list[tuple[str, int, str]]:
    records = []
    for path in sorted(
        (
            candidate
            for candidate in root.rglob("*")
            if candidate.is_file() and not (exclude and exclude(candidate))
        ),
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
    exclude: Callable[[Path], bool] | None = None,
) -> None:
    directories = sorted(
        [source, *[path for path in source.rglob("*") if path.is_dir()]],
        key=lambda path: len(path.parts),
    )
    for directory in directories:
        relative = directory.relative_to(source)
        (staging / relative).mkdir(parents=True, exist_ok=False)
    files = [
        path for path in source.rglob("*") if path.is_file() and not (exclude and exclude(path))
    ]
    total_bytes = sum(path.stat().st_size for path in files)
    copied_bytes = 0
    for path in files:
        _copy_file_verified(path, staging / path.relative_to(source), cancelled)
        copied_bytes += path.stat().st_size
        fraction = copied_bytes / total_bytes if total_bytes else 1.0
        progress(base_progress + share * fraction, "archiving", str(path))


class StorageArchiveManager:
    def __init__(self, settings: Settings, database: Database, artifacts: ArtifactStore):
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
            cutoff = datetime.now(UTC) - timedelta(days=int(arguments.get("older_than_days", 30)))
            rows = self.repository.tasks_before(cutoff.isoformat())
        invalid = [row["id"] for row in rows if row["state"] not in TERMINAL_TASK_STATES]
        if invalid:
            raise ValueError(f"only terminal tasks can be archived: {invalid}")
        states = set(arguments.get("states") or TERMINAL_TASK_STATES)
        return [
            row for row in rows if row["operation"] != "storage.archive" and row["state"] in states
        ]

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
                    raise ValueError("cross-volume archive requires confirm_source_removal=true")
                staging = destination.with_name(f".{destination.name}.staging-{archive_task_id}")
                if staging.exists():
                    failed_root = destination_root / "VoxWeave" / "archive-failures"
                    failed_root.mkdir(parents=True, exist_ok=True)
                    staging.replace(failed_root / f"{staging.name}-{uuid.uuid4().hex}")
                _copy_tree_verified(source, staging, cancelled, progress, base_progress, share)
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

    def inspect(
        self,
        arguments: dict[str, Any],
        progress: Progress,
        cancelled: Cancelled,
        _task_id: str,
    ) -> dict[str, Any]:
        areas = {
            "artifacts": self.settings.artifacts_dir,
            "downloads": self.settings.downloads_dir,
            "cache": self.settings.cache_dir,
            "components": self.settings.components_dir,
            "models": self.settings.managed_models_dir,
            "logs": self.settings.logs_dir,
        }
        summaries: dict[str, dict[str, int]] = {}
        total_bytes = 0
        for index, (name, root) in enumerate(areas.items(), start=1):
            files = 0
            size = 0
            if root.exists():
                for path in root.rglob("*"):
                    if cancelled():
                        raise InterruptedError("storage inspection cancelled")
                    if path.is_file():
                        try:
                            size += path.stat().st_size
                            files += 1
                        except (FileNotFoundError, PermissionError, OSError):
                            continue
            summaries[name] = {"files": files, "bytes": size}
            total_bytes += size
            progress(index / (len(areas) + 2), "inspecting_storage", name)
        candidates = self._candidate_tasks(arguments)
        reclaimable_bytes = 0
        for row in candidates:
            root = self.settings.artifacts_dir / str(row["id"])
            if root.is_dir():
                reclaimable_bytes += _tree_summary(root)[1]
        categories: dict[str, dict[str, int]] = {}
        terminal_rows = self.repository.terminal_tasks()
        registered = self.repository.active_artifact_paths()
        for row in terminal_rows:
            root = self.settings.artifacts_dir / str(row["id"])
            category = "results" if row["state"] == "completed" else "failed_runs"
            target = categories.setdefault(category, {"files": 0, "bytes": 0})
            intermediate = categories.setdefault("intermediates", {"files": 0, "bytes": 0})
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    size = path.stat().st_size
                    target["files"] += 1
                    target["bytes"] += size
                    if str(path.resolve()) not in registered:
                        intermediate["files"] += 1
                        intermediate["bytes"] += size
        return {
            "data_root": str(self.settings.root),
            "total_bytes": total_bytes,
            "free_bytes": shutil.disk_usage(self.settings.root).free,
            "areas": summaries,
            "reclaimable_task_count": len(candidates),
            "reclaimable_bytes": reclaimable_bytes,
            "archive_count": self.repository.completed_archive_count(),
            "categories": categories,
            "migrations": self.repository.migrations(),
        }

    def restore(
        self,
        arguments: dict[str, Any],
        progress: Progress,
        cancelled: Cancelled,
        task_id: str,
    ) -> dict[str, Any]:
        requested = list(arguments["task_ids"])
        archives = self.repository.completed_archives(requested)
        found = {row["task_id"] for row in archives}
        missing = sorted(set(requested) - found)
        if missing:
            raise LookupError(f"completed archives not found: {missing}")
        restored = []
        for index, record in enumerate(archives, start=1):
            if cancelled():
                raise InterruptedError("storage restore cancelled")
            source = Path(record["archive_path"]).resolve()
            destination = Path(record["source_path"]).resolve()
            if not source.is_dir():
                raise FileNotFoundError(source)
            if destination.exists():
                if _tree_records(source) != _tree_records(destination):
                    raise FileExistsError(f"restore destination conflicts: {destination}")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                staging = destination.with_name(f".{destination.name}.restore-{task_id}")
                if staging.exists():
                    raise FileExistsError(staging)
                _copy_tree_verified(
                    source,
                    staging,
                    cancelled,
                    progress,
                    (index - 1) / max(1, len(archives)),
                    1 / max(1, len(archives)),
                )
                staging.replace(destination)
            if _tree_records(source) != _tree_records(destination):
                raise OSError(f"restored artifact verification failed: {destination}")
            self.artifacts.mark_restored(destination, source)
            restored.append(self.repository.mark_restored(str(record["task_id"])))
        return {
            "requested_count": len(requested),
            "restored_count": len(restored),
            "archives": restored,
        }

    def migration_plan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        source = self.settings.root.resolve()
        target = Path(arguments["target_root"]).expanduser().resolve()
        if target == source or source in target.parents or target in source.parents:
            raise ValueError("data migration target must be outside the current data root")
        conflicts = []
        if target.exists():
            conflicts.append("target directory already exists")
        probe = target if target.exists() else target.parent
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        records = [
            (str(path.relative_to(source)), path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(source.rglob("*"), key=lambda value: str(value).casefold())
            if path.is_file()
            and path not in {self.settings.discovery_path, self.settings.lock_path}
            and not path.name.endswith(("-wal", "-shm"))
        ]
        total_bytes = sum(item[1] for item in records)
        payload = json.dumps(
            {"source": str(source), "target": str(target), "files": records},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "source_root": str(source),
            "target_root": str(target),
            "file_count": len(records),
            "total_bytes": total_bytes,
            "free_bytes": shutil.disk_usage(probe).free,
            "plan_digest": hashlib.sha256(payload).hexdigest(),
            "conflicts": conflicts,
        }

    def prepare_migration(self, arguments: dict[str, Any]) -> dict[str, Any]:
        plan = self.migration_plan(arguments)
        if plan["plan_digest"] != arguments["plan_digest"]:
            raise ValueError("storage migration plan changed; inspect it again")
        if plan["conflicts"]:
            raise ValueError("; ".join(plan["conflicts"]))
        migration_id = str(uuid.uuid4())
        manifest_dir = self.settings.state_dir / "storage-migrations"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest = manifest_dir / f"{migration_id}.json"
        payload = {
            "protocol": "voxweave-storage-migration",
            "version": 1,
            "id": migration_id,
            **plan,
            "state": "prepared",
            "created_at": utc_now(),
            "pointer_path": str(LOCAL_POINTER),
            "application_command": [
                sys.executable,
                *([] if getattr(sys, "frozen", False) else ["-m", "voxweave.app"]),
            ],
        }
        temporary = manifest.with_name(f".{manifest.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(manifest)
        self.repository.create_migration(
            migration_id,
            plan["source_root"],
            plan["target_root"],
            plan["plan_digest"],
            str(manifest),
        )
        command = [
            sys.executable,
            *([] if getattr(sys, "frozen", False) else ["-m", "voxweave.app"]),
            "--voxweave-storage-migrate",
            str(manifest),
        ]
        return {
            **plan,
            "migration_id": migration_id,
            "state": "prepared",
            "manifest_path": str(manifest),
            "bootstrap_command": command,
        }
