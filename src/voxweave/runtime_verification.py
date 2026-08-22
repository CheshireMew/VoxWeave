from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config
from .config import Settings
from .runtime import resolve_rvc_python

PROTOCOL = "voxweave-runtime-verification"
VERSION = 1


def _path_identity(value: str | Path | None) -> str | None:
    if not value:
        return None
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def settings_identity(settings: Settings) -> dict[str, Any]:
    return {
        "rvc_root": _path_identity(settings.rvc_root),
        "rvc_python": _path_identity(resolve_rvc_python(settings)),
        "ffmpeg": _path_identity(settings.ffmpeg),
        "ffprobe": _path_identity(settings.ffprobe),
        "hardware_backend": settings.hardware_backend,
    }


def report_identity(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "rvc_root": _path_identity(report.get("rvc_root")),
        "rvc_python": _path_identity(report.get("rvc_python")),
        "ffmpeg": _path_identity(report.get("ffmpeg")),
        "ffprobe": _path_identity(report.get("ffprobe")),
        "hardware_backend": report.get("hardware_backend"),
    }


def load_runtime_verification(settings: Settings) -> dict[str, Any] | None:
    path = settings.runtime_verification_path
    if not path.is_file():
        legacy = config.SOURCE_ROOT / ".voxweave" / "runtime-verification.json"
        if not legacy.is_file():
            return None
        path = legacy
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        payload.get("protocol") != PROTOCOL
        or payload.get("version") != VERSION
        or payload.get("identity") != settings_identity(settings)
    ):
        return None
    report = payload.get("report")
    if not isinstance(report, dict) or not report.get("ready"):
        return None
    if path != settings.runtime_verification_path:
        save_runtime_verification(settings, report)
    return {**report, "cached": True, "verified_at": payload.get("verified_at")}


def save_runtime_verification(settings: Settings, report: dict[str, Any]) -> None:
    payload = {
        "protocol": PROTOCOL,
        "version": VERSION,
        "identity": report_identity(report),
        "verified_at": datetime.now(UTC).isoformat(),
        "report": {**report, "cached": False},
    }
    target = settings.runtime_verification_path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
