from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .bounded_ids import BoundedIdSet
from .gui_activity import TaskActivity
from .gui_requests import RequestCoordinator
from .gui_support import local_path
from .gui_tasks import TaskFeed


class BatchRulesViewModel(QObject):
    itemsChanged = Signal()
    loadingChanged = Signal()
    ruleSaved = Signal(str)

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
        self._loading = False
        self._loaded = False
        self._handled_tasks = BoundedIdSet()
        task_feed.taskUpdated.connect(self._consume_task)

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> list[dict[str, Any]]:
        return self._items

    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        return self._loading or not self._loaded

    @Slot()
    def refresh(self) -> None:
        self._loading = True
        self.loadingChanged.emit()

        def update(result: dict[str, Any]) -> None:
            self._items = list(result["items"])
            self._loading = False
            self._loaded = True
            self.itemsChanged.emit()
            self.loadingChanged.emit()

        def failed(message: str) -> None:
            self._loading = False
            self._loaded = True
            self.loadingChanged.emit()
            self.requests.status_callback(message, "danger")

        self.requests.submit(
            "batch.list",
            {"limit": 100},
            update,
            show_status=False,
            error_callback=failed,
            request_key="batches",
        )

    @Slot("QVariantMap")
    def saveRule(self, value: dict[str, Any]) -> None:
        payload = dict(value)
        batch_id = str(payload.pop("batch_id", "") or "")
        payload["input_root"] = local_path(str(payload["input_root"]))
        payload["output_root"] = local_path(str(payload["output_root"]))
        payload.setdefault("preset_name", "custom")
        payload.setdefault("recursive", True)

        def saved(result: dict[str, Any]) -> None:
            self.refresh()
            self.ruleSaved.emit(str(result["id"]))

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
    def plan(self, batch_id: str) -> None:
        self.activity.submit(
            "batch.plan",
            {"batch_id": batch_id},
            action_key=f"batch-plan:{batch_id}",
        )

    @Slot(str)
    def retry(self, batch_id: str) -> None:
        self.activity.submit(
            "batch.retry",
            {"batch_id": batch_id},
            action_key=f"batch-retry:{batch_id}",
        )

    @Slot(str, "QVariantMap")
    def retryItem(self, item_id: str, variant: dict[str, Any]) -> None:
        self.requests.submit(
            "batch.item.retry",
            {"item_id": item_id, "variant": dict(variant)},
            lambda _result: self.refresh(),
            request_key=f"batch-item-retry:{item_id}",
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
