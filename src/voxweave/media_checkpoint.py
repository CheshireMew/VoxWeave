from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .hashing import sha256_file


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _verified_checkpoint_file(record: dict[str, Any] | None) -> Path | None:
    if not record or not record.get("path") or not record.get("sha256"):
        return None
    path = Path(record["path"])
    if not path.is_file() or path.stat().st_size != int(record.get("size_bytes", -1)):
        return None
    return path if sha256_file(path) == record["sha256"] else None


def _file_matches_record(path: Path, record: dict[str, Any] | None) -> bool:
    return bool(
        record
        and path.is_file()
        and path.stat().st_size == int(record.get("size_bytes", -1))
        and sha256_file(path) == record.get("sha256")
    )


def _write_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _publish_prepared_output(
    prepared_output: Path,
    output: Path,
    *,
    overwrite: bool,
    cancelled: Callable[[], bool],
    checkpoint: dict[str, Any],
    checkpoint_path: Path,
    result: dict[str, Any],
) -> None:
    checkpoint["stages"]["publication"] = {
        "state": "prepared",
        "prepared_output": _file_record(prepared_output),
        "result": result,
    }
    _write_checkpoint(checkpoint_path, checkpoint)
    if cancelled():
        raise InterruptedError("task cancellation requested")
    if output.exists() and not overwrite:
        raise FileExistsError(output)
    prepared_output.replace(output)
    checkpoint["stages"]["publication"] = {
        "state": "published",
        "prepared_output": _file_record(output),
        "result": result,
    }
    _write_checkpoint(checkpoint_path, checkpoint)


def _load_resume_checkpoint(
    settings: Settings, previous_task: str | None, signature: dict[str, Any]
) -> dict[str, Any] | None:
    if not previous_task:
        return None
    path = settings.artifacts_dir / str(previous_task) / "checkpoint.json"
    if not path.is_file():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        checkpoint.get("protocol") != "voxweave-conversion-checkpoint"
        or checkpoint.get("version") != 1
        or checkpoint.get("signature") != signature
    ):
        return None
    return checkpoint
