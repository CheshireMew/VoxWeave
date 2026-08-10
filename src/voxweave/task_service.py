from __future__ import annotations

from typing import Any

from .artifacts import ArtifactStore
from .batch import BatchManager
from .task_manager import TaskManager


class TaskService:
    def __init__(
        self,
        tasks: TaskManager,
        artifacts: ArtifactStore,
        batches: BatchManager,
    ) -> None:
        self.tasks = tasks
        self.artifacts = artifacts
        self.batches = batches

    def get(self, task_id: str) -> dict[str, Any]:
        task = self.tasks.get(task_id)
        task["artifacts"] = self.artifacts.list_for_task(task_id)
        return task

    def retry(
        self,
        task_id: str,
        *,
        request_id: str | None,
        actor: dict[str, Any] | None,
    ) -> dict[str, Any]:
        task = self.tasks.retry(task_id, request_id=request_id, actor=actor)
        self.batches.relink_retry(task_id, task["task_id"])
        return task
