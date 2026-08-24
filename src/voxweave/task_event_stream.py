from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .task_manager import TaskManager


class TaskEventStream:
    """Blocking event-feed boundary used by transports without exposing task internals."""

    def __init__(self, tasks: TaskManager) -> None:
        self.tasks = tasks

    def wait(
        self,
        task_id: str | None,
        after_id: int,
        limit: int,
        timeout: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        return self.tasks.wait_events(task_id, after_id, limit, timeout, cancelled)

    def wake(self) -> None:
        self.tasks.wake_event_waiters()
