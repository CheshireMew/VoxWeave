from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .gui_presenters import task_error_summary
from .gui_requests import RequestCoordinator
from .gui_tasks import TaskFeed

TASK_TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


class TaskActivity(QObject):
    """Owns GUI submissions that live beyond the initial HTTP response."""

    busyChanged = Signal()

    def __init__(
        self,
        requests: RequestCoordinator,
        task_feed: TaskFeed,
        status_callback: Any,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.task_feed = task_feed
        self.status_callback = status_callback
        self._busy: set[str] = set()
        self._completions: dict[str, tuple[Any, str]] = {}
        self.task_feed.taskUpdated.connect(self._consume_task)

    @Property("QVariantList", notify=busyChanged)
    def busyKeys(self) -> list[str]:
        return sorted(self._busy)

    def _set_busy(self, key: str, busy: bool) -> None:
        before = set(self._busy)
        if busy:
            self._busy.add(key)
        else:
            self._busy.discard(key)
        if before != self._busy:
            self.busyChanged.emit()

    def submit(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        action_key: str,
        completed: Any = None,
        submitted: Any = None,
    ) -> None:
        self._set_busy(action_key, True)

        def accepted(task: dict[str, Any]) -> None:
            self._completions[task["id"]] = (completed, action_key)
            if submitted:
                submitted(task)
            self.task_feed.accept(task)

        def failed(message: str) -> None:
            self._set_busy(action_key, False)
            self.status_callback(message, "danger")

        self.requests.submit(
            operation,
            arguments,
            accepted,
            error_callback=failed,
            request_key=f"submit:{action_key}",
        )

    def abandon(self, action_key: str) -> None:
        self.requests.invalidate(f"submit:{action_key}")
        self._set_busy(action_key, False)

    @Slot(object)
    def _consume_task(self, value: object) -> None:
        task = dict(value)
        task_id = str(task["id"])
        state = task.get("state")
        if state not in TASK_TERMINAL_STATES:
            return
        completion = self._completions.pop(task_id, None)
        if completion:
            callback, action_key = completion
            self._set_busy(action_key, False)
            if state == "completed" and callback:
                callback(task.get("result"))
        if state == "failed" and completion:
            self.status_callback(task_error_summary(task) or "task failed", "danger")
