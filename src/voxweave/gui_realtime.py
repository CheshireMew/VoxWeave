from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from .config import Settings, normalize_realtime_settings
from .gui_requests import RequestCoordinator


class RealtimeViewModel(QObject):
    devicesChanged = Signal()
    audioRouteChanged = Signal()
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
        self._last_prepare_arguments: dict[str, Any] | None = None
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

    def _resolve_audio_route(self) -> dict[str, Any]:
        hostapis = list(self._devices.get("hostapis", []))
        devices = list(self._devices.get("devices", []))
        preferred_host = str(self._preferences.get("hostapi", ""))
        host = next(
            (item for item in hostapis if str(item.get("name", "")) == preferred_host),
            None,
        )
        if host is None:
            default_input = int(self._devices.get("default_input_device", -1))
            default_record = next(
                (item for item in devices if int(item.get("id", -1)) == default_input),
                None,
            )
            if default_record is not None:
                default_host_id = int(default_record.get("hostapi_id", -1))
                host = next(
                    (
                        item
                        for item in hostapis
                        if int(item.get("id", -1)) == default_host_id
                    ),
                    None,
                )
        if host is None and hostapis:
            host = hostapis[0]
        if host is None:
            return {
                "ready": False,
                "hostapi_id": -1,
                "hostapi": "",
                "input_device": -1,
                "input_device_name": "",
                "output_device": -1,
                "output_device_name": "",
            }

        host_id = int(host.get("id", -1))
        input_devices = [
            item
            for item in devices
            if int(item.get("hostapi_id", -1)) == host_id
            and int(item.get("input_channels", 0)) > 0
        ]
        output_devices = [
            item
            for item in devices
            if int(item.get("hostapi_id", -1)) == host_id
            and int(item.get("output_channels", 0)) > 0
        ]

        def selected_device(
            candidates: list[dict[str, Any]], preference_name: str, default_name: str
        ) -> dict[str, Any] | None:
            preferred_name = str(self._preferences.get(preference_name, ""))
            selected = next(
                (
                    item
                    for item in candidates
                    if str(item.get("name", "")) == preferred_name
                ),
                None,
            )
            if selected is not None:
                return selected
            default_id = int(self._devices.get(default_name, -1))
            return next(
                (item for item in candidates if int(item.get("id", -1)) == default_id),
                candidates[0] if candidates else None,
            )

        input_device = selected_device(
            input_devices, "input_device", "default_input_device"
        )
        output_device = selected_device(
            output_devices, "output_device", "default_output_device"
        )
        return {
            "ready": input_device is not None and output_device is not None,
            "hostapi_id": host_id,
            "hostapi": str(host.get("name", "")),
            "input_device": int(input_device.get("id", -1)) if input_device else -1,
            "input_device_name": str(input_device.get("name", ""))
            if input_device
            else "",
            "output_device": int(output_device.get("id", -1)) if output_device else -1,
            "output_device_name": str(output_device.get("name", ""))
            if output_device
            else "",
        }

    @Property("QVariantMap", notify=audioRouteChanged)
    def audioRoute(self) -> dict[str, Any]:
        return self._resolve_audio_route()

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
        route_changed = any(
            preferences[name] != self._preferences[name]
            for name in ("hostapi", "input_device", "output_device")
        )
        if preferences != self._preferences:
            self._preferences = preferences
            self.preferencesChanged.emit()
            if route_changed:
                self.audioRouteChanged.emit()
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

    @Slot(int, int)
    def saveAudioRoute(self, input_device: int, output_device: int) -> None:
        input_record = self._device(input_device)
        output_record = self._device(output_device)
        if (
            input_record is None
            or output_record is None
            or int(input_record.get("input_channels", 0)) < 1
            or int(output_record.get("output_channels", 0)) < 1
            or int(input_record.get("hostapi_id", -1))
            != int(output_record.get("hostapi_id", -1))
        ):
            return
        self.savePreferences(
            {
                **self._preferences,
                "hostapi": str(input_record.get("hostapi", "")),
                "input_device": str(input_record.get("name", "")),
                "output_device": str(output_record.get("name", "")),
            }
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
            self.audioRouteChanged.emit()

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
            worker_state = str((result.get("worker") or {}).get("state") or "")
            if worker_state in {"failed", "not_started"}:
                self._last_prepare_arguments = None
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

    @staticmethod
    def _session_arguments(
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
    ) -> dict[str, Any]:
        return {
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
        }

    @Slot(str, int, int, int, str, float, float, float, float, float)
    def prepareModel(
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
    ) -> None:
        arguments = self._session_arguments(
            model,
            input_device,
            output_device,
            pitch,
            f0,
            index_rate,
            rms_mix_rate,
            vad_threshold,
            input_gate_db,
            block_seconds,
            False,
        )
        worker = self._status.get("worker") or {}
        if (
            arguments == self._last_prepare_arguments
            and worker.get("state") in {"starting", "warming", "ready"}
            and worker.get("model_id") == model
        ):
            return
        self._last_prepare_arguments = dict(arguments)

        def update(result: dict[str, Any]) -> None:
            self._status = result
            self._set_poll_interval(result.get("state"))
            self.statusChanged.emit()

        def failed(message: str) -> None:
            self._last_prepare_arguments = None
            self.status_callback(message, "danger")

        self.requests.submit(
            "realtime.prepare",
            arguments,
            update,
            show_status=False,
            error_callback=failed,
            request_key="realtime-prepare",
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
        arguments = self._session_arguments(
            model,
            input_device,
            output_device,
            pitch,
            f0,
            index_rate,
            rms_mix_rate,
            vad_threshold,
            input_gate_db,
            block_seconds,
            test_mode,
        )

        def update(result: dict[str, Any]) -> None:
            self._status = result
            self._set_poll_interval(result.get("state"))
            self.statusChanged.emit()
            self.status_callback(self.text_callback("realtime.status.starting"), "info")

        self.requests.submit(
            "realtime.start",
            arguments,
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
