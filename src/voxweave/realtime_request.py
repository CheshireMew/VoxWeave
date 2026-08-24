from __future__ import annotations

from typing import Any

from .model_registry import ModelRegistry
from .parameter_contracts import normalize_realtime_start
from .rvc_engine import RvcEngine


class RealtimeRequestBuilder:
    """Resolves models and audio routes into one validated worker command."""

    def __init__(self, models: ModelRegistry, engine: RvcEngine) -> None:
        self.models = models
        self.engine = engine

    def devices(self) -> dict[str, Any]:
        payload = self.engine.audio_devices()
        return {
            "hostapis": payload["hostapis"],
            "devices": payload["devices"],
            "default_input_device": payload["default_input_device"],
            "default_output_device": payload["default_output_device"],
        }

    def audio_test(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.engine.audio_test(
            str(arguments["mode"]),
            int(arguments["device"]),
            float(arguments.get("duration_seconds", 2.0)),
        )

    def worker_command(
        self,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        model = self.models.resolve_for_execution(arguments["model"])
        normalized = normalize_realtime_start(arguments)
        device_payload = self.devices()
        devices = {int(device["id"]): device for device in device_payload["devices"]}
        input_device = devices.get(normalized["input_device"])
        output_device = devices.get(normalized["output_device"])
        if not input_device or int(input_device["input_channels"]) < 1:
            raise ValueError(f"device is not an audio input: {normalized['input_device']}")
        if not output_device or int(output_device["output_channels"]) < 1:
            raise ValueError(f"device is not an audio output: {normalized['output_device']}")
        if input_device["hostapi_id"] != output_device["hostapi_id"]:
            raise ValueError("input and output devices must use the same Windows audio host API")
        normalized.update(
            input_device_name=input_device["name"],
            output_device_name=output_device["name"],
            input_device_sample_rate=int(input_device["default_sample_rate"]),
            output_device_sample_rate=int(output_device["default_sample_rate"]),
            hostapi=input_device["hostapi"],
        )
        normalized["model"] = model["id"]
        return model, normalized, self.engine.realtime_payload(model, normalized)
