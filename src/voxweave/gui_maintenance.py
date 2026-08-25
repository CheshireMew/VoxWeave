from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from . import __version__
from .config import Settings
from .gui_activity import TaskActivity
from .gui_support import local_path
from .process_control import start_managed_process
from .runtime_verification import load_runtime_verification, save_runtime_verification
from .updater import RELEASES_PAGE_URL


class MaintenanceViewModel(QObject):
    runtimeChanged = Signal()
    runtimeAvailable = Signal()
    runtimeInstalled = Signal()
    runtimeInstallRequested = Signal()
    diagnosticPathChanged = Signal()
    storageChanged = Signal()
    updateChanged = Signal()

    def __init__(
        self,
        settings: Settings,
        activity: TaskActivity,
        status_callback: Any,
        text_callback: Any = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.activity = activity
        self.status_callback = status_callback
        self.text_callback = text_callback or (lambda key: key)
        self._runtime: dict[str, Any] = {}
        self._diagnostic_path = ""
        self._storage: dict[str, Any] = {}
        self._update: dict[str, Any] = {
            "current_version": __version__,
            "release_url": RELEASES_PAGE_URL,
        }

    @Property(str, constant=True)
    def dataRootUrl(self) -> str:
        return QUrl.fromLocalFile(str(self.settings.root)).toString()

    @Property(str, constant=True)
    def dataRoot(self) -> str:
        return str(self.settings.root)

    @Property(str, notify=runtimeChanged)
    def runtimeText(self) -> str:
        return json.dumps(self._runtime, ensure_ascii=False, indent=2)

    @Property("QVariantMap", notify=runtimeChanged)
    def runtimeInfo(self) -> dict[str, Any]:
        doctor = self._runtime.get("doctor") or {}
        return {
            "ready": bool(self._runtime.get("ready")),
            "device": doctor.get("device") or "—",
            "python": self._runtime.get("rvc_python") or "—",
            "rvc_root": self._runtime.get("rvc_root") or "—",
            "ffmpeg": self._runtime.get("ffmpeg") or "—",
        }

    @Property(bool, notify=runtimeChanged)
    def runtimeReady(self) -> bool:
        return bool(self._runtime.get("ready"))

    @Slot()
    def openDataRoot(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.settings.root)))

    @Property(str, notify=runtimeChanged)
    def runtimeError(self) -> str:
        return str(self._runtime.get("error") or "")

    @Property(str, notify=diagnosticPathChanged)
    def diagnosticPath(self) -> str:
        return self._diagnostic_path

    @Property("QVariantMap", notify=storageChanged)
    def storage(self) -> dict[str, Any]:
        return dict(self._storage)

    @Property("QVariantMap", notify=updateChanged)
    def updateInfo(self) -> dict[str, Any]:
        return dict(self._update)

    @Slot()
    def inspectRuntime(self) -> None:
        def update(result: dict[str, Any]) -> None:
            self._runtime = result
            save_runtime_verification(self.settings, result)
            self.runtimeChanged.emit()

        self.activity.submit("runtime.inspect", {}, action_key="runtime-inspect", completed=update)

    @Slot()
    def ensureRuntime(self) -> None:
        """Trust a matching prior verification or run the first full inspection."""

        cached = load_runtime_verification(self.settings)
        if cached is not None:
            self._runtime = cached
            self.runtimeChanged.emit()
            self.runtimeAvailable.emit()
            return

        def update(result: dict[str, Any]) -> None:
            self._runtime = result
            save_runtime_verification(self.settings, result)
            self.runtimeChanged.emit()
            if self.runtimeReady:
                self.runtimeAvailable.emit()
                return
            self.status_callback(self.text_callback("runtime.install_needed"), "info")
            self.runtimeInstallRequested.emit()

        self.activity.submit("runtime.inspect", {}, action_key="runtime-inspect", completed=update)

    def _install_runtime(self) -> None:
        def update(result: dict[str, Any]) -> None:
            self._runtime = dict(result)
            save_runtime_verification(self.settings, result)
            self.runtimeChanged.emit()
            if self.runtimeReady:
                self.runtimeInstalled.emit()
                self.runtimeAvailable.emit()

        self.activity.submit(
            "runtime.install",
            {},
            action_key="runtime-install",
            completed=update,
        )

    @Slot()
    def installRuntime(self) -> None:
        self._install_runtime()

    @Slot(str)
    def exportDiagnostics(self, path_value: str) -> None:
        target = Path(local_path(path_value)).expanduser().resolve()
        if target.suffix.casefold() != ".json":
            target = target.with_suffix(".json")
        if target.exists():
            self.status_callback(
                self.text_callback("diagnostics.target_exists").format(path=target),
                "danger",
            )
            return

        def write(payload: dict[str, Any]) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._diagnostic_path = str(target)
            self.diagnosticPathChanged.emit()
            self.status_callback(
                self.text_callback("diagnostics.exported").format(path=target),
                "success",
            )

        self.activity.submit(
            "diagnostics.snapshot",
            {},
            action_key="diagnostics-export",
            completed=write,
        )

    @Slot(str, int)
    def archiveArtifacts(self, destination_value: str, older_than_days: int) -> None:
        self.activity.submit(
            "storage.archive",
            {
                "destination_root": local_path(destination_value),
                "older_than_days": older_than_days,
                "confirm_source_removal": True,
            },
            action_key="storage-archive",
        )

    @Slot(str, int, "QVariantList")
    def archiveArtifactStates(
        self,
        destination_value: str,
        older_than_days: int,
        states: list[str],
    ) -> None:
        self.activity.submit(
            "storage.archive",
            {
                "destination_root": local_path(destination_value),
                "older_than_days": older_than_days,
                "states": list(states),
                "confirm_source_removal": True,
            },
            action_key="storage-archive",
        )

    @Slot(int)
    def inspectStorage(self, older_than_days: int = 30) -> None:
        def updated(result: dict[str, Any]) -> None:
            self._storage = dict(result)
            self.storageChanged.emit()

        self.activity.submit(
            "storage.inspect",
            {"older_than_days": older_than_days},
            action_key="storage-inspect",
            completed=updated,
        )

    @Slot()
    def checkForUpdates(self) -> None:
        def updated(result: dict[str, Any]) -> None:
            self._update = {**self._update, **result}
            self.updateChanged.emit()

        self.activity.submit(
            "update.check",
            {"include_prerelease": False},
            action_key="update-check",
            completed=updated,
        )

    @Slot()
    def downloadUpdate(self) -> None:
        version = str(self._update.get("latest_version") or "")
        if not version:
            return

        def updated(result: dict[str, Any]) -> None:
            self._update = {**self._update, **result}
            self.updateChanged.emit()

        self.activity.submit(
            "update.download",
            {"version": version},
            action_key="update-download",
            completed=updated,
        )

    @Slot(str)
    def restoreArtifacts(self, task_ids_value: str) -> None:
        task_ids = [
            value.strip()
            for value in task_ids_value.replace("\n", ",").split(",")
            if value.strip()
        ]
        if not task_ids:
            return
        self.activity.submit(
            "storage.restore",
            {"task_ids": task_ids},
            action_key="storage-restore",
            completed=lambda _result: self.inspectStorage(30),
        )

    @Slot(str)
    def planStorageMigration(self, target_value: str) -> None:
        target = local_path(target_value)

        def planned(result: dict[str, Any]) -> None:
            self._storage = {**self._storage, "migration_plan": dict(result)}
            self.storageChanged.emit()

        self.activity.submit(
            "storage.migration.plan",
            {"target_root": target},
            action_key="storage-migration-plan",
            completed=planned,
        )

    @Slot()
    def prepareStorageMigration(self) -> None:
        plan = dict(self._storage.get("migration_plan") or {})
        if not plan or plan.get("conflicts"):
            return

        def prepared(result: dict[str, Any]) -> None:
            self._storage = {**self._storage, "migration": dict(result)}
            self.storageChanged.emit()
            start_managed_process(list(result["bootstrap_command"]))
            QTimer.singleShot(0, QApplication.quit)

        self.activity.requests.submit(
            "storage.migration.prepare",
            {
                "target_root": plan["target_root"],
                "plan_digest": plan["plan_digest"],
            },
            prepared,
            request_key="storage-migration-prepare",
        )

    @Slot()
    def installUpdate(self) -> None:
        version = str(self._update.get("latest_version") or "")
        if not version or not self._update.get("downloaded_path"):
            return

        def installed(result: dict[str, Any]) -> None:
            self._update = {**self._update, **result}
            self.updateChanged.emit()

        self.activity.submit(
            "update.install",
            {"version": version},
            action_key="update-install",
            completed=installed,
        )

    def _start_update_bootstrap(self, result: dict[str, Any]) -> None:
        self._update = {**self._update, **result}
        self.updateChanged.emit()
        start_managed_process(list(result["bootstrap_command"]))
        QTimer.singleShot(0, QApplication.quit)

    @Slot()
    def activateUpdate(self) -> None:
        version = str(self._update.get("version") or self._update.get("latest_version") or "")
        if not version:
            return
        self.activity.requests.submit(
            "update.activate",
            {"version": version},
            self._start_update_bootstrap,
            request_key="update-activate",
        )

    @Slot()
    def rollbackUpdate(self) -> None:
        self.activity.requests.submit(
            "update.rollback",
            {},
            self._start_update_bootstrap,
            request_key="update-rollback",
        )

    @Slot()
    def openUpdate(self) -> None:
        path = str(self._update.get("downloaded_path") or "")
        url = QUrl.fromLocalFile(path) if path else QUrl(str(self._update.get("release_url") or ""))
        if url.isValid():
            QDesktopServices.openUrl(url)
