from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

F0Method = Literal["rmvpe", "fcpe", "pm"]
BlockSeconds = Literal[0.25, 0.5, 1.0]


@dataclass(frozen=True, slots=True)
class ParameterOption:
    value: str | float
    label: str | None = None
    label_key: str | None = None

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {"value": self.value}
        if self.label is not None:
            result["label"] = self.label
        if self.label_key is not None:
            result["label_key"] = self.label_key
        return result


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    kind: Literal["integer", "number", "choice", "boolean"]
    default: int | float | str | bool
    minimum: int | float | None = None
    maximum: int | float | None = None
    step: int | float | None = None
    ui_scale: int | float = 1
    options: tuple[ParameterOption, ...] = ()

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind,
            "default": self.default,
            "ui_scale": self.ui_scale,
        }
        if self.minimum is not None:
            result["minimum"] = self.minimum
            result["ui_minimum"] = self.minimum * self.ui_scale
        if self.maximum is not None:
            result["maximum"] = self.maximum
            result["ui_maximum"] = self.maximum * self.ui_scale
        if self.step is not None:
            result["step"] = self.step
            result["ui_step"] = self.step * self.ui_scale
        result["ui_default"] = (
            self.default * self.ui_scale
            if isinstance(self.default, int | float) and not isinstance(self.default, bool)
            else self.default
        )
        if self.options:
            result["options"] = [option.public() for option in self.options]
        return result


PITCH_SPEC = ParameterSpec("integer", 0, -36, 36, 1)
F0_SPEC = ParameterSpec(
    "choice",
    "rmvpe",
    options=(
        ParameterOption("rmvpe", label="RMVPE"),
        ParameterOption("fcpe", label="FCPE"),
        ParameterOption("pm", label="PM"),
    ),
)
INDEX_RATE_SPEC = ParameterSpec("number", 0.72, 0.0, 1.0, 0.01, 100)
RMS_MIX_RATE_SPEC = ParameterSpec("number", 0.25, 0.0, 1.0, 0.01, 100)
PROTECT_SPEC = ParameterSpec("number", 0.33, 0.0, 0.5, 0.01, 100)
VAD_THRESHOLD_SPEC = ParameterSpec("number", 0.35, 0.1, 0.9, 0.01, 100)
INPUT_GATE_DB_SPEC = ParameterSpec("number", -40.0, -60.0, -20.0, 1.0)
BLOCK_SECONDS_SPEC = ParameterSpec(
    "choice",
    0.25,
    options=(
        ParameterOption(0.25, label_key="realtime.latency.low"),
        ParameterOption(0.5, label_key="realtime.latency.balanced"),
        ParameterOption(1.0, label_key="realtime.latency.stable"),
    ),
)
TEST_MODE_SPEC = ParameterSpec("boolean", False)
PUSH_TO_TALK_SPEC = ParameterSpec("boolean", False)

RVC_PARAMETER_SPECS = {
    "pitch": PITCH_SPEC,
    "f0": F0_SPEC,
    "index_rate": INDEX_RATE_SPEC,
    "rms_mix_rate": RMS_MIX_RATE_SPEC,
    "protect": PROTECT_SPEC,
}

REALTIME_PARAMETER_SPECS = {
    "pitch": PITCH_SPEC,
    "f0": F0_SPEC,
    "index_rate": INDEX_RATE_SPEC,
    "rms_mix_rate": RMS_MIX_RATE_SPEC,
    "vad_threshold": VAD_THRESHOLD_SPEC,
    "input_gate_db": INPUT_GATE_DB_SPEC,
    "block_seconds": BLOCK_SECONDS_SPEC,
    "test_mode": TEST_MODE_SPEC,
    "push_to_talk": PUSH_TO_TALK_SPEC,
}

DEFAULT_REALTIME_SETTINGS: dict[str, Any] = {
    "model": "",
    "hostapi": "",
    "input_device": "",
    "output_device": "",
    **{name: spec.default for name, spec in REALTIME_PARAMETER_SPECS.items()},
}

REALTIME_WORKER_DEFAULTS: dict[str, float] = {
    "crossfade_seconds": 0.05,
    "extra_seconds": 2.5,
}


def _normalize_parameter(name: str, value: Any, spec: ParameterSpec) -> Any:
    if spec.kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
        return value
    if spec.kind == "choice":
        allowed = tuple(option.value for option in spec.options)
        if value not in allowed:
            display = ", ".join(str(item) for item in allowed)
            raise ValueError(f"{name} must be one of: {display}")
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        expected = "an integer" if spec.kind == "integer" else "a number"
        raise ValueError(f"{name} must be {expected}")
    if spec.kind == "integer":
        if not float(value).is_integer():
            raise ValueError(f"{name} must be an integer")
        normalized: int | float = int(value)
    else:
        normalized = float(value)
    if spec.minimum is not None and normalized < spec.minimum:
        raise ValueError(f"{name} must be between {spec.minimum} and {spec.maximum}")
    if spec.maximum is not None and normalized > spec.maximum:
        raise ValueError(f"{name} must be between {spec.minimum} and {spec.maximum}")
    return normalized


def normalize_rvc_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        name: _normalize_parameter(name, parameters.get(name, spec.default), spec)
        for name, spec in RVC_PARAMETER_SPECS.items()
    }


def normalize_realtime_settings(value: Any) -> dict[str, Any]:
    """Return one complete, validated realtime preference record."""

    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("realtime settings must be an object")
    unknown = set(value) - DEFAULT_REALTIME_SETTINGS.keys()
    if unknown:
        raise ValueError(f"unsupported realtime settings: {sorted(unknown)}")
    result = {**DEFAULT_REALTIME_SETTINGS, **value}
    for name in ("model", "hostapi", "input_device", "output_device"):
        if not isinstance(result[name], str):
            raise ValueError(f"realtime.{name} must be a string")
    for name, spec in REALTIME_PARAMETER_SPECS.items():
        result[name] = _normalize_parameter(f"realtime.{name}", result[name], spec)
    return result


def normalize_realtime_start(arguments: dict[str, Any]) -> dict[str, Any]:
    values = normalize_rvc_parameters(arguments)
    for name in ("input_device", "output_device"):
        value = arguments.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        values[name] = value
    for name in (
        "vad_threshold",
        "input_gate_db",
        "block_seconds",
        "test_mode",
        "push_to_talk",
    ):
        spec = REALTIME_PARAMETER_SPECS[name]
        values[name] = _normalize_parameter(name, arguments.get(name, spec.default), spec)
    values["recording"] = bool(arguments.get("recording", False))
    recording_directory = arguments.get("recording_directory")
    if recording_directory is not None and not isinstance(recording_directory, str):
        raise ValueError("recording_directory must be a path string")
    values["recording_directory"] = recording_directory
    values.update(REALTIME_WORKER_DEFAULTS)
    return values


def realtime_parameter_capabilities() -> dict[str, dict[str, Any]]:
    return {name: spec.public() for name, spec in REALTIME_PARAMETER_SPECS.items()}
