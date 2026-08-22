from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, QUrl, QUrlQuery, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWebSockets import QWebSocket

from .client import ensure_service
from .config import Settings
from .discovery import Discovery
from .gui_presenters import (
    localized_task_stage,
    localized_task_title,
    localized_timestamp,
    task_error_summary,
    task_result_path,
)
from .gui_requests import RequestCoordinator
from .gui_support import local_path


class TaskEventStream(QObject):
    eventReceived = Signal(object)
    connectionReady = Signal(object)
    connectionFailed = Signal(str)

    def __init__(
        self,
        settings: Settings,
        parent: QObject | None = None,
        discovery_provider: Any = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.discovery_provider = discovery_provider or (lambda: ensure_service(settings))
        self.socket = QWebSocket(parent=self)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voxweave-events")
        self.reconnect = QTimer(self)
        self.reconnect.setSingleShot(True)
        self.reconnect.setInterval(1000)
        self.reconnect.timeout.connect(self._discover)
        self.socket.textMessageReceived.connect(self._message)
        self.socket.disconnected.connect(self._schedule_reconnect)
        self.socket.connected.connect(self.reconnect.stop)
        self.socket.errorOccurred.connect(lambda _error: self._schedule_reconnect())
        self.connectionReady.connect(self._connect)
        self.connectionFailed.connect(lambda _message: self._schedule_reconnect())
        self.active = False
        self.connecting = False
        self.after_id = 0

    def start(self) -> None:
        if self.active:
            return
        self.active = True
        self._discover()

    def _discover(self) -> None:
        if not self.active or self.connecting:
            return
        self.connecting = True

        def work() -> None:
            try:
                discovery = self.discovery_provider()
            except Exception as error:  # noqa: BLE001 - event transport boundary
                self.connectionFailed.emit(str(error))
            else:
                self.connectionReady.emit(discovery)

        self.executor.submit(work)

    @Slot(object)
    def _connect(self, value: object) -> None:
        self.connecting = False
        if not self.active:
            return
        discovery: Discovery = value  # type: ignore[assignment]
        url = QUrl(f"ws://127.0.0.1:{discovery.port}/v1/events")
        query = QUrlQuery()
        query.addQueryItem("token", discovery.token)
        query.addQueryItem("after_id", str(self.after_id))
        url.setQuery(query)
        self.socket.abort()
        self.socket.open(url)

    @Slot(str)
    def _message(self, message: str) -> None:
        try:
            event = json.loads(message)
            event_id = int(event["id"])
            task_id = str(event["task_id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return
        self.after_id = max(self.after_id, event_id)
        event["task_id"] = task_id
        self.eventReceived.emit(event)

    @Slot()
    def _schedule_reconnect(self) -> None:
        self.connecting = False
        if self.active and not self.reconnect.isActive():
            self.reconnect.start()

    def shutdown(self) -> None:
        self.active = False
        self.reconnect.stop()
        self.socket.abort()
        self.executor.shutdown(wait=False, cancel_futures=True)


class TaskFeed(QObject):
    itemsChanged = Signal()
    taskUpdated = Signal(object)

    def __init__(
        self,
        settings: Settings,
        requests: RequestCoordinator,
        parent: QObject | None = None,
        discovery_provider: Any = None,
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.items: list[dict[str, Any]] = []
        self.next_cursor: str | None = None
        self.refreshing = False
        self.pending_events: list[dict[str, Any]] = []
        self.snapshot_event_cursor = 0
        self.started = False
        self.events = TaskEventStream(settings, self, discovery_provider)
        self.events.eventReceived.connect(self._event)

    def start(self) -> None:
        self.started = True
        self.refresh()

    def _drain_pending_events(self) -> None:
        pending, self.pending_events = self.pending_events, []
        for event in pending:
            self._apply_event(event)

    def accept(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        self.items = [item for item in self.items if item["id"] != task_id]
        self.items.append(task)
        self.items.sort(
            key=lambda item: (str(item.get("created_at", "")), str(item["id"])),
            reverse=True,
        )
        self.itemsChanged.emit()
        self.taskUpdated.emit(task)

    @Slot()
    def refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True

        def update(result: dict[str, Any]) -> None:
            self.refreshing = False
            self.items = list(result["items"])
            self.next_cursor = result.get("next_cursor")
            self.snapshot_event_cursor = max(
                self.snapshot_event_cursor, int(result.get("event_cursor") or 0)
            )
            self.itemsChanged.emit()
            for task in self.items:
                self.taskUpdated.emit(task)
            self._drain_pending_events()
            if self.started and not self.events.active:
                self.events.after_id = int(result.get("event_cursor") or 0)
                self.events.start()

        def failed(_message: str) -> None:
            self.refreshing = False
            self._drain_pending_events()

        self.requests.submit(
            "task.list",
            {"limit": 200},
            update,
            show_status=False,
            error_callback=failed,
            request_key="task-page-initial",
        )

    @Slot()
    def load_more(self) -> None:
        if self.refreshing or not self.next_cursor:
            return
        self.refreshing = True
        cursor = self.next_cursor

        def update(result: dict[str, Any]) -> None:
            self.refreshing = False
            known = {item["id"] for item in self.items}
            self.items.extend(item for item in result["items"] if item["id"] not in known)
            self.next_cursor = result.get("next_cursor")
            self.itemsChanged.emit()
            self._drain_pending_events()

        def failed(_message: str) -> None:
            self.refreshing = False
            self._drain_pending_events()

        self.requests.submit(
            "task.list",
            {"limit": 200, "cursor": cursor},
            update,
            show_status=False,
            error_callback=failed,
            request_key=f"task-page:{cursor}",
        )

    @Slot(object)
    def _event(self, value: object) -> None:
        event = dict(value)
        if self.refreshing:
            self.pending_events.append(event)
            return
        self._apply_event(event)

    def _apply_event(self, event: dict[str, Any]) -> None:
        task_id = event["task_id"]
        existing = next((item for item in self.items if item["id"] == task_id), None)
        if existing is None:
            if int(event.get("id") or 0) > self.snapshot_event_cursor:
                self.refresh()
            return
        terminal = event.get("state") in {"completed", "failed", "cancelled", "interrupted"}
        if existing is not None and not terminal:
            self.accept(
                {
                    **existing,
                    "state": event.get("state", existing.get("state")),
                    "progress": event.get("progress", existing.get("progress", 0.0)),
                    "stage": event.get("stage", existing.get("stage")),
                    "updated_at": event.get("created_at", existing.get("updated_at")),
                }
            )
            return
        self.requests.submit(
            "task.get",
            {"task_id": task_id},
            self.accept,
            show_status=False,
            request_key=f"task:{task_id}",
        )

    def shutdown(self) -> None:
        self.started = False
        self.events.shutdown()


class TaskListViewModel(QObject):
    itemsChanged = Signal()

    def __init__(
        self,
        feed: TaskFeed,
        requests: RequestCoordinator,
        locale_context: Any,
        status_callback: Any,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.feed = feed
        self.requests = requests
        self.locale_context = locale_context
        self.status_callback = status_callback
        self.feed.itemsChanged.connect(self.itemsChanged)

    def _text(self, key: str) -> str:
        language, translations = self.locale_context()
        table = translations.get(language, translations["en"])
        return table.get(key, key)

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> list[dict[str, Any]]:
        language, translations = self.locale_context()
        projected = []
        for task in self.feed.items:
            item = dict(task)
            item["localized_title"] = localized_task_title(task, language, translations)
            error_summary = task_error_summary(task)
            item["error_summary"] = (
                self.requests.error_formatter(task.get("error_type"), error_summary)
                if error_summary
                else ""
            )
            item["result_path"] = task_result_path(task)
            item["localized_stage"] = localized_task_stage(task, language, translations)
            item["localized_timestamp"] = localized_timestamp(task.get("updated_at"))
            item["is_maintenance"] = str(task.get("operation") or "") in {
                "runtime.inspect",
                "diagnostics.snapshot",
            }
            projected.append(item)
        return projected

    @Property(bool, notify=itemsChanged)
    def hasMore(self) -> bool:
        return bool(self.feed.next_cursor)

    def locale_changed(self) -> None:
        self.itemsChanged.emit()

    @Slot()
    def refresh(self) -> None:
        self.feed.refresh()

    @Slot()
    def loadMore(self) -> None:
        self.feed.load_more()

    @Slot(str)
    def cancel(self, task_id: str) -> None:
        self.requests.submit("task.cancel", {"task_id": task_id}, self.feed.accept)

    @Slot(str)
    def retry(self, task_id: str) -> None:
        self.requests.submit("task.retry", {"task_id": task_id}, self.feed.accept)

    @Slot(str)
    def openResult(self, path: str) -> None:
        selected = Path(local_path(path)).expanduser().resolve()
        if not selected.exists():
            self.status_callback(
                self._text("error.file_missing").format(path=selected), "danger"
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(selected))):
            self.status_callback(
                self._text("error.open_failed").format(path=selected), "danger"
            )

    @Slot(str)
    def openResultFolder(self, path: str) -> None:
        selected = Path(local_path(path)).expanduser().resolve()
        folder = selected if selected.is_dir() else selected.parent
        if not folder.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self.status_callback(
                self._text("error.open_folder_failed").format(path=folder), "danger"
            )

    @Slot(str)
    def copyText(self, value: str) -> None:
        QGuiApplication.clipboard().setText(value)
