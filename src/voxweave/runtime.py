from __future__ import annotations

import json
import platform
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import PACKAGE_ROOT, Settings
from .hashing import sha256_file
from .process_control import run_capture
from .runtime_contract import runtime_contract


class RuntimeErrorDetail(RuntimeError):
    pass


def _run_json(
    command: list[str],
    *,
    cwd: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    completed = run_capture(command, cwd=cwd, cancelled=cancelled)
    output = completed.stdout.strip().splitlines()
    if completed.returncode != 0:
        raise RuntimeErrorDetail(completed.stderr.strip() or completed.stdout.strip())
    if not output:
        raise RuntimeErrorDetail(f"command returned no JSON: {command[0]}")
    return json.loads(output[-1])


def resolve_rvc_python(settings: Settings) -> Path | None:
    if settings.rvc_python:
        path = Path(settings.rvc_python)
        if path.is_file():
            return path
    if settings.rvc_root:
        root = Path(settings.rvc_root)
        candidates = (
            root / ".venv" / "Scripts" / "python.exe",
            root / ".venv" / "bin" / "python",
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    return None


def resolve_rvc_entry(settings: Settings) -> Path | None:
    if not settings.rvc_root:
        return None
    root = Path(settings.rvc_root)
    required = (root / "configs" / "config.py", root / "infer" / "vc" / "modules.py")
    entry = PACKAGE_ROOT / "rvc_worker.py"
    return entry if entry.is_file() and all(path.is_file() for path in required) else None


def inspect_runtime(
    settings: Settings, cancelled: Callable[[], bool] | None = None
) -> dict[str, Any]:
    contract = runtime_contract()
    python = resolve_rvc_python(settings)
    entry = resolve_rvc_entry(settings)
    payload: dict[str, Any] = {
        "platform": platform.platform(),
        "python": sys.version,
        "data_root": str(settings.root),
        "rvc_root": settings.rvc_root,
        "rvc_python": str(python) if python else None,
        "ffmpeg": settings.ffmpeg,
        "ffprobe": settings.ffprobe,
        "hardware_backend": settings.hardware_backend,
        "ready": False,
        "rvc_revision": None,
        "doctor": None,
        "components": {},
    }
    if settings.rvc_root and (Path(settings.rvc_root) / ".git").exists():
        revision = run_capture(
            ["git", "-C", settings.rvc_root, "rev-parse", "HEAD"],
            cancelled=cancelled,
        )
        if revision.returncode == 0:
            payload["rvc_revision"] = revision.stdout.strip()
    elif settings.rvc_root:
        revision_marker = Path(settings.rvc_root) / ".voxweave-rvc-revision"
        if revision_marker.is_file():
            payload["rvc_revision"] = revision_marker.read_text(
                encoding="utf-8"
            ).strip()
    if python and entry:
        try:
            payload["doctor"] = _run_json(
                [
                    str(python),
                    "-B",
                    str(entry),
                    "--rvc-root",
                    settings.rvc_root,
                    "doctor",
                ],
                cwd=Path(settings.rvc_root),
                cancelled=cancelled,
            )
            payload["components"]["python_runtime"] = _run_json(
                [str(python), "-B", str(PACKAGE_ROOT / "runtime_worker.py")],
                cwd=entry.parent,
                cancelled=cancelled,
            )
            payload["ready"] = bool(
                payload["doctor"].get("ok")
                and settings.ffmpeg
                and Path(settings.ffmpeg).is_file()
                and settings.ffprobe
                and Path(settings.ffprobe).is_file()
            )
        except (RuntimeErrorDetail, ValueError) as exc:
            payload["error"] = str(exc)
    separation = contract.source_separation
    separation_model = (
        Path(settings.rvc_root) / "assets" / separation.model_file
        if settings.rvc_root
        else None
    )
    payload["components"]["source_separation"] = {
        "backend": separation.backend,
        "model_id": separation.model_id,
        "ready": bool(separation_model and separation_model.is_file()),
        "model_path": str(separation_model) if separation_model else None,
        "model_sha256": sha256_file(separation_model)
        if separation_model and separation_model.is_file()
        else None,
        "source": separation.source,
        "code_license_spdx": separation.code_license_spdx,
        "model_license_spdx": separation.model_license_spdx,
    }
    speaker = contract.speaker_embedding
    speaker_model = Path(settings.wespeaker_model) if settings.wespeaker_model else None
    payload["components"]["speaker_embedding"] = {
        "backend": speaker.backend,
        "ready": bool(speaker_model and speaker_model.is_file()),
        "model_path": str(speaker_model) if speaker_model else None,
        "model_sha256": sha256_file(speaker_model)
        if speaker_model and speaker_model.is_file()
        else None,
        "code_license_spdx": speaker.code_license_spdx,
        "model_license_spdx": speaker.model_license_spdx,
        "source": speaker.source,
        "revision": speaker.revision,
    }
    payload["pinned_rvc_revision"] = contract.rvc_source.revision
    payload["pinned_asset_revision"] = contract.runtime_assets.revision
    payload["rvc_revision_matches_pin"] = (
        payload["rvc_revision"] == contract.rvc_source.revision
    )
    return payload
