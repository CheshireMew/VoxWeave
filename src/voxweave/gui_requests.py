from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from .config import Settings


class RequestCoordinator(QObject):
    completed = Signal(object)

    def __init__(
        self,
        settings: Settings,
        transport: Any,
        status_callback: Any,
        operation_label: Any | None = None,
        error_formatter: Any | None = None,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.transport = transport
        self.status_callback = status_callback
        self.operation_label = operation_label or (lambda operation: operation)
        self.error_formatter = error_formatter or (lambda _error_type, message: str(message))
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="voxweave-gui")
        self.generations: dict[str, int] = {}
        self.active: dict[str, str] = {}
        self.completed.connect(self._finish)

    def _emit_completed(self, envelope: dict[str, Any]) -> None:
        try:
            self.completed.emit(envelope)
        except RuntimeError:
            return

    def _refresh_status(self) -> None:
        if self.active:
            latest = next(reversed(self.active))
            self.status_callback(f"{self.operation_label(self.active[latest])} …", "info")
        else:
            self.status_callback("Ready", "success")

    @Slot(object)
    def _finish(self, envelope: object) -> None:
        item = dict(envelope)
        request_id = str(item["request_id"])
        show_status = bool(item["show_status"])
        self.active.pop(request_id, None)
        request_key = item.get("request_key")
        if request_key and item["generation"] != self.generations.get(request_key):
            if show_status:
                self._refresh_status()
            return
        error = item.get("error")
        if error:
            error = self.error_formatter(item.get("error_type"), error)
            callback = item.get("error_callback")
            if callback:
                callback(error)
            else:
                self.status_callback(str(error), "danger")
            return
        callback = item.get("callback")
        if callback:
            callback(item.get("payload"))
        if show_status:
            self._refresh_status()

    def submit(
        self,
        operation: str,
        arguments: dict[str, Any],
        callback: Any = None,
        *,
        show_status: bool = True,
        error_callback: Any = None,
        request_key: str | None = None,
    ) -> None:
        request_id = str(uuid.uuid4())
        generation = 0
        if request_key:
            generation = self.generations.get(request_key, 0) + 1
            self.generations[request_key] = generation
        if show_status:
            self.active[request_id] = operation
            self._refresh_status()

        def work() -> None:
            request = {
                "protocol": "voxweave-control",
                "version": 1,
                "operation": operation,
                "arguments": arguments,
                "request_id": request_id,
                "actor": {"kind": "desktop", "name": "VoxWeave GUI"},
            }
            try:
                payload = self.transport(self.settings, "POST", "/v1/execute", request)
            except Exception as exc:  # noqa: BLE001 - UI boundary
                self._emit_completed(
                    {
                        "request_id": request_id,
                        "request_key": request_key,
                        "generation": generation,
                        "show_status": show_status,
                        "error": str(exc),
                        "error_type": "service_unavailable",
                        "error_callback": error_callback,
                    }
                )
                return
            if not payload.get("ok"):
                self._emit_completed(
                    {
                        "request_id": request_id,
                        "request_key": request_key,
                        "generation": generation,
                        "show_status": show_status,
                        "error": payload.get("error", "operation failed"),
                        "error_type": payload.get("error_type", "operation_failed"),
                        "error_callback": error_callback,
                    }
                )
                return
            self._emit_completed(
                {
                    "request_id": request_id,
                    "request_key": request_key,
                    "generation": generation,
                    "show_status": show_status,
                    "payload": payload["result"],
                    "callback": callback,
                }
            )

        self.executor.submit(work)

    def invalidate(self, request_key: str) -> None:
        self.generations[request_key] = self.generations.get(request_key, 0) + 1

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
