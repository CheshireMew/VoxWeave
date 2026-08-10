from __future__ import annotations

import json
import sys

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from .client import request_json
from .config import PACKAGE_ROOT, Settings, configure_process_environment, load_settings
from .gui_activity import TaskActivity
from .gui_batches import BatchRulesViewModel
from .gui_maintenance import MaintenanceViewModel
from .gui_media import MediaViewModel
from .gui_models import ModelCatalogViewModel
from .gui_realtime import RealtimeViewModel
from .gui_requests import RequestCoordinator
from .gui_tasks import TaskFeed, TaskListViewModel


class Bridge(QObject):
    """Application composition root exposed to QML."""

    statusChanged = Signal()
    languageChanged = Signal()

    def __init__(self, settings: Settings, *, start_background: bool = True):
        super().__init__()
        self.settings = settings
        self._status = "Ready"
        self._status_kind = "success"
        self._language = settings.language
        translations_path = PACKAGE_ROOT / "resources" / "translations.json"
        self.translations = json.loads(translations_path.read_text(encoding="utf-8"))

        self.requests = RequestCoordinator(
            settings, request_json, self._set_status, parent=self
        )
        self._task_feed = TaskFeed(settings, self.requests, self)
        self._activity = TaskActivity(
            self.requests, self._task_feed, self._set_status, self
        )
        def locale_context() -> tuple[str, dict[str, dict[str, str]]]:
            return self._language, self.translations
        self._model_catalog = ModelCatalogViewModel(
            self.requests, self._activity, self._task_feed, locale_context, self
        )
        self._task_list = TaskListViewModel(
            self._task_feed,
            self.requests,
            locale_context,
            self._set_status,
            self,
        )
        self._media = MediaViewModel(
            self.requests, self._activity, self._task_feed, self
        )
        self._realtime = RealtimeViewModel(
            self.requests, self._set_status, self.text, self
        )
        self._batch_rules = BatchRulesViewModel(
            self.requests, self._activity, self._task_feed, self
        )
        self._maintenance = MaintenanceViewModel(
            settings, self._activity, self._set_status, self
        )
        if start_background:
            QTimer.singleShot(0, self._task_feed.start)
            QTimer.singleShot(0, self._model_catalog.refresh)
            QTimer.singleShot(0, self._batch_rules.refresh)
            QTimer.singleShot(0, self._realtime.start)

    @Property(QObject, constant=True)
    def activity(self) -> TaskActivity:
        return self._activity

    @Property(QObject, constant=True)
    def modelCatalog(self) -> ModelCatalogViewModel:
        return self._model_catalog

    @Property(QObject, constant=True)
    def taskList(self) -> TaskListViewModel:
        return self._task_list

    @Property(QObject, constant=True)
    def media(self) -> MediaViewModel:
        return self._media

    @Property(QObject, constant=True)
    def realtime(self) -> RealtimeViewModel:
        return self._realtime

    @Property(QObject, constant=True)
    def batchRules(self) -> BatchRulesViewModel:
        return self._batch_rules

    @Property(QObject, constant=True)
    def maintenance(self) -> MaintenanceViewModel:
        return self._maintenance

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=statusChanged)
    def statusKind(self) -> str:
        return self._status_kind

    @Property(str, notify=languageChanged)
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        if value not in self.translations or value == self._language:
            return
        previous = self._language
        self._language = value
        self._emit_locale_changed()

        def rollback(message: str) -> None:
            self._language = previous
            self._emit_locale_changed()
            self._set_status(message, "danger")

        self.requests.submit(
            "settings.update",
            {"language": value},
            show_status=False,
            error_callback=rollback,
            request_key="language",
        )

    def _emit_locale_changed(self) -> None:
        self.languageChanged.emit()
        self._model_catalog.locale_changed()
        self._task_list.locale_changed()

    @Slot(str, result=str)
    def text(self, key: str) -> str:
        return self.translations.get(self._language, {}).get(key, key)

    def _set_status(self, value: str, kind: str = "info") -> None:
        self._status = value
        self._status_kind = kind
        self.statusChanged.emit()

    @Slot()
    def shutdown(self) -> None:
        self._realtime.shutdown()
        self._task_feed.shutdown()
        self.requests.shutdown()


def main() -> int:
    settings = load_settings()
    configure_process_environment(settings)
    app = QGuiApplication(sys.argv)
    app.setApplicationName("VoxWeave")
    app.setOrganizationName("CheshireMew")
    engine = QQmlApplicationEngine()
    bridge = Bridge(settings)
    engine.setInitialProperties({"bridge": bridge})
    engine.load(QUrl.fromLocalFile(str(PACKAGE_ROOT / "qml" / "Main.qml")))
    if not engine.rootObjects():
        bridge.shutdown()
        return 1
    exit_code = app.exec()
    bridge.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
