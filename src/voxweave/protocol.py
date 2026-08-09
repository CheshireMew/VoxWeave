from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

PROTOCOL = "voxweave-control"
PROTOCOL_VERSION = 1


OPERATIONS: dict[str, dict[str, Any]] = {
    "runtime.inspect": {"long_running": False, "arguments": {}},
    "runtime.install": {
        "long_running": True,
        "arguments": {
            "rvc_root": "absolute path",
            "rvc_python": "absolute path",
            "install_separation": "download the optional PyMSS model after license review",
            "install_speaker_model": "download the CC-BY-4.0 WeSpeaker ONNX model",
        },
    },
    "model.scan": {
        "long_running": False,
        "arguments": {"weight_roots": "absolute path[]", "index_roots": "absolute path[]"},
    },
    "model.list": {"long_running": False, "arguments": {}},
    "model.resolve": {"long_running": False, "arguments": {"voice": "id, name, or alias"}},
    "model.import": {
        "long_running": True,
        "arguments": {
            "model": "absolute .pth path or HTTPS URL",
            "index": "optional absolute .index path",
            "index_url": "optional HTTPS .index URL",
            "id": "optional stable id",
            "display_name": "optional display name",
            "aliases": "optional string[]",
            "license_spdx": "optional SPDX id",
            "source_url": "optional URL",
            "model_sha256": "required SHA-256 for URL imports",
            "index_sha256": "required SHA-256 when index_url is used",
            "download_size_bytes": "required model size for URL imports",
            "index_size_bytes": "required index size when index_url is used",
        },
    },
    "model.catalog.install": {
        "long_running": True,
        "arguments": {"catalog_url": "HTTPS catalog URL", "model_id": "catalog model id"},
    },
    "preset.list": {"long_running": False, "arguments": {"model": "optional model selector"}},
    "preset.save": {
        "long_running": False,
        "arguments": {
            "model": "model selector",
            "name": "preset name",
            "parameters": "conversion parameter object",
        },
    },
    "media.inspect": {"long_running": False, "arguments": {"input": "absolute media path"}},
    "media.analyze": {
        "long_running": True,
        "arguments": {
            "input": "absolute media path",
            "content_mode": "clean|mixed|singing",
        },
    },
    "conversion.preview": {
        "long_running": True,
        "arguments": {
            "input": "absolute media path",
            "model": "model selector",
            "variants": "one to four conversion parameter objects",
            "start_seconds": "preview start",
            "duration_seconds": "10 to 20 seconds",
            "output_directory": "absolute directory",
            "content_mode": "clean|mixed|singing",
        },
    },
    "conversion.run": {
        "long_running": True,
        "arguments": {
            "input": "absolute media path",
            "output": "absolute output path",
            "model": "model selector",
            "pitch": "integer semitones",
            "f0": "rmvpe|fcpe|pm",
            "index_rate": "0..1",
            "rms_mix_rate": "0..1",
            "protect": "0..0.5",
            "content_mode": "clean|mixed|singing",
            "selected_speakers": "optional speaker id[]",
            "analysis_manifest": "optional media.analyze manifest path",
            "overlap_policy": "skip|convert flagged or unresolved overlap intervals",
            "overwrite": "boolean, defaults false",
        },
    },
    "batch.create": {
        "long_running": False,
        "arguments": {
            "input_root": "absolute directory",
            "output_root": "absolute directory",
            "model": "model selector",
            "preset": "conversion parameters",
            "preset_name": "stable output filename label",
            "recursive": "boolean",
            "watch": "boolean",
            "extensions": "optional file extension[]",
        },
    },
    "batch.run": {"long_running": False, "arguments": {"batch_id": "batch id"}},
    "batch.watch": {
        "long_running": False,
        "arguments": {"batch_id": "batch id", "enabled": "boolean"},
    },
    "task.list": {"long_running": False, "arguments": {}},
    "task.get": {"long_running": False, "arguments": {"task_id": "task id"}},
    "task.cancel": {"long_running": False, "arguments": {"task_id": "task id"}},
    "task.retry": {"long_running": False, "arguments": {"task_id": "task id"}},
}


REQUIRED_ARGUMENTS = {
    "model.resolve": {"voice"},
    "model.import": {"model"},
    "model.catalog.install": {"catalog_url", "model_id"},
    "preset.save": {"model", "name", "parameters"},
    "media.inspect": {"input"},
    "media.analyze": {"input"},
    "conversion.preview": {"input", "model"},
    "conversion.run": {"input", "output", "model"},
    "batch.create": {"input_root", "output_root", "model"},
    "batch.run": {"batch_id"},
    "batch.watch": {"batch_id", "enabled"},
    "task.get": {"task_id"},
    "task.cancel": {"task_id"},
    "task.retry": {"task_id"},
}


def _validate_parameters(parameters: dict[str, Any]) -> None:
    if parameters.get("f0", "rmvpe") not in {"rmvpe", "fcpe", "pm"}:
        raise ValueError("f0 must be rmvpe, fcpe, or pm")
    ranges = {
        "index_rate": (0.0, 1.0),
        "rms_mix_rate": (0.0, 1.0),
        "protect": (0.0, 0.5),
    }
    for name, (minimum, maximum) in ranges.items():
        if name in parameters and not minimum <= float(parameters[name]) <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
    if "pitch" in parameters and not -36 <= int(parameters["pitch"]) <= 36:
        raise ValueError("pitch must be between -36 and 36 semitones")
    if parameters.get("content_mode", "clean") not in {"clean", "mixed", "singing"}:
        raise ValueError("content_mode must be clean, mixed, or singing")


def validate_arguments(operation: str, arguments: dict[str, Any]) -> None:
    if operation not in OPERATIONS:
        raise LookupError(f"unsupported operation: {operation}")
    allowed = set(OPERATIONS[operation]["arguments"])
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"unsupported arguments for {operation}: {sorted(unknown)}")
    missing = REQUIRED_ARGUMENTS.get(operation, set()) - set(arguments)
    if missing:
        raise ValueError(f"missing arguments for {operation}: {sorted(missing)}")

    scalar_paths = {
        "input",
        "output",
        "output_directory",
        "input_root",
        "output_root",
        "analysis_manifest",
        "index",
        "rvc_root",
        "rvc_python",
    }
    if operation == "model.import":
        model_value = arguments.get("model")
        if model_value and not str(model_value).lower().startswith("https://"):
            scalar_paths.add("model")
    for name in scalar_paths:
        value = arguments.get(name)
        if value is not None and not Path(value).is_absolute():
            raise ValueError(f"{name} must be an absolute path")
    for name in ("weight_roots", "index_roots"):
        values = arguments.get(name) or []
        if not isinstance(values, list) or any(not Path(value).is_absolute() for value in values):
            raise ValueError(f"{name} must contain absolute paths")
    for name in ("catalog_url", "source_url", "index_url"):
        value = arguments.get(name)
        if value and not str(value).lower().startswith("https://"):
            raise ValueError(f"{name} must use HTTPS")
    if operation == "model.import" and str(arguments["model"]).lower().startswith("https://"):
        required = {"id", "display_name", "license_spdx", "model_sha256", "download_size_bytes"}
        missing_url_fields = required - set(arguments)
        if missing_url_fields:
            raise ValueError(f"URL model imports require: {sorted(missing_url_fields)}")
        if arguments.get("index_url"):
            index_required = {"index_sha256", "index_size_bytes"} - set(arguments)
            if index_required:
                raise ValueError(f"URL index imports require: {sorted(index_required)}")
        for name in ("download_size_bytes", "index_size_bytes"):
            if name in arguments and int(arguments[name]) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("model_sha256", "index_sha256"):
            if name in arguments and not re.fullmatch(r"[0-9a-fA-F]{64}", arguments[name]):
                raise ValueError(f"{name} must be a 64-character SHA-256")
    if "id" in arguments and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", arguments["id"]):
        raise ValueError("id must be a stable ASCII identifier without path separators")
    if arguments.get("index") and arguments.get("index_url"):
        raise ValueError("index and index_url cannot both be provided")

    if operation in {"conversion.run", "preset.save"}:
        parameters = arguments if operation == "conversion.run" else arguments["parameters"]
        _validate_parameters(parameters)
    if operation == "conversion.preview":
        if arguments.get("content_mode", "clean") not in {"clean", "mixed", "singing"}:
            raise ValueError("content_mode must be clean, mixed, or singing")
        variants = arguments.get("variants") or [{}]
        if not isinstance(variants, list) or not 1 <= len(variants) <= 4:
            raise ValueError("variants must contain one to four parameter objects")
        for variant in variants:
            if not isinstance(variant, dict):
                raise ValueError("each preview variant must be an object")
            _validate_parameters(variant)
    if operation == "batch.create":
        preset = arguments.get("preset") or {}
        if not isinstance(preset, dict):
            raise ValueError("preset must be an object")
        _validate_parameters(preset)
    if "selected_speakers" in arguments and not isinstance(arguments["selected_speakers"], list):
        raise ValueError("selected_speakers must be an array")
    if arguments.get("overlap_policy", "convert") not in {"skip", "convert"}:
        raise ValueError("overlap_policy must be skip or convert")
    for name in ("overwrite", "recursive", "watch", "enabled"):
        if name in arguments and not isinstance(arguments[name], bool):
            raise ValueError(f"{name} must be boolean")
    if "install_separation" in arguments and not isinstance(arguments["install_separation"], bool):
        raise ValueError("install_separation must be boolean")
    if "install_speaker_model" in arguments and not isinstance(
        arguments["install_speaker_model"], bool
    ):
        raise ValueError("install_speaker_model must be boolean")


def describe() -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION,
        "product": "VoxWeave",
        "product_version": "0.1.0",
        "operations": deepcopy(OPERATIONS),
    }


def success(request_id: str | None, result: Any) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": True,
        "result": result,
    }


def failure(request_id: str | None, error_type: str, error: str) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": False,
        "error_type": error_type,
        "error": error,
    }
