from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from .config import Settings
from .gui_activity import TaskActivity
from .gui_support import local_path


class MaintenanceViewModel(QObject):
    runtimeChanged = Signal()
    diagnosticPathChanged = Signal()

    def __init__(
        self,
        settings: Settings,
        activity: TaskActivity,
        status_callback: Any,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.activity = activity
        self.status_callback = status_callback
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

    @Property(str, notify=diagnosticPathChanged)
    def diagnosticPath(self) -> str:
        return self._diagnostic_path

    @Slot()
    def inspectRuntime(self) -> None:
        def update(result: dict[str, Any]) -> None:
            self._runtime = result
            self.runtimeChanged.emit()

        self.activity.submit(
            "runtime.inspect", {}, action_key="runtime-inspect", completed=update
        )

    @Slot(str)
    def exportDiagnostics(self, path_value: str) -> None:
        target = Path(local_path(path_value)).expanduser().resolve()
        if target.suffix.casefold() != ".json":
            target = target.with_suffix(".json")
        if target.exists():
            self.status_callback(f"diagnostic file already exists: {target}", "danger")
            return

        def write(payload: dict[str, Any]) -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._diagnostic_path = str(target)
            self.diagnosticPathChanged.emit()
            self.status_callback(f"diagnostics exported: {target}", "success")

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
