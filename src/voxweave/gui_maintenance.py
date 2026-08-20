from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from .config import Settings
from .gui_activity import TaskActivity
from .gui_support import local_path
from .runtime_verification import load_runtime_verification, save_runtime_verification


class MaintenanceViewModel(QObject):
    runtimeChanged = Signal()
    runtimeAvailable = Signal()
    runtimeInstalled = Signal()
    runtimeInstallRequested = Signal()
    diagnosticPathChanged = Signal()

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
