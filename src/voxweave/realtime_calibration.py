from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any


class RealtimeCalibrationService:
    def __init__(
        self,
        devices: Callable[[], dict[str, Any]],
        audio_test: Callable[[dict[str, Any]], dict[str, Any]],
        resolve_model: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.devices = devices
        self.audio_test = audio_test
        self.resolve_model = resolve_model

    def calibrate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        devices = {int(item["id"]): item for item in self.devices()["devices"]}
        input_device = devices.get(int(arguments["input_device"]))
        output_device = devices.get(int(arguments["output_device"]))
        if not input_device or int(input_device["input_channels"]) < 1:
            raise ValueError("calibration input device is unavailable")
        if not output_device or int(output_device["output_channels"]) < 1:
            raise ValueError("calibration output device is unavailable")
        if input_device["hostapi_id"] != output_device["hostapi_id"]:
            raise ValueError("calibration devices must use the same audio host API")
        measured = self.audio_test(
            {
                "mode": "input",
                "device": int(arguments["input_device"]),
                "duration_seconds": float(arguments["duration_seconds"]),
            }
        )
        rms = max(float(measured.get("rms") or 0), 1e-6)
        peak = max(float(measured.get("peak") or 0), 0)
        input_db = 20 * math.log10(rms)
        noise_floor_db = float(measured.get("noise_floor_db") or min(input_db, -60.0))
        signal_db = float(measured.get("signal_db") or input_db)
        snr_db = float(measured.get("snr_db") or max(0.0, signal_db - noise_floor_db))
        stability = float(measured.get("device_stability") or 0.0)
        gate = max(-60.0, min(-20.0, noise_floor_db + 8.0))
        recommended_block = (
            0.25
            if stability >= 0.99 and snr_db >= 18
            else 0.5
            if stability >= 0.95 and snr_db >= 10
            else 1.0
        )
        recommended_pitch = 0
        if arguments.get("model") and self.resolve_model is not None:
            model = self.resolve_model(str(arguments["model"]))
            recommended_pitch = int((model.get("recommended") or {}).get("pitch") or 0)
        recommended_index_rate = 0.78 if snr_db >= 24 else 0.68 if snr_db >= 14 else 0.5
        return {
            "input_device": int(arguments["input_device"]),
            "output_device": int(arguments["output_device"]),
            "measured_peak": peak,
            "measured_rms": rms,
            "measured_input_db": round(input_db, 1),
            "recommended_input_gate_db": round(gate, 1),
            "recommended_vad_threshold": 0.5 if snr_db >= 18 else 0.4,
            "latency_options_ms": {"0.25": 540, "0.5": 1040, "1.0": 2040},
            "recommended_block_seconds": recommended_block,
            "noise_floor_db": round(noise_floor_db, 1),
            "signal_db": round(signal_db, 1),
            "snr_db": round(snr_db, 1),
            "pitch_hz_min": measured.get("pitch_hz_min"),
            "pitch_hz_median": measured.get("pitch_hz_median"),
            "pitch_hz_max": measured.get("pitch_hz_max"),
            "device_stability": round(stability, 3),
            "recommended_pitch": recommended_pitch,
            "recommended_index_rate": round(recommended_index_rate, 2),
        }
