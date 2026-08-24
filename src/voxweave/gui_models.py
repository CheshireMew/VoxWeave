from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from .bounded_ids import BoundedIdSet
from .capabilities import STARTER_MODEL_IDS
from .gui_activity import TaskActivity
from .gui_presenters import localized_model_name, localized_text
from .gui_requests import RequestCoordinator
from .gui_support import local_path
from .gui_tasks import TaskFeed

MODEL_OPERATIONS = {"model.import", "model.catalog.install", "model.scan"}


class ModelCatalogViewModel(QObject):
    itemsChanged = Signal()
    catalogItemsChanged = Signal()
    loadingChanged = Signal()
    starterInstallRequested = Signal(object)
    conversionModelRequested = Signal(str)

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
        self._starter_details: dict[str, dict[str, Any]] = {}
        self._catalog_downloads: dict[str, float] = {}
        self._handled_tasks = BoundedIdSet()
        self._automatic_provisioning = False
        self._startup_requested = False
        self._startup_scan_started = False
        self._startup_scan_complete = False
        self._model_list_loaded = False
        self._catalog_loaded = False
        self._models_loading = False
        self._catalog_loading = False
        self._provision_pending = False
        self._startup_provisioned = False
        self._starter_queue: list[str] = []
        self._starter_total = 0
        task_feed.taskUpdated.connect(self._consume_task)

    def _set_status(self, key: str, kind: str = "info", **values: Any) -> None:
        if self.status_callback:
            self.status_callback(
                localized_text(key, self.locale_context).format(**values), kind
            )

    def _set_items(self, result: list[dict[str, Any]]) -> None:
        self._items = result
        self.itemsChanged.emit()

    def _emit_loading_changed(self) -> None:
        self.loadingChanged.emit()

    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        return (
            self._models_loading
            or self._catalog_loading
            or not self._model_list_loaded
            or not self._catalog_loaded
        )

    @Property(bool, notify=loadingChanged)
    def catalogLoading(self) -> bool:
        return self._catalog_loading or not self._catalog_loaded

    def _request_catalog(self, completed: Any = None) -> None:
        self._catalog_loading = True
        self._emit_loading_changed()

        def update(result: list[dict[str, Any]]) -> None:
            self._catalog_items = result
            self._starter_details = {
                str(entry["id"]): dict(entry)
                for entry in result
                if bool(entry.get("starter"))
                and int(entry.get("download_size_bytes") or 0) > 0
            }
            installed = {str(entry["id"]) for entry in result if bool(entry.get("installed"))}
            for model_id in installed:
                self._catalog_downloads.pop(model_id, None)
            self._catalog_loaded = True
            self._catalog_loading = False
            self.catalogItemsChanged.emit()
            self._emit_loading_changed()
            if completed:
                completed()
            self._continue_startup_provisioning()

        def failed(message: str) -> None:
            self._catalog_items = []
            self._starter_details = {}
            self._catalog_loaded = True
            self._catalog_loading = False
            self.catalogItemsChanged.emit()
            self._emit_loading_changed()
            self._provision_failed(message)
            self._set_status("models.catalog_unavailable", "danger")

        self.requests.submit(
            "model.catalog.list",
            {},
            update,
            show_status=False,
            error_callback=failed,
            request_key="model-catalog",
        )

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> list[dict[str, Any]]:
        language, translations = self.locale_context()
        table = translations.get(language, translations.get("en", {}))
        projected = []
        for model in self._items:
            item = dict(model)
            item["localized_name"] = localized_model_name(model, language, translations)
            item["license_label"] = (
                table.get("models.license_unknown", "Unknown license")
                if not model.get("license_spdx")
                or model.get("license_spdx") == "LicenseRef-Unknown"
                else str(model["license_spdx"])
            )
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

    @Property("QVariantMap", notify=catalogItemsChanged)
    def starterDetails(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._starter_details.items()}

    def _refresh(self, *, discover_local: bool) -> None:
        self._models_loading = True
        self._emit_loading_changed()

        def update(result: list[dict[str, Any]]) -> None:
            self._set_items(result)
            self._model_list_loaded = True
            self._models_loading = False
            self._emit_loading_changed()
            if discover_local:
                if not result and not self._startup_scan_started:
                    self._startup_scan_started = True
                    self.activity.submit(
                        "model.scan",
                        {},
                        action_key="model-scan",
                        completed=self._startup_scan_completed,
                        failure_callback=self._provision_failed,
                    )
                self._continue_startup_provisioning()

        def failed(_message: str) -> None:
            self._model_list_loaded = True
            self._models_loading = False
            self._emit_loading_changed()
            self._provision_failed(_message)

        self.requests.submit(
            "model.list",
            {},
            update,
            error_callback=failed,
            request_key="models",
        )
        self._request_catalog()

    @Slot()
    def discover(self) -> None:
        """Load model and catalog state once; runtime readiness owns provisioning."""

        if self._startup_requested:
            return
        self._startup_requested = True
        self._refresh(discover_local=True)

    @Slot()
    def refresh(self) -> None:
        self._refresh(discover_local=False)

    @Slot()
    def provision(self) -> None:
        """Make a packaged first run usable without asking for setup choices."""

        if self._startup_provisioned or self._automatic_provisioning:
            return
        self._provision_pending = True
        if not self._startup_requested:
            self.discover()
        self._continue_startup_provisioning()

    def _continue_startup_provisioning(self) -> None:
        if (
            not self._provision_pending
            or self._startup_provisioned
            or self._automatic_provisioning
            or not self._model_list_loaded
            or not self._catalog_loaded
            or (self._startup_scan_started and not self._startup_scan_complete)
        ):
            return
        self._provision_pending = False
        self._automatic_provisioning = True
        self._set_status("models.auto.checking")
        ready = [
            item
            for item in self._items
            if item.get("status") == "ready" and not item.get("archived")
        ]
        # Startup provisioning exists to make an empty installation usable. Once
        # any active model is ready, prompting for additional starter models is
        # both unnecessary and contradicts the dialog's "no usable models" text.
        if ready:
            self._finish_provisioning()
            return
        archived_ready = [
            item
            for item in self._items
            if item.get("status") == "ready" and item.get("archived")
        ]
        if archived_ready:
            self._finish_provisioning("models.auto.archived", "warning")
            return
        if self._startup_scan_complete:
            self._starter_queue = self._available_starter_ids()
            self._request_starter_install()
        else:
            self._startup_scan_started = True
            self.activity.submit(
                "model.scan",
                {},
                action_key="model-scan",
                completed=self._local_scan_completed,
                failure_callback=self._provision_failed,
            )

    def _startup_scan_completed(self, result: list[dict[str, Any]]) -> None:
        self._startup_scan_complete = True
        self._set_items(result)
        self._continue_startup_provisioning()

    def _local_scan_completed(self, result: list[dict[str, Any]]) -> None:
        self._set_items(result)
        if any(
            item.get("status") == "ready" and not item.get("archived")
            for item in result
        ):
            self._finish_provisioning()
            return
        if any(
            item.get("status") == "ready" and item.get("archived")
            for item in result
        ):
            self._finish_provisioning("models.auto.archived", "warning")
            return
        self._starter_queue = self._available_starter_ids()
        self._request_starter_install()

    def _available_starter_ids(self) -> list[str]:
        return [
            model_id
            for model_id in STARTER_MODEL_IDS
            if model_id in self._starter_details
        ]

    def _request_starter_install(self) -> None:
        if not self._starter_queue:
            self._finish_provisioning("models.auto.no_starter", "warning")
            return
        self._starter_total = len(self._starter_queue)
        self.starterInstallRequested.emit(list(self._starter_queue))

    @Slot()
    def confirmStarterInstall(self) -> None:
        self._install_next_starter()

    @Slot()
    def declineStarterInstall(self) -> None:
        self._starter_queue.clear()
        self._automatic_provisioning = False
        self._startup_provisioned = True
        self._set_status("models.auto.cancelled")

    def _install_next_starter(self) -> None:
        if not self._starter_queue:
            self._finish_provisioning()
            return
        model_id = self._starter_queue.pop(0)
        current = self._starter_total - len(self._starter_queue)
        self._set_status(
            "models.auto.installing",
            current=current,
            total=self._starter_total,
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

    def _finish_provisioning(
        self, status_key: str = "models.auto.ready", kind: str = "success"
    ) -> None:
        self._automatic_provisioning = False
        self._startup_provisioned = True
        self._set_status(status_key, kind)

    @Slot(str)
    def useInConversion(self, model_id: str) -> None:
        if any(
            item.get("id") == model_id
            and item.get("status") == "ready"
            and not item.get("archived")
            for item in self._items
        ):
            self.conversionModelRequested.emit(model_id)

    @Slot(str)
    def openSource(self, source_url: str) -> None:
        url = QUrl(source_url)
        if url.scheme().casefold() != "https" or not QDesktopServices.openUrl(url):
            self._set_status("models.source_open_failed", "danger")

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

    @Slot("QVariantMap")
    def importLocal(self, value: dict[str, Any]) -> None:
        command = dict(value)
        model_value = str(command["model"])
        index_value = str(command.get("index") or "")
        arguments: dict[str, Any] = {"model": local_path(model_value)}
        optional = {
            "index": local_path(index_value) if index_value else "",
            "id": str(command.get("id") or "").strip(),
            "display_name": str(command.get("display_name") or "").strip(),
            "license_spdx": str(command.get("license_spdx") or "").strip(),
            "source_url": str(command.get("source_url") or "").strip(),
        }
        arguments.update({key: value for key, value in optional.items() if value})
        self.activity.submit("model.import", arguments, action_key="model-import")

    @Slot(str, str)
    def chooseIndex(self, model_id: str, index_path: str) -> None:
        model = next((item for item in self._items if item.get("id") == model_id), None)
        if model is None or index_path not in list(model.get("index_candidates") or []):
            self._set_status("models.index_choice_invalid", "danger")
            return
        self.importLocal(
            {
                "model": str(model["model_path"]),
                "index": index_path,
                "id": model_id,
                "display_name": str(model["display_name"]),
                "license_spdx": str(model.get("license_spdx") or ""),
                "source_url": str(model.get("source_url") or ""),
            }
        )

    @Slot("QVariantMap")
    def importUrl(self, value: dict[str, Any]) -> None:
        command = dict(value)
        arguments = {
            "model": str(command["model"]).strip(),
            "id": str(command["id"]).strip(),
            "display_name": str(command["display_name"]).strip(),
            "license_spdx": str(command["license_spdx"]).strip(),
            "model_sha256": str(command["model_sha256"]).strip(),
            "download_size_bytes": int(command["download_size_bytes"]),
        }
        source_url = str(command.get("source_url") or "")
        if source_url.strip():
            arguments["source_url"] = source_url.strip()
        index_url = str(command.get("index_url") or "").strip()
        if index_url:
            arguments.update(
                {
                    "index_url": index_url,
                    "index_sha256": str(command["index_sha256"]).strip(),
                    "index_size_bytes": int(command["index_size_bytes"]),
                }
            )
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
