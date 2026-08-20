from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .gui_activity import TaskActivity
from .gui_requests import RequestCoordinator
from .gui_support import local_path
from .gui_tasks import TaskFeed


class BatchRulesViewModel(QObject):
    itemsChanged = Signal()

    def __init__(
        self,
        requests: RequestCoordinator,
        activity: TaskActivity,
        task_feed: TaskFeed,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.activity = activity
        self._items: list[dict[str, Any]] = []
        self._handled_tasks: set[str] = set()
        task_feed.taskUpdated.connect(self._consume_task)

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> list[dict[str, Any]]:
        return self._items

    @Slot()
    def refresh(self) -> None:
        def update(result: dict[str, Any]) -> None:
            self._items = list(result["items"])
            self.itemsChanged.emit()

        self.requests.submit(
            "batch.list",
            {"limit": 100},
            update,
            show_status=False,
            request_key="batches",
        )

    @Slot(str, str, str, bool)
    def create(self, input_root: str, output_root: str, model: str, watch: bool) -> None:
        def created(result: dict[str, Any]) -> None:
            self.refresh()
            if not watch:
                self.activity.submit(
                    "batch.run",
                    {"batch_id": result["id"]},
                    action_key=f"batch-run:{result['id']}",
                )

        self.requests.submit(
            "batch.create",
            {
                "input_root": local_path(input_root),
                "output_root": local_path(output_root),
                "model": model,
                "preset": {"content_mode": "clean"},
                "preset_name": "default",
                "recursive": True,
                "watch": watch,
            },
            created,
        )

    @Slot("QVariantMap")
    def saveRule(self, value: dict[str, Any]) -> None:
        payload = dict(value)
        batch_id = str(payload.pop("batch_id", "") or "")
        watch = bool(payload.get("watch", False))
        payload["input_root"] = local_path(str(payload["input_root"]))
        payload["output_root"] = local_path(str(payload["output_root"]))
        payload.setdefault("preset_name", "custom")
        payload.setdefault("recursive", True)

        def saved(result: dict[str, Any]) -> None:
            self.refresh()
            if not batch_id and not watch:
                self.activity.submit(
                    "batch.run",
                    {"batch_id": result["id"]},
                    action_key=f"batch-run:{result['id']}",
                )

        if batch_id:
            payload["batch_id"] = batch_id
        self.requests.submit(
            "batch.update" if batch_id else "batch.create",
            payload,
            saved,
        )

    @Slot(str, bool)
    def setArchived(self, batch_id: str, archived: bool) -> None:
        self.requests.submit(
            "batch.archive",
            {"batch_id": batch_id, "archived": archived},
            lambda _result: self.refresh(),
            request_key=f"batch-archive:{batch_id}",
        )

    @Slot(str)
    def run(self, batch_id: str) -> None:
        self.activity.submit(
            "batch.run",
            {"batch_id": batch_id},
            action_key=f"batch-run:{batch_id}",
        )

    @Slot(str)
    def retry(self, batch_id: str) -> None:
        self.activity.submit(
            "batch.retry",
            {"batch_id": batch_id},
            action_key=f"batch-retry:{batch_id}",
        )

    @Slot(str, bool)
    def setWatch(self, batch_id: str, enabled: bool) -> None:
        self.requests.submit(
            "batch.watch",
            {"batch_id": batch_id, "enabled": enabled},
            lambda _result: self.refresh(),
            request_key=f"batch-watch:{batch_id}",
        )

    @Slot(object)
    def _consume_task(self, value: object) -> None:
        task = dict(value)
        task_id = str(task["id"])
        if (
            task.get("state") == "completed"
            and task.get("operation") in {"batch.run", "batch.retry"}
            and task_id not in self._handled_tasks
        ):
            self._handled_tasks.add(task_id)
            self.refresh()
