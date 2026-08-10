from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from .config import Settings, normalize_realtime_settings
from .gui_requests import RequestCoordinator


class RealtimeViewModel(QObject):
    devicesChanged = Signal()
    statusChanged = Signal()

    def __init__(
        self,
        settings: Settings,
        requests: RequestCoordinator,
        status_callback: Any,
        text_callback: Any,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.status_callback = status_callback
        self.text_callback = text_callback
        self._devices: dict[str, Any] = {"hostapis": [], "devices": []}
        self._preferences = dict(settings.realtime)
        self._saved_preferences = dict(settings.realtime)
        self._save_inflight = False
        self._status: dict[str, Any] = {
            "session_id": None,
            "state": "idle",
            "stage": "idle",
            "metrics": {},
        }
        self._refreshing = False
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refreshStatus)
        self.preferences_timer = QTimer(self)
        self.preferences_timer.setSingleShot(True)
        self.preferences_timer.setInterval(250)
        self.preferences_timer.timeout.connect(self._persist_preferences)

    def _set_poll_interval(self, state: str | None) -> None:
        self.timer.setInterval(250 if state in {"starting", "running", "stopping"} else 1000)

    @Property("QVariantMap", notify=devicesChanged)
    def devices(self) -> dict[str, Any]:
        return self._devices

    @Property("QVariantMap", notify=statusChanged)
    def status(self) -> dict[str, Any]:
        return self._status

    preferencesChanged = Signal()

    @Property("QVariantMap", notify=preferencesChanged)
    def preferences(self) -> dict[str, Any]:
        return dict(self._preferences)

    @Slot("QVariantMap")
    def savePreferences(self, value: dict[str, Any]) -> None:
        try:
            preferences = normalize_realtime_settings(dict(value))
        except (TypeError, ValueError) as exc:
            self.status_callback(str(exc), "danger")
            return
        if preferences != self._preferences:
            self._preferences = preferences
            self.preferencesChanged.emit()
        if self._preferences != self._saved_preferences:
            self.preferences_timer.start()

    def _persist_preferences(self) -> None:
        if self._save_inflight or self._preferences == self._saved_preferences:
            return
        self._save_inflight = True
        snapshot = dict(self._preferences)

        def update(result: dict[str, Any]) -> None:
            self._save_inflight = False
            self._saved_preferences = normalize_realtime_settings(result["realtime"])
            if self._preferences != self._saved_preferences:
                self.preferences_timer.start(0)

        def failed(message: str) -> None:
            self._save_inflight = False
            self.status_callback(message, "danger")

        self.requests.submit(
            "settings.update",
            {"realtime": snapshot},
            update,
            show_status=False,
            error_callback=failed,
            request_key="realtime-preferences",
        )

    def _device(self, device_id: int) -> dict[str, Any] | None:
        return next(
            (
                device
                for device in self._devices.get("devices", [])
                if int(device.get("id", -1)) == device_id
            ),
            None,
        )

    def start(self) -> None:
        self.timer.start()
        self.refreshDevices()
        self.refreshStatus()

    @Slot()
    def refreshDevices(self) -> None:
        def update(result: dict[str, Any]) -> None:
            payload = dict(result)
            payload["devices"] = [
                {**device, "display_name": f"{device['name']} · {device['hostapi']}"}
                for device in result.get("devices", [])
            ]
            self._devices = payload
            self.devicesChanged.emit()

        self.requests.submit(
            "realtime.devices",
            {},
            update,
            show_status=False,
            request_key="realtime-devices",
        )

    @Slot()
    def refreshStatus(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True

        def update(result: dict[str, Any]) -> None:
            self._refreshing = False
            previous_state = self._status.get("state")
            self._status = result
            self._set_poll_interval(result.get("state"))
            self.statusChanged.emit()
            if result.get("state") == "failed" and previous_state != "failed":
                self.status_callback(result.get("error") or "realtime session failed", "danger")

        def failed(message: str) -> None:
            self._refreshing = False
            self.status_callback(message, "danger")

        self.requests.submit(
            "realtime.status",
            {},
            update,
            show_status=False,
            error_callback=failed,
            request_key="realtime-status",
        )

    @Slot(str, int, int, int, str, float, float, float, float, float, bool)
    def startSession(
        self,
        model: str,
        input_device: int,
        output_device: int,
        pitch: int,
        f0: str,
        index_rate: float,
        rms_mix_rate: float,
        vad_threshold: float,
        input_gate_db: float,
        block_seconds: float,
        test_mode: bool,
    ) -> None:
        input_record = self._device(input_device)
        output_record = self._device(output_device)
        remembered = {
            **self._preferences,
            "model": model,
            "pitch": pitch,
            "f0": f0,
            "index_rate": index_rate,
            "rms_mix_rate": rms_mix_rate,
            "vad_threshold": vad_threshold,
            "input_gate_db": input_gate_db,
            "block_seconds": block_seconds,
            "test_mode": test_mode,
        }
        if input_record:
            remembered["hostapi"] = str(input_record.get("hostapi", ""))
            remembered["input_device"] = str(input_record.get("name", ""))
        if output_record:
            remembered["output_device"] = str(output_record.get("name", ""))
        self.savePreferences(remembered)
        self.preferences_timer.stop()
        self._persist_preferences()

        def update(result: dict[str, Any]) -> None:
            self._status = result
            self._set_poll_interval(result.get("state"))
            self.statusChanged.emit()
            self.status_callback(self.text_callback("realtime.status.starting"), "info")

        self.requests.submit(
            "realtime.start",
            {
                "model": model,
                "input_device": input_device,
                "output_device": output_device,
                "pitch": pitch,
                "f0": f0,
                "index_rate": index_rate,
                "rms_mix_rate": rms_mix_rate,
                "vad_threshold": vad_threshold,
                "input_gate_db": input_gate_db,
                "block_seconds": block_seconds,
                "test_mode": test_mode,
            },
            update,
            request_key="realtime-control",
        )

    @Slot()
    def stopSession(self) -> None:
        def update(result: dict[str, Any]) -> None:
            self._status = result
            self._set_poll_interval(result.get("state"))
            self.statusChanged.emit()

        self.requests.submit("realtime.stop", {}, update, request_key="realtime-control")

    def shutdown(self) -> None:
        self.timer.stop()
        self.preferences_timer.stop()
        self._persist_preferences()
