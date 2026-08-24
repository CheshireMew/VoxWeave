from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .batch_repository import BatchRepository
from .batch_rules import BatchRuleService
from .batch_run import BatchRunCoordinator
from .batch_submission import BatchSubmissionService
from .batch_watch import BatchWatchSupervisor
from .database import Database
from .task_manager import DeferredTask, TaskContext, TaskManager


class BatchManager:
    """Composition boundary for the batch subsystem's focused services."""

    def __init__(
        self,
        database: Database,
        tasks: TaskManager,
        resolve_model: Callable[[str], dict[str, Any]],
    ) -> None:
        repository = BatchRepository(database)
        self.rules = BatchRuleService(repository, resolve_model)
        self.submissions = BatchSubmissionService(database, repository, tasks)
        self.runs = BatchRunCoordinator(
            repository,
            tasks,
            self.submissions,
            self.rules.get,
        )
        self.watch = BatchWatchSupervisor(repository, self.submissions, self.runs)

    def start(self) -> None:
        self.watch.start()

    def create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.rules.create(arguments)

    def update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.rules.update(arguments)

    def archive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.rules.archive(arguments)

    def get(self, batch_id: str) -> dict[str, Any]:
        return self.rules.get(batch_id)

    def list(self, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
        return self.rules.list(limit, cursor)

    def set_watch(self, batch_id: str, enabled: bool) -> dict[str, Any]:
        return self.rules.set_watch(batch_id, enabled)

    def run(
        self,
        arguments: dict[str, Any],
        context: TaskContext,
    ) -> dict[str, Any] | DeferredTask:
        return self.runs.run(arguments, context)

    def retry(
        self,
        arguments: dict[str, Any],
        context: TaskContext,
    ) -> dict[str, Any] | DeferredTask:
        return self.runs.retry(arguments, context)

    def relink_retry(self, previous_task_id: str, task_id: str) -> None:
        self.runs.relink_retry(previous_task_id, task_id)

    def durable_task_ids(self) -> set[str]:
        return self.runs.durable_task_ids()

    def shutdown(self) -> None:
        self.watch.shutdown()
