from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .database import Database
from .protocol import public_error_code, validate_completion_result
from .task_repository import TaskRepository

Progress = Callable[[float, str, str | None], None]


@dataclass(frozen=True, slots=True)
class TaskContext:
    task_id: str
    retry_of: str | None
    snapshot: dict[str, Any]
    progress: Progress
    cancelled: Callable[[], bool]


Handler = Callable[[dict[str, Any], TaskContext], Any]


@dataclass(frozen=True, slots=True)
class DeferredTask:
    stage: str
    detail: str


TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}
LIFECYCLE_STATES = {"queued", "running", *TERMINAL_STATES}
LOGGER = logging.getLogger(__name__)
PROGRESS_INTERVAL_SECONDS = 0.1
PROGRESS_DELTA = 0.02
MAX_WORKER_FAILURES = 3


class TaskManager:
    def __init__(self, database: Database):
        self.database = database
        self.repository = TaskRepository(database)
        self.handlers: dict[str, Handler] = {}
        self.queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.started = False
        self._dispatch_condition = threading.Condition()
        self._dispatch_pause_reasons: set[str] = set()
        self._executing = False
        self._cancel_lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._event_condition = threading.Condition()
        self._event_generation = 0
        self._task_event_generation: dict[str, int] = {}
        self._task_event_waiters: dict[str, int] = {}

    def _notify_event(self, task_id: str) -> None:
        with self._event_condition:
            self._event_generation += 1
            if self._task_event_waiters.get(task_id, 0):
                self._task_event_generation[task_id] += 1
            self._event_condition.notify_all()

    def _cancel_event(self, task_id: str) -> threading.Event:
        with self._cancel_lock:
            return self._cancel_events.setdefault(task_id, threading.Event())

    def _forget_cancel_event(self, task_id: str) -> None:
        with self._cancel_lock:
            self._cancel_events.pop(task_id, None)

    def register(self, operation: str, handler: Handler) -> None:
        if self.started:
            raise RuntimeError("task handlers must be registered before the manager starts")
        self.handlers[operation] = handler

    def start(self, *, preserved_task_ids: set[str] | None = None) -> None:
        if self.started:
            raise RuntimeError("task manager is already running")
        for task_id in self.repository.recover_for_start(preserved_task_ids or set()):
            self.queue.put(task_id)
        self.started = True
        self.worker = threading.Thread(
            target=self._run, name="voxweave-task-worker", daemon=True
        )
        self.worker.start()

    def create_in_transaction(
        self,
        db: Any,
        operation: str,
        arguments: dict[str, Any],
        *,
        request_id: str | None,
        actor: dict[str, Any] | None,
        snapshot: dict[str, Any] | None = None,
        retry_of: str | None = None,
    ) -> tuple[str, bool]:
        return self.repository.create(
            db,
            operation,
            arguments,
            request_id=request_id,
            actor=actor,
            snapshot=snapshot,
            retry_of=retry_of,
        )

    def submit(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        request_id: str | None = None,
        actor: dict[str, Any] | None = None,
        snapshot: dict[str, Any] | None = None,
        retry_of: str | None = None,
    ) -> dict[str, Any]:
        if not self.started:
            raise RuntimeError("task manager has not started")
        if operation not in self.handlers:
            raise LookupError(f"no long-running handler registered for {operation}")
        arguments = dict(arguments)
        with self.database.connect() as db:
            task_id, created = self.create_in_transaction(
                db,
                operation,
                arguments,
                request_id=request_id,
                actor=actor,
                snapshot=snapshot,
                retry_of=retry_of,
            )
        if created:
            self.notify_enqueued(task_id)
        return self.get(task_id)

    def notify_enqueued(self, task_id: str) -> None:
        self._cancel_event(task_id)
        self._notify_event(task_id)
        self.queue.put(task_id)

    def _update(
        self,
        task_id: str,
        *,
        state: str,
        progress: float,
        stage: str | None,
        detail: str | None = None,
        result: Any = None,
        error_type: str | None = None,
        error: str | None = None,
    ) -> None:
        if state not in LIFECYCLE_STATES:
            raise ValueError(f"invalid task lifecycle state: {state}")
        normalized_progress = max(0.0, min(1.0, progress))
        self.repository.update(
            task_id,
            state=state,
            progress=normalized_progress,
            stage=stage,
            detail=detail,
            result=result,
            error_type=error_type,
            error=error,
        )
        self._notify_event(task_id)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                task_id = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                with self._dispatch_condition:
                    while self._dispatch_pause_reasons and not self.stop_event.is_set():
                        self._dispatch_condition.wait(timeout=0.25)
                    if self.stop_event.is_set():
                        continue
                    self._executing = True
                self._execute(task_id)
            except Exception as exc:  # noqa: BLE001 - keep the durable worker alive
                LOGGER.exception("unhandled task worker failure for %s", task_id)
                try:
                    retry = self.repository.recover_worker_failure(
                        task_id,
                        str(exc) or type(exc).__name__,
                        max_failures=MAX_WORKER_FAILURES,
                    )
                    self._notify_event(task_id)
                except Exception:  # noqa: BLE001 - the worker must remain observable
                    LOGGER.exception("failed to persist task worker failure for %s", task_id)
                    retry = False
                if retry and not self.stop_event.is_set():
                    self.queue.put(task_id)
            finally:
                with self._dispatch_condition:
                    self._executing = False
                    self._dispatch_condition.notify_all()
                self.queue.task_done()

    def pause_dispatch(self, reason: str) -> bool:
        with self._dispatch_condition:
            if self._executing:
                return False
            self._dispatch_pause_reasons.add(reason)
            return True

    def resume_dispatch(self, reason: str) -> None:
        with self._dispatch_condition:
            self._dispatch_pause_reasons.discard(reason)
            self._dispatch_condition.notify_all()

    def _execute(self, task_id: str) -> None:
        row = self.repository.raw(task_id)
        if not row or row["state"] != "queued":
            return
        operation = row["operation"]
        handler = self.handlers.get(operation)
        if not handler:
            self._update(
                task_id,
                state="failed",
                progress=0.0,
                stage="dispatch",
                error_type="operation_unavailable",
                error=f"no handler registered for {operation}",
            )
            return

        cancel_event = self._cancel_event(task_id)
        if row.get("cancel_requested"):
            cancel_event.set()

        def cancelled() -> bool:
            return self.stop_event.is_set() or cancel_event.is_set()

        last_progress_at = 0.0
        last_progress_value = -1.0
        last_progress_stage: str | None = None

        def progress(value: float, stage: str, detail: str | None = None) -> None:
            nonlocal last_progress_at, last_progress_value, last_progress_stage
            if cancelled():
                raise InterruptedError("task cancellation requested")
            if stage in TERMINAL_STATES:
                raise ValueError("task handlers cannot publish terminal lifecycle states")
            normalized = max(0.0, min(1.0, value))
            now = time.monotonic()
            if (
                stage == last_progress_stage
                and now - last_progress_at < PROGRESS_INTERVAL_SECONDS
                and abs(normalized - last_progress_value) < PROGRESS_DELTA
            ):
                return
            self._update(task_id, state="running", progress=value, stage=stage, detail=detail)
            last_progress_at = now
            last_progress_value = normalized
            last_progress_stage = stage

        try:
            if cancelled():
                raise InterruptedError("task cancellation requested")
            self._update(task_id, state="running", progress=0.01, stage="starting")
            LOGGER.info(
                "task started", extra={"task_id": task_id, "operation": operation}
            )
            context = TaskContext(
                task_id=task_id,
                retry_of=row.get("retry_of"),
                snapshot=json.loads(row.get("snapshot_json") or "{}"),
                progress=progress,
                cancelled=cancelled,
            )
            result = handler(json.loads(row["arguments_json"]), context)
            if not isinstance(result, DeferredTask):
                result = validate_completion_result(operation, result)
        except InterruptedError as exc:
            service_stopping = self.stop_event.is_set()
            self._update(
                task_id,
                state="interrupted" if service_stopping else "cancelled",
                progress=0.0,
                stage="service_shutdown" if service_stopping else "cancelled",
                error_type="service_shutdown" if service_stopping else "cancelled",
                error="service stopped during task" if service_stopping else str(exc),
            )
            LOGGER.info(
                "task interrupted",
                extra={"task_id": task_id, "operation": operation},
            )
        except Exception as exc:  # noqa: BLE001 - task errors must be persisted
            self._update(
                task_id,
                state="failed",
                progress=0.0,
                stage="failed",
                error_type=public_error_code(exc),
                error=str(exc) or "operation failed",
            )
            LOGGER.exception(
                "task failed", extra={"task_id": task_id, "operation": operation}
            )
        else:
            if isinstance(result, DeferredTask):
                self._update(
                    task_id,
                    state="running",
                    progress=0.99,
                    stage=result.stage,
                    detail=result.detail,
                )
                return
            self._update(task_id, state="completed", progress=1.0, stage="completed", result=result)
            LOGGER.info(
                "task completed", extra={"task_id": task_id, "operation": operation}
            )
        finally:
            self._forget_cancel_event(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        return self.repository.get(task_id)

    def list(self, limit: int = 200, cursor: str | None = None) -> dict[str, Any]:
        return self.repository.list(limit, cursor)

    def find_by_request(
        self,
        request_id: str,
        operation: str,
        arguments: dict[str, Any],
        actor: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        return self.repository.find_by_request(request_id, operation, arguments, actor)

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self.get(task_id)
        if task["state"] in TERMINAL_STATES:
            return task
        if task["state"] == "queued":
            self._cancel_event(task_id).set()
            self._update(
                task_id,
                state="cancelled",
                progress=0.0,
                stage="cancelled",
                error_type="cancelled",
                error="task cancellation requested before dispatch",
            )
            self._forget_cancel_event(task_id)
        else:
            self._cancel_event(task_id).set()
            self.repository.request_cancel(task_id)
        return self.get(task_id)

    def retry(
        self,
        task_id: str,
        *,
        request_id: str | None = None,
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = self.get(task_id)
        if task["state"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("only failed, cancelled, or interrupted tasks can be retried")
        return self.submit(
            task["operation"],
            dict(task["arguments"]),
            request_id=request_id,
            actor=actor,
            snapshot=task["snapshot"],
            retry_of=task_id,
        )

    def complete_deferred(self, task_id: str, result: Any) -> None:
        task = self.get(task_id)
        if task["state"] in TERMINAL_STATES:
            return
        try:
            result = validate_completion_result(task["operation"], result)
        except Exception as exc:  # noqa: BLE001 - contract failure is persisted
            self._update(
                task_id,
                state="failed",
                progress=0.0,
                stage="failed",
                error_type=public_error_code(exc),
                error=str(exc),
            )
            return
        self._update(
            task_id,
            state="completed",
            progress=1.0,
            stage="completed",
            result=result,
        )

    def fail_deferred(self, task_id: str, error_type: str, error: str, result: Any) -> None:
        task = self.get(task_id)
        if task["state"] in TERMINAL_STATES:
            return
        self._update(
            task_id,
            state="failed",
            progress=1.0,
            stage="failed",
            result=result,
            error_type=error_type,
            error=error,
        )

    def cancel_deferred(self, task_id: str, result: Any) -> None:
        task = self.get(task_id)
        if task["state"] in TERMINAL_STATES:
            return
        self._update(
            task_id,
            state="cancelled",
            progress=1.0,
            stage="cancelled",
            result=result,
            error_type="cancelled",
            error="batch run cancellation requested",
        )

    def events(
        self, task_id: str, after_id: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        return self.repository.events(task_id, after_id, limit)

    def events_all(self, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return self.repository.events_all(after_id, limit)

    def wait_events(
        self,
        task_id: str | None,
        after_id: int,
        limit: int,
        timeout: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        query = self.events if task_id else self.events_all
        arguments = (task_id, after_id, limit) if task_id else (after_id, limit)
        with self._event_condition:
            if task_id:
                self._task_event_waiters[task_id] = (
                    self._task_event_waiters.get(task_id, 0) + 1
                )
                self._task_event_generation.setdefault(task_id, 0)
            try:
                rows = query(*arguments)
                if rows:
                    return rows
                generation = (
                    self._task_event_generation[task_id]
                    if task_id
                    else self._event_generation
                )

                def changed() -> bool:
                    current = (
                        self._task_event_generation[task_id]
                        if task_id
                        else self._event_generation
                    )
                    return current != generation or bool(cancelled and cancelled())

                self._event_condition.wait_for(changed, timeout=max(0.0, timeout))
                if cancelled and cancelled():
                    return []
                return query(*arguments)
            finally:
                if task_id:
                    remaining = self._task_event_waiters[task_id] - 1
                    if remaining:
                        self._task_event_waiters[task_id] = remaining
                    else:
                        self._task_event_waiters.pop(task_id, None)
                        self._task_event_generation.pop(task_id, None)

    def wake_event_waiters(self) -> None:
        with self._event_condition:
            self._event_generation += 1
            for task_id in self._task_event_generation:
                self._task_event_generation[task_id] += 1
            self._event_condition.notify_all()

    def recent_events(self, limit: int = 500) -> list[dict[str, Any]]:
        return self.repository.recent_events(limit)

    def shutdown(self) -> None:
        self.started = False
        self.stop_event.set()
        with self._dispatch_condition:
            self._dispatch_condition.notify_all()
        self.wake_event_waiters()
        if self.worker:
            self.worker.join(timeout=30)
            if self.worker.is_alive():
                raise RuntimeError("task worker did not stop within 30 seconds")
            self.worker = None
