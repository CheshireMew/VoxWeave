from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .gui_activity import TaskActivity
from .gui_presenters import localized_model_name
from .gui_requests import RequestCoordinator
from .gui_support import local_path
from .gui_tasks import TaskFeed

MODEL_OPERATIONS = {"model.import", "model.catalog.install", "model.scan"}


class ModelCatalogViewModel(QObject):
    itemsChanged = Signal()

    def __init__(
        self,
        requests: RequestCoordinator,
        activity: TaskActivity,
        task_feed: TaskFeed,
        locale_context: Any,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.activity = activity
        self.locale_context = locale_context
        self._items: list[dict[str, Any]] = []
        self._handled_tasks: set[str] = set()
        task_feed.taskUpdated.connect(self._consume_task)

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> list[dict[str, Any]]:
        language, translations = self.locale_context()
        projected = []
        for model in self._items:
            item = dict(model)
            item["localized_name"] = localized_model_name(
                model, language, translations
            )
            projected.append(item)
        return projected

    def locale_changed(self) -> None:
        self.itemsChanged.emit()

    @Slot()
    def refresh(self) -> None:
        def update(result: list[dict[str, Any]]) -> None:
            self._items = result
            self.itemsChanged.emit()

        self.requests.submit("model.list", {}, update, request_key="models")

    @Slot()
    def scan(self) -> None:
        self.activity.submit("model.scan", {}, action_key="model-scan")

    @Slot(str)
    def scanWeightRoot(self, root_value: str) -> None:
        self.activity.submit(
            "model.scan",
            {"weight_roots": [local_path(root_value)], "remember_roots": True},
            action_key="model-scan",
        )

    @Slot(str)
    def scanIndexRoot(self, root_value: str) -> None:
        self.activity.submit(
            "model.scan",
            {"index_roots": [local_path(root_value)], "remember_roots": True},
            action_key="model-scan",
        )

    @Slot(str, str, str, str, str, str)
    def importLocal(
        self,
        model_value: str,
        index_value: str,
        model_id: str,
        display_name: str,
        license_spdx: str,
        source_url: str,
    ) -> None:
        arguments: dict[str, Any] = {"model": local_path(model_value)}
        optional = {
            "index": local_path(index_value) if index_value else "",
            "id": model_id.strip(),
            "display_name": display_name.strip(),
            "license_spdx": license_spdx.strip(),
            "source_url": source_url.strip(),
        }
        arguments.update({key: value for key, value in optional.items() if value})
        self.activity.submit("model.import", arguments, action_key="model-import")

    @Slot(str, str, str, str, str, int, str)
    def importUrl(
        self,
        model_url: str,
        model_id: str,
        display_name: str,
        license_spdx: str,
        model_sha256: str,
        download_size_bytes: int,
        source_url: str,
    ) -> None:
        arguments = {
            "model": model_url.strip(),
            "id": model_id.strip(),
            "display_name": display_name.strip(),
            "license_spdx": license_spdx.strip(),
            "model_sha256": model_sha256.strip(),
            "download_size_bytes": download_size_bytes,
        }
        if source_url.strip():
            arguments["source_url"] = source_url.strip()
        self.activity.submit("model.import", arguments, action_key="model-import")

    @Slot(object)
    def _consume_task(self, value: object) -> None:
        task = dict(value)
        task_id = str(task["id"])
        if (
            task.get("state") == "completed"
            and task.get("operation") in MODEL_OPERATIONS
            and task_id not in self._handled_tasks
        ):
            self._handled_tasks.add(task_id)
            self.refresh()
