from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, Signal, Slot

from .gui_activity import TaskActivity
from .gui_presenters import localized_model_name
from .gui_requests import RequestCoordinator
from .gui_support import local_path
from .gui_tasks import TaskFeed

MODEL_OPERATIONS = {"model.import", "model.catalog.install", "model.scan"}
STARTER_MODEL_IDS = (
    "community.zh-male-young",
    "community.zh-female-senior",
)


class ModelCatalogViewModel(QObject):
    itemsChanged = Signal()
    catalogItemsChanged = Signal()
    starterInstallRequested = Signal(object)

    def __init__(
        self,
        requests: RequestCoordinator,
        activity: TaskActivity,
        task_feed: TaskFeed,
        locale_context: Any,
        parent: QObject | None = None,
        status_callback: Any = None,
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.activity = activity
        self.locale_context = locale_context
        self.status_callback = status_callback
        self._items: list[dict[str, Any]] = []
        self._catalog_items: list[dict[str, Any]] = []
        self._catalog_downloads: dict[str, float] = {}
        self._handled_tasks: set[str] = set()
        self._automatic_scan_started = False
        self._automatic_provisioning = False
        self._starter_queue: list[str] = []
        task_feed.taskUpdated.connect(self._consume_task)

    def _text(self, key: str) -> str:
        language, translations = self.locale_context()
        table = translations.get(language, translations["en"])
        return table.get(key, key)

    def _set_status(self, key: str, kind: str = "info", **values: Any) -> None:
        if self.status_callback:
            self.status_callback(self._text(key).format(**values), kind)

    def _set_items(self, result: list[dict[str, Any]]) -> None:
        self._items = result
        self.itemsChanged.emit()

    def _request_catalog(self) -> None:
        def update(result: list[dict[str, Any]]) -> None:
            self._catalog_items = result
            installed = {str(entry["id"]) for entry in result if bool(entry.get("installed"))}
            for model_id in installed:
                self._catalog_downloads.pop(model_id, None)
            self.catalogItemsChanged.emit()

        self.requests.submit(
            "model.catalog.list",
            {},
            update,
            request_key="model-catalog",
        )

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> list[dict[str, Any]]:
        language, translations = self.locale_context()
        projected = []
        for model in self._items:
            item = dict(model)
            item["localized_name"] = localized_model_name(model, language, translations)
            projected.append(item)
        return projected

    def locale_changed(self) -> None:
        self.itemsChanged.emit()
        self.catalogItemsChanged.emit()

    @Property("QVariantList", notify=catalogItemsChanged)
    def catalogItems(self) -> list[dict[str, Any]]:
        language, translations = self.locale_context()
        table = translations.get(language, translations["en"])
        projected = []
        for entry in self._catalog_items:
            item = dict(entry)
            item["localized_name"] = table.get(
                f"model.name.{entry['id']}", str(entry["display_name"])
            )
            item["localized_description"] = table.get(f"catalog.description.{entry['id']}", "")
            item["license_label"] = (
                table.get("models.license_unknown", "Unknown license")
                if entry["license_spdx"] == "LicenseRef-Unknown"
                else entry["license_spdx"]
            )
            item["download_megabytes"] = round(
                (int(entry["model_size_bytes"]) + int(entry.get("index_size_bytes") or 0))
                / (1024 * 1024),
                1,
            )
            model_id = str(entry["id"])
            item["downloading"] = model_id in self._catalog_downloads
            item["download_progress"] = self._catalog_downloads.get(model_id, 0.0)
            projected.append(item)
        return projected

    def _refresh(self, *, discover_local: bool) -> None:
        def update(result: list[dict[str, Any]]) -> None:
            self._set_items(result)
            if discover_local and not result and not self._automatic_scan_started:
                self._automatic_scan_started = True
                self.scan()

        self.requests.submit("model.list", {}, update, request_key="models")
        self._request_catalog()

    @Slot()
    def discover(self) -> None:
        """Load the library and scan configured RVC roots when it is empty."""

        self._refresh(discover_local=True)

    @Slot()
    def refresh(self) -> None:
        self._refresh(discover_local=False)

    @Slot()
    def provision(self) -> None:
        """Make a packaged first run usable without asking for setup choices."""

        if self._automatic_provisioning:
            return
        self._automatic_provisioning = True
        self._set_status("models.auto.checking")
        self._request_catalog()

        def update(result: list[dict[str, Any]]) -> None:
            self._set_items(result)
            ready = [
                item
                for item in result
                if item.get("status") == "ready" and not item.get("archived")
            ]
            if not ready and any(item.get("archived") for item in result):
                self._finish_provisioning()
                return
            installed_starters = {
                str(item["id"]) for item in ready if item.get("id") in STARTER_MODEL_IDS
            }
            if ready and not installed_starters:
                self._finish_provisioning()
                return
            if installed_starters:
                self._starter_queue = [
                    model_id for model_id in STARTER_MODEL_IDS if model_id not in installed_starters
                ]
                self._request_starter_install()
                return
            self.activity.submit(
                "model.scan",
                {},
                action_key="model-scan",
                completed=self._local_scan_completed,
                failure_callback=self._provision_failed,
            )

        self.requests.submit("model.list", {}, update, request_key="models")

    def _local_scan_completed(self, result: list[dict[str, Any]]) -> None:
        self._set_items(result)
        if any(item.get("status") == "ready" for item in result):
            self._finish_provisioning()
            return
        self._starter_queue = list(STARTER_MODEL_IDS)
        self._request_starter_install()

    def _request_starter_install(self) -> None:
        self.starterInstallRequested.emit(list(self._starter_queue))

    @Slot()
    def confirmStarterInstall(self) -> None:
        self._install_next_starter()

    @Slot()
    def declineStarterInstall(self) -> None:
        self._starter_queue.clear()
        self._automatic_provisioning = False
        self._set_status("models.auto.cancelled")

    def _install_next_starter(self) -> None:
        if not self._starter_queue:
            self._finish_provisioning()
            return
        model_id = self._starter_queue.pop(0)
        current = len(STARTER_MODEL_IDS) - len(self._starter_queue)
        self._set_status(
            "models.auto.installing",
            current=current,
            total=len(STARTER_MODEL_IDS),
        )

        def installed(model: dict[str, Any]) -> None:
            existing = [item for item in self._items if item.get("id") != model.get("id")]
            self._set_items([*existing, model])
            self._install_next_starter()

        self.activity.submit(
            "model.catalog.install",
            {"model_id": model_id},
            action_key=f"catalog-model:{model_id}",
            completed=installed,
            failure_callback=self._provision_failed,
        )

    def _provision_failed(self, _message: str) -> None:
        self._starter_queue.clear()
        self._automatic_provisioning = False

    def _finish_provisioning(self) -> None:
        self._automatic_provisioning = False
        self._set_status("models.auto.ready", "success")
        self.refresh()

    @Slot(str)
    def installCatalogModel(self, model_id: str) -> None:
        self.activity.submit(
            "model.catalog.install",
            {"model_id": model_id},
            action_key=f"catalog-model:{model_id}",
        )

    @Slot(str, bool)
    def setArchived(self, model_id: str, archived: bool) -> None:
        self.requests.submit(
            "model.archive",
            {"model_id": model_id, "archived": archived},
            lambda _result: self.refresh(),
            request_key=f"model-archive:{model_id}",
        )

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
        if task.get("operation") == "model.catalog.install":
            arguments = task.get("arguments") or {}
            model_id = str(arguments.get("model_id") or "")
            if model_id:
                state = task.get("state")
                if state in {"failed", "cancelled", "interrupted"}:
                    self._catalog_downloads.pop(model_id, None)
                elif state != "completed" or model_id in self._catalog_downloads:
                    self._catalog_downloads[model_id] = max(
                        0.0, min(1.0, float(task.get("progress") or 0.0))
                    )
                self.catalogItemsChanged.emit()
        if (
            task.get("state") == "completed"
            and task.get("operation") in MODEL_OPERATIONS
            and task_id not in self._handled_tasks
        ):
            self._handled_tasks.add(task_id)
            self.refresh()
