from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from .config import Settings
from .protocol import PROTOCOL, PROTOCOL_VERSION

MAX_QUEUED_REQUESTS = 256
LOGGER = logging.getLogger(__name__)


class DiagnosticMessage(str):
    """Display-safe text that retains the unabridged local diagnostic."""

    detail: str

    def __new__(cls, value: str, detail: str) -> DiagnosticMessage:
        instance = super().__new__(cls, value)
        instance.detail = detail
        return instance


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
        self.inflight_keys: set[str] = set()
        self.pending_by_key: dict[str, dict[str, Any]] = {}
        self.capacity = threading.BoundedSemaphore(MAX_QUEUED_REQUESTS)
        self.settings_write_lock = threading.Lock()
        self.closed = False
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

    def _invoke_callback(self, callback: Any, value: Any) -> bool:
        try:
            callback(value)
            return True
        except Exception as exc:  # noqa: BLE001 - isolate GUI consumer callbacks
            LOGGER.exception("GUI request callback failed")
            self.status_callback(
                self.error_formatter("callback_failed", str(exc) or type(exc).__name__),
                "danger",
            )
            return False

    @Slot(object)
    def _finish(self, envelope: object) -> None:
        item = dict(envelope)
        request_id = str(item["request_id"])
        show_status = bool(item["show_status"])
        self.active.pop(request_id, None)
        request_key = item.get("request_key")
        if self.closed:
            if request_key:
                self.inflight_keys.discard(request_key)
            return
        pending = None
        if request_key:
            self.inflight_keys.discard(request_key)
            pending = self.pending_by_key.pop(request_key, None)
            if pending and not self.closed:
                self._schedule(pending)
        if request_key and item["generation"] != self.generations.get(request_key):
            if not pending and request_key not in self.inflight_keys:
                self.generations.pop(request_key, None)
            if show_status:
                self._refresh_status()
            return
        if request_key and not pending:
            self.generations.pop(request_key, None)
        error = item.get("error")
        if error:
            detail = str(error)
            error = DiagnosticMessage(self.error_formatter(item.get("error_type"), detail), detail)
            callback = item.get("error_callback")
            if callback:
                self._invoke_callback(callback, error)
            else:
                self.status_callback(error, "danger")
            if show_status:
                self._refresh_status()
            return
        callback = item.get("callback")
        if callback:
            self._invoke_callback(callback, item.get("payload"))
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
        generation = 0
        if request_key:
            generation = self.generations.get(request_key, 0) + 1
            self.generations[request_key] = generation
        item = {
            "request_id": str(uuid.uuid4()),
            "operation": operation,
            "arguments": dict(arguments),
            "callback": callback,
            "show_status": show_status,
            "error_callback": error_callback,
            "request_key": request_key,
            "generation": generation,
        }
        if self.closed:
            return
        if request_key and request_key in self.inflight_keys:
            self.pending_by_key[request_key] = item
            return
        self._schedule(item)

    def _schedule(self, item: dict[str, Any]) -> None:
        if self.closed:
            return
        request_id = str(item["request_id"])
        operation = str(item["operation"])
        arguments = dict(item["arguments"])
        callback = item.get("callback")
        show_status = bool(item["show_status"])
        error_callback = item.get("error_callback")
        request_key = item.get("request_key")
        generation = int(item["generation"])
        if not self.capacity.acquire(blocking=False):
            if request_key:
                self.inflight_keys.discard(request_key)
                if generation == self.generations.get(request_key):
                    self.generations.pop(request_key, None)
            message = "Too many requests are pending; please wait for the current work to finish."
            if error_callback:
                self._invoke_callback(error_callback, message)
            elif show_status:
                self.status_callback(message, "danger")
            return
        if request_key:
            self.inflight_keys.add(request_key)
        if show_status:
            self.active[request_id] = operation
            self._refresh_status()

        def work() -> None:
            request = {
                "protocol": PROTOCOL,
                "version": PROTOCOL_VERSION,
                "operation": operation,
                "arguments": arguments,
                "request_id": request_id,
                "actor": {"kind": "desktop", "name": "VoxWeave GUI"},
            }
            envelope = {
                "request_id": request_id,
                "request_key": request_key,
                "generation": generation,
                "show_status": show_status,
            }
            try:
                if operation == "settings.update":
                    with self.settings_write_lock:
                        request["arguments"] = {
                            **arguments,
                            "expected_revision": self.settings.revision,
                        }
                        payload = self.transport(self.settings, "POST", "/v1/execute", request)
                        if (
                            isinstance(payload, dict)
                            and not payload.get("ok")
                            and payload.get("error_type") == "revision_conflict"
                        ):
                            current = self._fetch_settings()
                            request["request_id"] = str(uuid.uuid4())
                            request["arguments"]["expected_revision"] = current.revision
                            payload = self.transport(self.settings, "POST", "/v1/execute", request)
                        if isinstance(payload, dict) and payload.get("ok"):
                            result_settings = payload.get("result", {}).get("settings")
                            if isinstance(result_settings, dict):
                                self.settings.replace_with(Settings(**result_settings))
                else:
                    payload = self.transport(self.settings, "POST", "/v1/execute", request)
                if not isinstance(payload, dict):
                    raise TypeError("service returned an invalid response")
                if not payload.get("ok"):
                    envelope.update(
                        error=payload.get("error", "operation failed"),
                        error_type=payload.get("error_type", "operation_failed"),
                        error_callback=error_callback,
                    )
                else:
                    envelope.update(payload=payload["result"], callback=callback)
            except Exception as exc:  # noqa: BLE001 - UI boundary
                envelope.update(
                    error=str(exc),
                    error_type="service_unavailable",
                    error_callback=error_callback,
                )
            finally:
                self.capacity.release()
            self._emit_completed(envelope)

        try:
            self.executor.submit(work)
        except RuntimeError:
            self.capacity.release()
            if request_key:
                self.inflight_keys.discard(request_key)
            self.active.pop(request_id, None)

    def _fetch_settings(self) -> Settings:
        request = {
            "protocol": PROTOCOL,
            "version": PROTOCOL_VERSION,
            "operation": "settings.get",
            "arguments": {},
            "request_id": str(uuid.uuid4()),
            "actor": {"kind": "desktop", "name": "VoxWeave GUI"},
        }
        payload = self.transport(self.settings, "POST", "/v1/execute", request)
        if not isinstance(payload, dict) or not payload.get("ok"):
            message = (
                payload.get("error") if isinstance(payload, dict) else "settings refresh failed"
            )
            raise RuntimeError(str(message))
        current = Settings(**dict(payload["result"]))
        self.settings.replace_with(current)
        return current

    def invalidate(self, request_key: str) -> None:
        if request_key in self.inflight_keys:
            self.generations[request_key] = self.generations.get(request_key, 0) + 1
        else:
            self.generations.pop(request_key, None)
        self.pending_by_key.pop(request_key, None)

    def shutdown(self) -> None:
        self.closed = True
        self.active.clear()
        self.inflight_keys.clear()
        self.pending_by_key.clear()
        self.generations.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)
