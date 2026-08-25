from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .config import persist_data_root_pointer
from .file_lock import InterprocessFileLock
from .process_control import start_managed_process
from .storage import _copy_tree_verified, _tree_records


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _wait_for_service(source: Path, timeout: float = 60.0) -> InterprocessFileLock:
    lock = InterprocessFileLock(source / "state" / "service.lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock.acquire()
            return lock
        except RuntimeError as error:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "VoxWeave background service did not stop for migration"
                ) from error
            time.sleep(0.2)


def _is_transient(source: Path, path: Path) -> bool:
    relative = path.relative_to(source)
    return relative.parts in {
        ("state", "service.json"),
        ("state", "service.lock"),
    } or path.name.endswith(("-wal", "-shm"))


def run_migration(manifest_value: str) -> int:
    manifest_path = Path(manifest_value).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("protocol") != "voxweave-storage-migration" or payload.get("version") != 1:
        raise ValueError("unsupported storage migration manifest")
    source = Path(payload["source_root"]).resolve()
    target = Path(payload["target_root"]).resolve()
    pointer = Path(payload["pointer_path"]).resolve()
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.migration-{payload['id']}")
    if staging.exists():
        raise FileExistsError(staging)
    lock = _wait_for_service(source)
    try:
        payload["state"] = "copying"
        _write_manifest(manifest_path, payload)
        _copy_tree_verified(
            source,
            staging,
            lambda: False,
            lambda _fraction, _stage, _detail: None,
            0.0,
            1.0,
            lambda path: _is_transient(source, path),
        )
        source_records = _tree_records(source, lambda path: _is_transient(source, path))
        staging_records = _tree_records(staging)
        if source_records != staging_records:
            raise OSError("storage migration verification failed")
        staging.replace(target)
        target_manifest = target / manifest_path.relative_to(source)
        payload["state"] = "completed"
        payload["completed_at"] = (
            __import__("datetime", fromlist=["datetime"])
            .datetime.now(__import__("datetime", fromlist=["UTC"]).UTC)
            .isoformat()
        )
        _write_manifest(target_manifest, payload)
        database_path = target / "state" / "voxweave.sqlite3"
        with sqlite3.connect(database_path) as db:
            db.execute(
                "UPDATE storage_migrations SET state='completed',updated_at=? WHERE id=?",
                (payload["completed_at"], payload["id"]),
            )
        persist_data_root_pointer(target, pointer)
    except Exception as error:
        payload["state"] = "failed"
        payload["error"] = str(error)
        _write_manifest(manifest_path, payload)
        raise
    finally:
        lock.release()
    start_managed_process(list(payload["application_command"]))
    return 0
