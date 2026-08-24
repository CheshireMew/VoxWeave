from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from .config import Settings, SettingsConflictError, resolve_data_root
from .file_lock import InterprocessFileLock


class SettingsFileStore:
    """Own settings JSON decoding, migration, locking and atomic publication."""

    def __init__(self, fallback: Settings) -> None:
        self.fallback = fallback.updated()
        self.path = fallback.config_path
        self.lock_path = self.path.with_suffix(".lock")

    def _decode(self) -> tuple[Settings, bool]:
        if not self.path.is_file():
            return self.fallback.updated(), True
        payload: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        migrated = "model_roots" in payload
        legacy_roots = payload.pop("model_roots", [])
        payload.setdefault("weight_roots", legacy_roots)
        payload.setdefault("index_roots", [])
        payload.setdefault("revision", 0)
        payload["data_root"] = self.fallback.data_root
        settings = Settings(**payload)
        return settings, migrated or payload != settings.payload()

    def load(self, *, create_layout: bool = True) -> Settings:
        settings, _normalization_needed = self._decode()
        if create_layout:
            settings.ensure_layout()
        return settings

    def _write(self, settings: Settings) -> None:
        settings.ensure_layout()
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(settings.payload(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def ensure_persisted(self, active: Settings) -> None:
        with InterprocessFileLock(self.lock_path):
            current, normalization_needed = self._decode()
            if not self.path.is_file():
                current = active.updated()
                normalization_needed = True
            if normalization_needed:
                self._write(current)
            active.replace_with(current)

    def commit(
        self,
        active: Settings,
        *,
        expected_revision: int | None = None,
        **changes: Any,
    ) -> dict[str, Any]:
        with InterprocessFileLock(self.lock_path):
            current, _normalization_needed = self._decode()
            if not self.path.is_file():
                current = active.updated()
            if expected_revision is not None and expected_revision != current.revision:
                raise SettingsConflictError(expected_revision, current.revision)
            candidate = current.updated(**changes)
            current_payload = current.payload()
            changed_fields = sorted(
                name
                for name, value in candidate.payload().items()
                if name != "revision" and value != current_payload[name]
            )
            if changed_fields:
                candidate.revision = current.revision + 1
                self._write(candidate)
            active.replace_with(candidate)
            return {
                "revision": candidate.revision,
                "changed_fields": changed_fields,
                "settings": candidate.payload(),
            }


def _which(name: str) -> str | None:
    value = shutil.which(name)
    return str(Path(value).resolve()) if value else None


def load_settings(*, create: bool = True) -> Settings:
    fallback = Settings(
        data_root=str(resolve_data_root()),
        ffmpeg=_which("ffmpeg"),
        ffprobe=_which("ffprobe"),
    )
    return SettingsFileStore(fallback).load(create_layout=create)
