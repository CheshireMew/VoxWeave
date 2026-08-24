from __future__ import annotations

import threading
from typing import Any

from .config import Settings
from .settings_file_store import SettingsFileStore
from .settings_repository import SettingsRepository


class SettingsService:
    def __init__(
        self,
        settings: Settings,
        repository: SettingsRepository,
        file_store: SettingsFileStore | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.file_store = file_store or SettingsFileStore(settings)
        self._lock = threading.RLock()
        self.file_store.ensure_persisted(settings)
        self.repository.record(settings.revision, [], settings.payload())

    def _record_change(self, result: dict[str, Any]) -> None:
        self.repository.record(
            int(result["revision"]),
            list(result["changed_fields"]),
            dict(result["settings"]),
        )

    def get(self) -> dict[str, Any]:
        return self.settings.payload()

    def replace(self, *, expected_revision: int | None = None, **changes: Any) -> dict[str, Any]:
        with self._lock:
            result = self.file_store.commit(
                self.settings,
                expected_revision=expected_revision,
                **changes,
            )
            if result["changed_fields"]:
                self._record_change(result)
            return result

    def update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        expected_revision = int(arguments["expected_revision"])
        changes: dict[str, Any] = {}
        if "language" in arguments:
            changes["language"] = arguments["language"]
        if "realtime" in arguments:
            changes["realtime"] = {
                **self.settings.realtime,
                **dict(arguments["realtime"]),
            }
        return self.replace(expected_revision=expected_revision, **changes)

    def events(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "events": self.repository.events(
                int(arguments["after_revision"]),
                int(arguments["limit"]),
            ),
            "current_revision": self.settings.revision,
        }
