from __future__ import annotations

import json
import logging
import os
import queue
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
WATCH_SETTLE_SECONDS = 5.0
WATCH_RECONCILE_SECONDS = 300.0
WATCH_LOOP_SECONDS = 0.25


class _WindowsDirectoryWatcher:
    """ReadDirectoryChangesW adapter with overflow recovery signalling."""

    def __init__(self, root: Path, recursive: bool) -> None:
        self.root = root.resolve()
        self.recursive = recursive
        self.changes: queue.Queue[Path] = queue.Queue(maxsize=8192)
        self.overflow = threading.Event()
        self.stop_event = threading.Event()
        self._handle: int | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"voxweave-directory-watch-{uuid.uuid4().hex[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def drain(self) -> tuple[list[Path], bool]:
        paths: list[Path] = []
        overflow = self.overflow.is_set()
        self.overflow.clear()
        while True:
            try:
                item = self.changes.get_nowait()
            except queue.Empty:
                break
            paths.append(item)
        return paths, overflow

    def _record(self, path: Path) -> None:
        try:
            self.changes.put_nowait(path)
        except queue.Full:
            self.overflow.set()

    def stop(self) -> None:
        self.stop_event.set()
        handle = self._handle
        if handle not in {None, -1}:
            import ctypes
            from ctypes import wintypes

            cancel_io = ctypes.WinDLL("kernel32", use_last_error=True).CancelIoEx
            cancel_io.argtypes = (wintypes.HANDLE, wintypes.LPVOID)
            cancel_io.restype = wintypes.BOOL
            cancel_io(wintypes.HANDLE(handle), None)
        self._thread.join(timeout=2)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        read_changes = kernel32.ReadDirectoryChangesW
        read_changes.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPDWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        )
        read_changes.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            str(self.root),
            0x0001,  # FILE_LIST_DIRECTORY
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            self.overflow.set()
            return
        self._handle = int(handle)
        buffer = ctypes.create_string_buffer(64 * 1024)
        returned = wintypes.DWORD()
        notify_filter = 0x00000001 | 0x00000002 | 0x00000008 | 0x00000010 | 0x00000040
        try:
            while not self.stop_event.is_set():
                ok = read_changes(
                    handle,
                    buffer,
                    len(buffer),
                    self.recursive,
                    notify_filter,
                    ctypes.byref(returned),
                    None,
                    None,
                )
                if not ok:
                    error = ctypes.get_last_error()
                    if self.stop_event.is_set() or error == 995:  # ERROR_OPERATION_ABORTED
                        return
                    self.overflow.set()
                    time.sleep(0.25)
                    continue
                if returned.value == 0:
                    self.overflow.set()
                    continue
                offset = 0
                data = buffer.raw[: returned.value]
                while offset + 12 <= len(data):
                    next_offset = int.from_bytes(data[offset : offset + 4], "little")
                    name_length = int.from_bytes(data[offset + 8 : offset + 12], "little")
                    name = data[offset + 12 : offset + 12 + name_length].decode(
                        "utf-16-le", errors="replace"
                    )
                    self._record(self.root / name)
                    if next_offset == 0:
                        break
                    offset += next_offset
        finally:
            self._handle = None
            close_handle(handle)


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
        self.settled: dict[tuple[str, str], tuple[int, int]] = {}
        self.watchers: dict[str, tuple[tuple[Any, ...], _WindowsDirectoryWatcher, float]] = {}
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
                    hidden = entry.name.startswith(".") or _is_hidden(path)
                    if hidden:
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
    def _watch_signature(rule: dict[str, Any]) -> tuple[Any, ...]:
        return (
            rule["input_root"],
            bool(rule["recursive"]),
            tuple(sorted(value.casefold() for value in rule["extensions"])),
            rule["model_id"],
            rule["model_sha256"],
            rule.get("index_sha256"),
            rule["preset_name"],
            json.dumps(rule["preset"], sort_keys=True, ensure_ascii=False),
            rule["output_root"],
        )

    def _matches_rule(self, rule: dict[str, Any], path: Path) -> bool:
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

    def _observe_path(self, rule: dict[str, Any], path: Path, *, force: bool) -> None:
        key = (rule["id"], str(path))
        if not self._matches_rule(rule, path):
            self.observed.pop(key, None)
            self.settled.pop(key, None)
            return
        try:
            file_stat = path.stat()
        except (FileNotFoundError, PermissionError, OSError):
            self.observed.pop(key, None)
            self.settled.pop(key, None)
            return
        identity = (file_stat.st_size, file_stat.st_mtime_ns)
        previous = self.observed.get(key)
        if force or self.settled.get(key) != identity:
            self.settled.pop(key, None)
            if force or not previous or previous[:2] != identity:
                self.observed[key] = (*identity, time.monotonic())

    def _reconcile_rule(self, rule: dict[str, Any]) -> None:
        files = self._files(rule)
        seen = {(rule["id"], str(path)) for path in files}
        for mapping in (self.observed, self.settled):
            for key in [key for key in mapping if key[0] == rule["id"] and key not in seen]:
                mapping.pop(key, None)
        for path in files:
            self._observe_path(rule, path, force=False)

    def _submit_stable_paths(self, rule: dict[str, Any]) -> None:
        now = time.monotonic()
        for key, previous in list(self.observed.items()):
            if key[0] != rule["id"]:
                continue
            path = Path(key[1])
            if not self._matches_rule(rule, path):
                self.observed.pop(key, None)
                self.settled.pop(key, None)
                continue
            try:
                file_stat = path.stat()
            except (FileNotFoundError, PermissionError, OSError):
                self.observed.pop(key, None)
                self.settled.pop(key, None)
                continue
            identity = (file_stat.st_size, file_stat.st_mtime_ns)
            if previous[:2] != identity:
                self.observed[key] = (*identity, now)
                continue
            if now - previous[2] < WATCH_SETTLE_SECONDS:
                continue
            self._submit_file(rule, path, self.stop_event.is_set)
            self.observed.pop(key, None)
            self.settled[key] = identity

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

    def _submit_file(
        self,
        rule: dict[str, Any],
        source: Path,
        cancelled: Callable[[], bool] | None = None,
    ) -> str:
        with self.submission_lock:
            if cancelled and cancelled():
                raise InterruptedError("batch submission cancelled")
            before = source.stat()
            source_hash = sha256_file(source, cancelled=cancelled)
            after = source.stat()
            if cancelled and cancelled():
                raise InterruptedError("batch submission cancelled")
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

    def run(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any] | DeferredTask:
        batch_id = arguments["batch_id"]
        rule = self.get(batch_id)
        if rule["state"] != "active":
            raise ValueError(f"batch rule is not active: {batch_id}")
        item_ids: list[str] = []
        failures = []
        files = self._files(rule, context.cancelled)
        total = max(1, len(files))
        for index, path in enumerate(files):
            if context.cancelled():
                raise InterruptedError("task cancellation requested")
            context.progress(index / total, "enumerating", f"{index}/{len(files)} files")
            try:
                item_ids.append(self._submit_file(rule, path, context.cancelled))
            except Exception as error:  # noqa: BLE001 - isolate each source file
                failures.append({"source_path": str(path), "error": str(error)})
        result = {"batch": rule, "tasks": [], "failures": failures}
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
            if item["state"] != item["task_state"] or item.get("error") != item.get(
                "task_error"
            ):
                self.repository.update_item_state(
                    item["id"], item["task_state"], item.get("task_error")
                )

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
        next_sync = 0.0
        rules: list[dict[str, Any]] = []
        while not self.stop_event.wait(WATCH_LOOP_SECONDS):
            try:
                now = time.monotonic()
                if now >= next_sync:
                    self._sync_items()
                    self._sync_runs()
                    rules = self.repository.watched_rules()
                    next_sync = now + 2.0
            except Exception:  # noqa: BLE001 - a watcher must remain supervised
                LOGGER.exception("batch watcher synchronization failed")
                continue
            active_rule_ids = {str(raw["id"]) for raw in rules}
            for rule_id in set(self.watchers) - active_rule_ids:
                _signature, watcher, _reconciled = self.watchers.pop(rule_id)
                watcher.stop()
                self.observed = {
                    key: value
                    for key, value in self.observed.items()
                    if key[0] != rule_id
                }
                self.settled = {
                    key: value
                    for key, value in self.settled.items()
                    if key[0] != rule_id
                }
            for raw in rules:
                try:
                    rule = Database.decode_json_row(raw, ("preset_json", "extensions_json"))
                    rule["recursive"] = bool(rule["recursive"])
                    signature = self._watch_signature(rule)
                    watcher_record = self.watchers.get(rule["id"])
                    if (
                        not watcher_record
                        or watcher_record[0] != signature
                        or not watcher_record[1].alive
                    ):
                        if watcher_record:
                            watcher_record[1].stop()
                        self.observed = {
                            key: value
                            for key, value in self.observed.items()
                            if key[0] != rule["id"]
                        }
                        self.settled = {
                            key: value
                            for key, value in self.settled.items()
                            if key[0] != rule["id"]
                        }
                        watcher = _WindowsDirectoryWatcher(
                            Path(rule["input_root"]), bool(rule["recursive"])
                        )
                        watcher.start()
                        self._reconcile_rule(rule)
                        watcher_record = (signature, watcher, time.monotonic())
                        self.watchers[rule["id"]] = watcher_record
                    _signature, watcher, last_reconcile = watcher_record
                    changed_paths, overflow = watcher.drain()
                    for path in set(changed_paths):
                        self._observe_path(rule, path, force=True)
                        if path.is_dir():
                            self._reconcile_rule(rule)
                    if overflow or time.monotonic() - last_reconcile >= WATCH_RECONCILE_SECONDS:
                        self._reconcile_rule(rule)
                        self.watchers[rule["id"]] = (
                            signature,
                            watcher,
                            time.monotonic(),
                        )
                    self._submit_stable_paths(rule)
                    if rule.get("last_error") is not None:
                        self.repository.clear_rule_error(rule["id"])
                        rule["last_error"] = None
                except InterruptedError:
                    if not self.stop_event.is_set():
                        LOGGER.exception("batch watch rule was interrupted: %s", raw.get("id"))
                except Exception as error:  # noqa: BLE001 - isolate each watched rule
                    LOGGER.exception("batch watch rule failed: %s", raw.get("id"))
                    self.repository.record_rule_error(raw["id"], str(error))
        for _signature, watcher, _reconciled in list(self.watchers.values()):
            watcher.stop()
        self.watchers.clear()

    def shutdown(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
