from __future__ import annotations

import json
import os
import re
import stat as stat_module
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .database import Database, utc_now
from .hashing import sha256_file
from .task_manager import TaskManager

DEFAULT_EXTENSIONS = [".wav", ".flac", ".mp3", ".m4a", ".aac", ".mp4", ".mkv", ".mov", ".webm"]
TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", ".download")


def _slug(value: str) -> str:
    normalized = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", "-", value.strip())
    return re.sub(r"[-\s]+", "-", normalized).strip("-. ") or "default"


def _is_hidden(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    attributes = getattr(path.stat(), "st_file_attributes", 0)
    hidden_flag = getattr(stat_module, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    return bool(attributes & hidden_flag) if os.name == "nt" else False


class BatchManager:
    def __init__(self, database: Database, tasks: TaskManager):
        self.database = database
        self.tasks = tasks
        self.stop_event = threading.Event()
        self.observed: dict[tuple[str, str], tuple[int, float]] = {}
        self.thread = threading.Thread(
            target=self._watch_loop, name="voxweave-batch-watch", daemon=True
        )
        self.thread.start()

    def create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        input_root = Path(arguments["input_root"]).expanduser().resolve()
        output_root = Path(arguments["output_root"]).expanduser().resolve()
        if not input_root.is_dir():
            raise NotADirectoryError(input_root)
        if output_root == input_root or input_root in output_root.parents:
            raise ValueError("output_root cannot be the input directory or a child of it")
        output_root.mkdir(parents=True, exist_ok=True)
        batch_id = str(uuid.uuid4())
        now = utc_now()
        self.database.execute(
            "INSERT INTO batch_rules("
            "id,input_root,output_root,model_selector,preset_json,preset_name,recursive,"
            "watch_enabled,extensions_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                batch_id,
                str(input_root),
                str(output_root),
                arguments["model"],
                json.dumps(arguments.get("preset") or {}, ensure_ascii=False),
                _slug(arguments.get("preset_name") or "default"),
                int(bool(arguments.get("recursive", True))),
                int(bool(arguments.get("watch", False))),
                json.dumps(arguments.get("extensions") or DEFAULT_EXTENSIONS),
                now,
                now,
            ),
        )
        return self.get(batch_id)

    def get(self, batch_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM batch_rules WHERE id=?", (batch_id,))
        if not row:
            raise LookupError(f"batch not found: {batch_id}")
        result = Database.decode_json_row(row, ("preset_json", "extensions_json"))
        result["recursive"] = bool(result["recursive"])
        result["watch_enabled"] = bool(result["watch_enabled"])
        return result

    def set_watch(self, batch_id: str, enabled: bool) -> dict[str, Any]:
        self.get(batch_id)
        self.database.execute(
            "UPDATE batch_rules SET watch_enabled=?,updated_at=? WHERE id=?",
            (int(enabled), utc_now(), batch_id),
        )
        return self.get(batch_id)

    def _files(self, rule: dict[str, Any]) -> list[Path]:
        root = Path(rule["input_root"])
        iterator = root.rglob("*") if rule["recursive"] else root.glob("*")
        extensions = {value.casefold() for value in rule["extensions"]}

        def visible(path: Path) -> bool:
            relative = path.relative_to(root)
            current = root
            for part in relative.parts:
                current /= part
                if _is_hidden(current):
                    return False
            return True

        return sorted(
            path
            for path in iterator
            if path.is_file()
            and visible(path)
            and path.suffix.casefold() in extensions
            and not path.name.casefold().endswith(TEMP_SUFFIXES)
        )

    def _output(self, rule: dict[str, Any], source: Path) -> Path:
        relative = source.relative_to(Path(rule["input_root"]))
        model_slug = _slug(rule["model_selector"])
        preset_slug = _slug(rule["preset_name"])
        suffix = (
            source.suffix
            if source.suffix.casefold() in {".mp4", ".mkv", ".mov", ".webm"}
            else ".wav"
        )
        name = f"{source.stem}_{model_slug}_{preset_slug}{suffix}"
        return Path(rule["output_root"]) / relative.parent / name

    def _submit_file(self, rule: dict[str, Any], source: Path) -> dict[str, Any]:
        stat = source.stat()
        existing = self.database.fetch_one(
            "SELECT * FROM batch_items WHERE "
            "batch_id=? AND source_path=? AND source_size=? AND source_mtime_ns=?",
            (rule["id"], str(source), stat.st_size, stat.st_mtime_ns),
        )
        if existing:
            task = self.tasks.get(existing["task_id"]) if existing.get("task_id") else None
            return {"batch_item": existing, "task": task}
        output = self._output(rule, source)
        item_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"voxweave:{rule['id']}:{source}:{stat.st_size}:{stat.st_mtime_ns}",
            )
        )
        now = utc_now()
        self.database.execute(
            "INSERT INTO batch_items("
            "id,batch_id,source_path,source_size,source_mtime_ns,source_sha256,"
            "output_path,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                rule["id"],
                str(source),
                stat.st_size,
                stat.st_mtime_ns,
                sha256_file(source),
                str(output),
                "submitting",
                now,
                now,
            ),
        )
        arguments = {
            **rule["preset"],
            "input": str(source),
            "output": str(output),
            "model": rule["model_selector"],
            "overwrite": False,
        }
        try:
            task = self.tasks.submit("conversion.run", arguments)
        except Exception as error:
            self.database.execute(
                "UPDATE batch_items SET state='failed',error=?,updated_at=? WHERE id=?",
                (str(error), utc_now(), item_id),
            )
            raise
        self.database.execute(
            "UPDATE batch_items SET task_id=?,state='queued',updated_at=? WHERE id=?",
            (task["task_id"], utc_now(), item_id),
        )
        return {
            "batch_item": self.database.fetch_one(
                "SELECT * FROM batch_items WHERE id=?", (item_id,)
            ),
            "task": task,
        }

    def run(self, batch_id: str) -> dict[str, Any]:
        rule = self.get(batch_id)
        tasks = [
            self._submit_file(rule, path)
            for path in self._files(rule)
            if not self._output(rule, path).exists()
        ]
        return {"batch": rule, "tasks": tasks}

    def retry_task(self, task_id: str) -> dict[str, Any]:
        task = self.tasks.retry(task_id)
        self.database.execute(
            "UPDATE batch_items SET task_id=?,state='queued',error=NULL,updated_at=? "
            "WHERE task_id=?",
            (task["task_id"], utc_now(), task_id),
        )
        return task

    def _sync_items(self) -> None:
        items = self.database.fetch_all(
            "SELECT id,task_id FROM batch_items WHERE task_id IS NOT NULL "
            "AND state NOT IN ('completed','failed','cancelled','interrupted')"
        )
        for item in items:
            task = self.tasks.get(item["task_id"])
            self.database.execute(
                "UPDATE batch_items SET state=?,error=?,updated_at=? WHERE id=?",
                (task["state"], task.get("error"), utc_now(), item["id"]),
            )

    def _watch_loop(self) -> None:
        while not self.stop_event.wait(2.0):
            self._sync_items()
            rules = self.database.fetch_all("SELECT * FROM batch_rules WHERE watch_enabled=1")
            for raw in rules:
                rule = Database.decode_json_row(raw, ("preset_json", "extensions_json"))
                rule["recursive"] = bool(rule["recursive"])
                for path in self._files(rule):
                    if self._output(rule, path).exists():
                        continue
                    key = (rule["id"], str(path))
                    size = path.stat().st_size
                    previous = self.observed.get(key)
                    now = time.time()
                    if previous and previous[0] == size and now - previous[1] >= 5.0:
                        try:
                            self._submit_file(rule, path)
                        except Exception:
                            continue
                        self.observed.pop(key, None)
                    elif not previous or previous[0] != size:
                        self.observed[key] = (size, now)

    def shutdown(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)
