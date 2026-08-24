from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .batch_directory_watcher import WindowsDirectoryWatcher
from .batch_repository import BatchRepository
from .batch_run import BatchRunCoordinator
from .batch_submission import BatchSubmissionService

LOGGER = logging.getLogger(__name__)
WATCH_SETTLE_SECONDS = 5.0
WATCH_RECONCILE_SECONDS = 300.0
WATCH_LOOP_SECONDS = 0.25

WatcherRecord = tuple[tuple[Any, ...], WindowsDirectoryWatcher, float]


class BatchWatchSupervisor:
    """Owns watch lifecycles, file settling and periodic reconciliation."""

    def __init__(
        self,
        repository: BatchRepository,
        submissions: BatchSubmissionService,
        runs: BatchRunCoordinator,
    ) -> None:
        self.repository = repository
        self.submissions = submissions
        self.runs = runs
        self.stop_event = threading.Event()
        self.observed: dict[tuple[str, str], tuple[int, int, float]] = {}
        self.settled: dict[tuple[str, str], tuple[int, int]] = {}
        self.watchers: dict[str, WatcherRecord] = {}
        self.thread: threading.Thread | None = None
        self.settle_seconds = WATCH_SETTLE_SECONDS

    def start(self) -> None:
        if self.thread:
            raise RuntimeError("batch watcher is already running")
        self.thread = threading.Thread(
            target=self._watch_loop,
            name="voxweave-batch-watch",
            daemon=True,
        )
        self.thread.start()

    @staticmethod
    def _signature(rule: dict[str, Any]) -> tuple[Any, ...]:
        return (
            int(rule["revision"]),
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

    def _forget_rule(self, rule_id: str) -> None:
        self.observed = {
            key: value for key, value in self.observed.items() if key[0] != rule_id
        }
        self.settled = {
            key: value for key, value in self.settled.items() if key[0] != rule_id
        }

    def _observe(self, rule: dict[str, Any], path: Path, *, force: bool) -> None:
        key = (rule["id"], str(path))
        if not self.submissions.matches(rule, path):
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

    def _reconcile(self, rule: dict[str, Any]) -> None:
        files = self.submissions.files(rule)
        seen = {(rule["id"], str(path)) for path in files}
        for mapping in (self.observed, self.settled):
            for key in [key for key in mapping if key[0] == rule["id"] and key not in seen]:
                mapping.pop(key, None)
        for path in files:
            self._observe(rule, path, force=False)

    def _submit_stable(self, rule: dict[str, Any]) -> None:
        now = time.monotonic()
        for key, previous in list(self.observed.items()):
            if key[0] != rule["id"]:
                continue
            path = Path(key[1])
            if not self.submissions.matches(rule, path):
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
            if now - previous[2] < self.settle_seconds:
                continue
            self.submissions.submit_file(rule, path, self.stop_event.is_set)
            self.observed.pop(key, None)
            self.settled[key] = identity

    def _sync_watchers(self, rules: list[dict[str, Any]]) -> None:
        active_rule_ids = {str(rule["id"]) for rule in rules}
        for rule_id in set(self.watchers) - active_rule_ids:
            _signature, watcher, _reconciled = self.watchers.pop(rule_id)
            watcher.stop()
            self._forget_rule(rule_id)

    def _process_rule(self, rule: dict[str, Any]) -> None:
        signature = self._signature(rule)
        watcher_record = self.watchers.get(rule["id"])
        if (
            not watcher_record
            or watcher_record[0] != signature
            or not watcher_record[1].alive
        ):
            if watcher_record:
                watcher_record[1].stop()
            self._forget_rule(rule["id"])
            watcher = WindowsDirectoryWatcher(
                Path(rule["input_root"]), bool(rule["recursive"])
            )
            watcher.start()
            self._reconcile(rule)
            watcher_record = (signature, watcher, time.monotonic())
            self.watchers[rule["id"]] = watcher_record
        _signature, watcher, last_reconcile = watcher_record
        changed_paths, overflow = watcher.drain()
        for path in set(changed_paths):
            self._observe(rule, path, force=True)
            if path.is_dir():
                self._reconcile(rule)
        if overflow or time.monotonic() - last_reconcile >= WATCH_RECONCILE_SECONDS:
            self._reconcile(rule)
            self.watchers[rule["id"]] = (signature, watcher, time.monotonic())
        self._submit_stable(rule)
        if rule.get("last_error") is not None:
            self.repository.clear_rule_error(rule["id"])

    def _watch_loop(self) -> None:
        next_sync = 0.0
        raw_rules: list[dict[str, Any]] = []
        while not self.stop_event.wait(WATCH_LOOP_SECONDS):
            try:
                now = time.monotonic()
                if now >= next_sync:
                    self.runs.sync()
                    raw_rules = self.repository.watched_rules()
                    next_sync = now + 2.0
            except Exception:  # noqa: BLE001 - a watcher must remain supervised
                LOGGER.exception("batch watcher synchronization failed")
                continue
            self._sync_watchers(raw_rules)
            for raw in raw_rules:
                try:
                    rule = self.repository.decode_rule(raw)
                    self._process_rule(rule)
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
        if not self.thread:
            return
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("batch watcher did not stop within 5 seconds")
        lingering = [
            rule_id
            for rule_id, (_signature, watcher, _reconciled) in self.watchers.items()
            if watcher.alive
        ]
        if lingering:
            raise RuntimeError(f"directory watchers did not stop for batch rules: {lingering}")
        self.thread = None
