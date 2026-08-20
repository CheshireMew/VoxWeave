from __future__ import annotations

import json
import logging
import os
import re
import stat as stat_module
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .batch_repository import BatchRepository
from .database import Database, utc_now
from .hashing import sha256_file
from .protocol import OperationError
from .task_manager import DeferredTask, TaskContext, TaskManager

DEFAULT_EXTENSIONS = [".wav", ".flac", ".mp3", ".m4a", ".aac", ".mp4", ".mkv", ".mov", ".webm"]
TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", ".download")
LOGGER = logging.getLogger(__name__)


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
    def __init__(
        self,
        database: Database,
        tasks: TaskManager,
        resolve_model: Callable[[str], dict[str, Any]],
    ):
        self.database = database
        self.repository = BatchRepository(database)
        self.tasks = tasks
        self.resolve_model = resolve_model
        self.stop_event = threading.Event()
        self.observed: dict[tuple[str, str], tuple[int, int, float]] = {}
        self.submission_lock = threading.RLock()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread:
            raise RuntimeError("batch watcher is already running")
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
        model = self.resolve_model(arguments["model"])
        batch_id = str(uuid.uuid4())
        now = utc_now()
        self.repository.create_rule(
            (
                batch_id,
                str(input_root),
                str(output_root),
                model["id"],
                model["model_sha256"],
                model["index_sha256"],
                json.dumps(arguments.get("preset") or {}, ensure_ascii=False),
                _slug(arguments.get("preset_name") or "default"),
                int(bool(arguments.get("recursive", True))),
                int(bool(arguments.get("watch", False))),
                json.dumps(arguments.get("extensions") or DEFAULT_EXTENSIONS),
                "active",
                now,
                now,
            ),
        )
        return self.get(batch_id)

    def update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        batch_id = arguments["batch_id"]
        self.get(batch_id)
        input_root = Path(arguments["input_root"]).expanduser().resolve()
        output_root = Path(arguments["output_root"]).expanduser().resolve()
        if not input_root.is_dir():
            raise NotADirectoryError(input_root)
        if output_root == input_root or input_root in output_root.parents:
            raise ValueError("output_root cannot be the input directory or a child of it")
        output_root.mkdir(parents=True, exist_ok=True)
        model = self.resolve_model(arguments["model"])
        self.repository.update_rule(
            batch_id,
            (
                str(input_root),
                str(output_root),
                model["id"],
                model["model_sha256"],
                model["index_sha256"],
                json.dumps(arguments.get("preset") or {}, ensure_ascii=False),
                _slug(arguments.get("preset_name") or "default"),
                int(bool(arguments.get("recursive", True))),
                int(bool(arguments.get("watch", False))),
                json.dumps(arguments.get("extensions") or DEFAULT_EXTENSIONS),
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

    def _files(
        self, rule: dict[str, Any], cancelled: Callable[[], bool] | None = None
    ) -> list[Path]:
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

        files = []
        for path in iterator:
            if cancelled and cancelled():
                raise InterruptedError("task cancellation requested")
            if (
                path.is_file()
                and visible(path)
                and path.suffix.casefold() in extensions
                and not path.name.casefold().endswith(TEMP_SUFFIXES)
            ):
                files.append(path)
        return sorted(files)

    def _output(self, rule: dict[str, Any], source: Path, source_hash: str) -> Path:
        relative = source.relative_to(Path(rule["input_root"]))
        model_slug = _slug(rule["model_id"])
        preset_slug = _slug(rule["preset_name"])
        suffix = (
            source.suffix
            if source.suffix.casefold() in {".mp4", ".mkv", ".mov", ".webm"}
            else ".wav"
        )
        source_type = source.suffix.casefold().removeprefix(".")
        name = f"{source.stem}_{source_type}_{model_slug}_{preset_slug}_{source_hash[:12]}{suffix}"
        return Path(rule["output_root"]) / relative.parent / name

    def _submit_file(self, rule: dict[str, Any], source: Path) -> dict[str, Any]:
        with self.submission_lock:
            before = source.stat()
            source_hash = sha256_file(source)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(f"source changed while hashing: {source}")
            output = self._output(rule, source, source_hash)
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
                existing = self.repository.find_item(db, rule["id"], str(source), source_hash)
                if existing and existing["task_id"]:
                    existing_dict = dict(existing)
                    task = self.tasks.get(existing_dict["task_id"])
                    return {"batch_item": existing_dict, "task": task}
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
            task = self.tasks.get(task_id)
            return {
                "batch_item": self.repository.get_item(item_id),
                "task": task,
            }

    def run(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any] | DeferredTask:
        batch_id = arguments["batch_id"]
        rule = self.get(batch_id)
        if rule["state"] != "active":
            raise ValueError(f"batch rule is not active: {batch_id}")
        tasks = []
        failures = []
        files = self._files(rule, context.cancelled)
        total = max(1, len(files))
        for index, path in enumerate(files):
            if context.cancelled():
                raise InterruptedError("task cancellation requested")
            context.progress(index / total, "enumerating", f"{index}/{len(files)} files")
            try:
                tasks.append(self._submit_file(rule, path))
            except Exception as error:  # noqa: BLE001 - isolate each source file
                failures.append({"source_path": str(path), "error": str(error)})
        item_ids = [item["batch_item"]["id"] for item in tasks]
        result = {"batch": rule, "tasks": tasks, "failures": failures}
        if not item_ids:
            if failures:
                raise OperationError(
                    "batch_submission_failed",
                    f"failed to submit {len(failures)} batch files",
                )
            return result
        self.repository.insert_run(context.task_id, batch_id, item_ids, failures)
        context.progress(0.99, "waiting_for_children", f"waiting for {len(item_ids)} files")
        return DeferredTask("waiting_for_children", f"waiting for {len(item_ids)} files")

    def relink_retry(self, previous_task_id: str, task_id: str) -> None:
        self.repository.relink_retry(previous_task_id, task_id)

    def durable_task_ids(self) -> set[str]:
        return self.repository.active_run_ids()

    def retry(
        self, arguments: dict[str, Any], context: TaskContext
    ) -> dict[str, Any] | DeferredTask:
        batch_id = arguments["batch_id"]
        self.get(batch_id)
        items = self.repository.retryable_items(batch_id)
        retried = []
        for index, item in enumerate(items):
            if context.cancelled():
                raise InterruptedError("task cancellation requested")
            if not item["task_id"]:
                continue
            task = self.tasks.retry(
                item["task_id"],
                request_id=f"batch-retry:{context.task_id}:{item['id']}",
                actor={"kind": "batch-retry", "batch_id": batch_id},
            )
            self.repository.link_existing_item(item["id"], task["id"])
            retried.append(item["id"])
            context.progress(
                0.9 * ((index + 1) / max(1, len(items))),
                "retrying_children",
                f"{index + 1}/{len(items)}",
            )
        if not retried:
            return {"batch_id": batch_id, "retried": 0, "items": []}
        self.repository.insert_run(context.task_id, batch_id, retried, [])
        return DeferredTask("waiting_for_children", f"waiting for {len(retried)} retries")

    def _sync_items(self) -> None:
        items = self.repository.pending_items()
        for item in items:
            task = self.tasks.get(item["task_id"])
            self.repository.update_item_state(item["id"], task["state"], task.get("error"))

    def _sync_runs(self) -> None:
        runs = self.repository.active_runs()
        for run in runs:
            item_ids = json.loads(run["item_ids_json"])
            items = self.repository.items_by_ids(item_ids)
            parent = self.tasks.get(run["id"])
            if parent["cancel_requested"]:
                for item in items:
                    if item["task_id"]:
                        self.tasks.cancel(item["task_id"])
            terminal = {"completed", "failed", "cancelled", "interrupted"}
            if len(items) != len(item_ids) or any(item["state"] not in terminal for item in items):
                continue
            counts: dict[str, int] = {}
            for item in items:
                counts[item["state"]] = counts.get(item["state"], 0) + 1
            result = {
                "batch_id": run["batch_id"],
                "item_count": len(items),
                "counts": counts,
                "items": items,
                "submission_failures": json.loads(run["submission_failures_json"]),
            }
            if parent["cancel_requested"]:
                self.tasks.cancel_deferred(run["id"], result)
                state = "cancelled"
            elif set(counts) == {"completed"} and not result["submission_failures"]:
                self.tasks.complete_deferred(run["id"], result)
                state = "completed"
            else:
                self.tasks.fail_deferred(
                    run["id"],
                    "batch_run_failed",
                    f"{sum(count for name, count in counts.items() if name != 'completed')} "
                    "batch items did not complete; "
                    f"{len(result['submission_failures'])} files failed submission",
                    result,
                )
                state = "failed"
            self.repository.finish_run(run["id"], state)

    def _watch_loop(self) -> None:
        while not self.stop_event.wait(2.0):
            try:
                self._sync_items()
                self._sync_runs()
                rules = self.repository.watched_rules()
            except Exception:  # noqa: BLE001 - a watcher must remain supervised
                LOGGER.exception("batch watcher synchronization failed")
                continue
            for raw in rules:
                try:
                    rule = Database.decode_json_row(raw, ("preset_json", "extensions_json"))
                    rule["recursive"] = bool(rule["recursive"])
                    files = self._files(rule)
                    seen = {(rule["id"], str(path)) for path in files}
                    for key in [
                        key for key in self.observed if key[0] == rule["id"] and key not in seen
                    ]:
                        self.observed.pop(key, None)
                    for path in files:
                        key = (rule["id"], str(path))
                        stat = path.stat()
                        previous = self.observed.get(key)
                        now = time.time()
                        stable = (
                            previous
                            and previous[:2] == (stat.st_size, stat.st_mtime_ns)
                            and now - previous[2] >= 5.0
                        )
                        if stable:
                            self._submit_file(rule, path)
                            self.observed.pop(key, None)
                        elif not previous or previous[:2] != (stat.st_size, stat.st_mtime_ns):
                            self.observed[key] = (stat.st_size, stat.st_mtime_ns, now)
                    self.repository.clear_rule_error(rule["id"])
                except Exception as error:  # noqa: BLE001 - isolate each watched rule
                    LOGGER.exception("batch watch rule failed: %s", raw.get("id"))
                    self.repository.record_rule_error(raw["id"], str(error))

    def shutdown(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
