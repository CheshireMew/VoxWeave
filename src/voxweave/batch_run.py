from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .batch_repository import BatchRepository
from .batch_submission import BatchSubmissionService
from .protocol import OperationError
from .task_manager import DeferredTask, TaskContext, TaskManager

TERMINAL_TASK_STATES = {"completed", "failed", "cancelled", "interrupted"}


class BatchRunCoordinator:
    """Owns parent/child batch task creation, retry and durable aggregation."""

    def __init__(
        self,
        repository: BatchRepository,
        tasks: TaskManager,
        submissions: BatchSubmissionService,
        get_rule: Callable[[str], dict[str, Any]],
    ) -> None:
        self.repository = repository
        self.tasks = tasks
        self.submissions = submissions
        self.get_rule = get_rule

    def run(
        self, arguments: dict[str, Any], context: TaskContext
    ) -> dict[str, Any] | DeferredTask:
        batch_id = arguments["batch_id"]
        rule = self.get_rule(batch_id)
        if rule["state"] != "active":
            raise ValueError(f"batch rule is not active: {batch_id}")
        item_ids: list[str] = []
        failures = []
        files = self.submissions.files(rule, context.cancelled)
        total = max(1, len(files))
        for index, path in enumerate(files):
            if context.cancelled():
                raise InterruptedError("task cancellation requested")
            context.progress(index / total, "enumerating", f"{index}/{len(files)} files")
            try:
                item_ids.append(self.submissions.submit_file(rule, path, context.cancelled))
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
        self.get_rule(batch_id)
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

    def sync(self) -> None:
        self._sync_items()
        self._sync_runs()

    def _sync_items(self) -> None:
        for item in self.repository.pending_items():
            if item["state"] != item["task_state"] or item.get("error") != item.get(
                "task_error"
            ):
                self.repository.update_item_state(
                    item["id"], item["task_state"], item.get("task_error")
                )

    def _sync_runs(self) -> None:
        for run in self.repository.active_runs():
            item_ids = json.loads(run["item_ids_json"])
            items = self.repository.items_by_ids(item_ids)
            parent = self.tasks.get(run["id"])
            if parent["cancel_requested"]:
                for item in items:
                    if item["task_id"]:
                        self.tasks.cancel(item["task_id"])
            if len(items) != len(item_ids) or any(
                item["state"] not in TERMINAL_TASK_STATES for item in items
            ):
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
