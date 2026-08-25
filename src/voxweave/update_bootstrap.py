from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .file_lock import InterprocessFileLock
from .process_control import start_managed_process, terminate_process_tree


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    with InterprocessFileLock(path.with_suffix(".json.lock")):
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)


def run_update_bootstrap(state_value: str, version: str, token: str) -> int:
    state_path = Path(state_value).resolve()
    state: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
    pending = state.get("pending") or {}
    if pending.get("version") != version or pending.get("token") != token:
        raise ValueError("update activation request is stale")
    record = state["installations"][version]
    target = Path(record["executable_path"]).resolve()
    if not target.is_file():
        raise FileNotFoundError(target)
    child = start_managed_process([str(target), "--voxweave-update-health-token", token])
    marker = state_path.parent / "update-health" / f"{token}.json"
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if marker.is_file():
            previous_version = pending.get("previous_version")
            if previous_version in state["installations"] and previous_version != version:
                state["installations"][previous_version]["state"] = "installed"
            record["state"] = "active"
            state["active_version"] = version
            state["active_executable"] = str(target)
            state.pop("pending", None)
            _write_state(state_path, state)
            return 0
        if child.poll() is not None:
            break
        time.sleep(0.2)
    terminate_process_tree(child)
    record["state"] = "failed"
    record["error"] = "new version did not report healthy startup within 45 seconds"
    previous_version = pending.get("previous_version")
    previous_executable = pending.get("previous_executable")
    if previous_version in state["installations"]:
        state["installations"][previous_version]["state"] = "active"
        state["active_version"] = previous_version
        state["active_executable"] = previous_executable
    state.pop("pending", None)
    _write_state(state_path, state)
    if previous_executable and Path(previous_executable).is_file():
        start_managed_process([str(Path(previous_executable).resolve())])
    return 1
